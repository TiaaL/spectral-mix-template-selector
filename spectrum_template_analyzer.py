#!/usr/bin/env python3
"""Analyze spectral band balance and classify an audio file into templates.

Example:
    python spectrum_template_analyzer.py vocal.wav
    python spectrum_template_analyzer.py vocal.mp3 --sr 44100 --top-db 35
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

# Some packaged librosa/numba combinations fail when numba tries to create a
# cache beside site-packages. Point it at a writable temp cache before import.
os.environ.setdefault("NUMBA_CACHE_DIR", str(Path(tempfile.gettempdir()) / "numba-cache"))

import librosa
import numpy as np
from scipy.signal import find_peaks


# Peakiness in a band is only trusted when the band has real energy. Using a
# *ratio* threshold here was wrong: in heavily body-dominant mixes (e.g. 73%
# lowmid) a real sibilant spike in harsh/sib can sit at 1–2% ratio and still
# be perceptually sharp — the ratio check would silently mask it.
#
# We instead check the band's max level in dB-relative-to-max-spectral-bin.
# Truly empty bands (codec-banded sources) bottom out near -80 dB; real
# content stays well above -60 dB even when its band ratio is tiny.
PEAKINESS_NOISE_FLOOR_DB = -60.0

# A harsh peak at this level is *strong* evidence for B, but on its own it is no
# longer decisive. A single harsh spike used to force B outright, which let an
# unqualified B (1 hit) beat a fully qualified C on body-dominant, presence-
# starved material: de-essing a mix whose real problem is a collapsed top end
# only makes it duller. B now requires independent high-frequency evidence.
STRONG_HARSH_PEAK_DB = 12.0

BANDS = OrderedDict(
    [
        ("sub", (20.0, 80.0)),
        ("low", (80.0, 180.0)),
        ("lowmid", (180.0, 500.0)),
        ("mid", (500.0, 1000.0)),
        ("upper", (1000.0, 4000.0)),
        ("harsh", (4000.0, 8000.0)),
        ("sib", (8000.0, 12000.0)),
        ("air", (12000.0, 20000.0)),
    ]
)


# Template thresholds — most taste/project-dependent part of the script.
CLASSIFICATION_RULES = {
    "template_A": {
        "name": "Muddy / Boxy Vocal",
        "tags": ["厚", "闷", "糊", "鼻", "箱感", "主体偏暗"],
        "minimum_hits": 2,
        # in_a_territory gates every rule so A stays mutually exclusive with
        # C's structural zone.
        "rules": {
            "lowmid_ratio_high": lambda m: m["ratios"]["lowmid"] >= 0.28 and in_a_territory(m),
            "mid_ratio_high": lambda m: m["ratios"]["mid"] >= 0.20 and in_a_territory(m),
            "body_to_presence_high": lambda m: safe_ge(m["body_to_presence"], 1.15) and in_a_territory(m),
        },
        "strong_rules": {
            "very_high_lowmid": lambda m: m["ratios"]["lowmid"] >= 0.34 and in_a_territory(m),
            "strong_body_to_presence": lambda m: safe_ge(m["body_to_presence"], 1.35) and in_a_territory(m),
        },
    },
    "template_C": {
        "name": "Imbalanced / Heavy Low-Mid",
        "tags": ["闷", "糊", "头重脚轻", "缺高频", "不通透"],
        "minimum_hits": 2,
        # in_c_territory (body-dominant + presence-starved + body peakiness)
        # gates every rule, so C only fires on the full structural pattern.
        "rules": {
            "extreme_lowmid": lambda m: m["ratios"]["lowmid"] >= 0.55 and in_c_territory(m),
            "very_high_body_to_presence": lambda m: safe_ge(m["body_to_presence"], 5.0) and in_c_territory(m),
            "band_limited_highs": lambda m: m["ratios"]["upper"] <= 0.06 and m["ratios"]["harsh"] <= 0.005 and in_c_territory(m),
            "body_peak_spiky": lambda m: m["peakiness_upper"] >= C_BODY_PEAK_DB and in_c_territory(m),
        },
        "strong_rules": {
            "mega_lowmid": lambda m: m["ratios"]["lowmid"] >= 0.70 and in_c_territory(m),
            "extreme_body_to_presence": lambda m: safe_ge(m["body_to_presence"], 10.0) and in_c_territory(m),
            "very_spiky_body_peak": lambda m: m["peakiness_upper"] >= C_BODY_PEAK_STRONG_DB and in_c_territory(m),
        },
    },
    "template_B": {
        "name": "Peaky / Harsh Vocal",
        "tags": ["炸", "刺", "硬", "毛", "金属感", "某些字突然冲"],
        "minimum_hits": 2,
        "rules": {
            "upper_ratio_high": lambda m: m["ratios"]["upper"] >= 0.26,
            "harsh_ratio_high": lambda m: m["ratios"]["harsh"] >= 0.16,
            "sib_ratio_high": lambda m: m["ratios"]["sib"] >= 0.12,
            "upper_peak_spiky": lambda m: m["peakiness_upper"] >= 9.0,
            "harsh_peak_spiky": lambda m: m["peakiness_harsh"] >= 9.0,
        },
        "strong_rules": {
            "very_spiky_harsh": lambda m: m["peakiness_harsh"] >= 12.0,
            "very_high_harsh_ratio": lambda m: m["ratios"]["harsh"] >= 0.22,
            "very_high_sib_ratio": lambda m: m["ratios"]["sib"] >= 0.18,
        },
    },
}


def safe_ge(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def safe_le(value: float | None, threshold: float) -> bool:
    return value is not None and value <= threshold


C_BODY_DOMINANT_RATIO = 0.70
C_PRESENCE_STARVED_RATIO = 0.10
C_PRESENCE_COLLAPSED_RATIO = 0.04
C_BODY_PEAK_DB = 9.0
C_BODY_PEAK_STRONG_DB = 12.0

# Below this presence ratio, peak-shape evidence for B is not trusted: there is
# too little high-frequency energy for a peak to mean "harsh". Set at C's
# presence-starved boundary so the two rule sets agree on where the top end is
# considered collapsed rather than harsh.
B_PRESENCE_FLOOR_RATIO = C_PRESENCE_STARVED_RATIO


def in_c_territory(metrics: dict[str, Any]) -> bool:
    """C is the 'head-heavy, presence-starved, peaky body' pattern.

    Normally requires body-dominant + presence-starved + a peaky body. But when
    presence has fully collapsed (≤ C_PRESENCE_COLLAPSED_RATIO), there is no
    upper-band content for peakiness to register on — that's a band-limited /
    severely-muffled source, which is qualitatively *worse* than C, so we let
    it fall into C territory without the peakiness requirement.
    """
    body_heavy = metrics["group_ratios"]["body"] >= C_BODY_DOMINANT_RATIO
    presence_starved = metrics["group_ratios"]["presence"] <= C_PRESENCE_STARVED_RATIO
    presence_collapsed = metrics["group_ratios"]["presence"] <= C_PRESENCE_COLLAPSED_RATIO
    has_body_peak = metrics["peakiness_upper"] >= C_BODY_PEAK_DB
    return body_heavy and presence_starved and (has_body_peak or presence_collapsed)


def in_a_territory(metrics: dict[str, Any]) -> bool:
    """A fires only outside C's structural zone."""
    return not in_c_territory(metrics)


def strip_silence(y: np.ndarray, top_db: float) -> np.ndarray:
    intervals = librosa.effects.split(y, top_db=top_db)
    if len(intervals) == 0:
        return y

    voiced = np.concatenate([y[start:end] for start, end in intervals])
    return voiced if len(voiced) else y


def band_mask(freqs: np.ndarray, low_hz: float, high_hz: float) -> np.ndarray:
    return (freqs >= low_hz) & (freqs < high_hz)


def band_power(power: np.ndarray, freqs: np.ndarray, low_hz: float, high_hz: float) -> float:
    mask = band_mask(freqs, low_hz, high_hz)
    if not np.any(mask):
        return 0.0
    return float(power[mask, :].sum())


def band_peakiness(
    mean_db_spectrum: np.ndarray,
    freqs: np.ndarray,
    low_hz: float,
    high_hz: float,
    prominence_db: float,
    top_n: int = 2,
) -> float:
    mask = band_mask(freqs, low_hz, high_hz)
    band_db = mean_db_spectrum[mask]
    if band_db.size < 3:
        return 0.0

    _, properties = find_peaks(band_db, prominence=prominence_db)
    prominences = properties.get("prominences")
    if prominences is None or len(prominences) == 0:
        return 0.0

    # Average of the top-N prominences — less sensitive to a single spike
    # than max(), still reported in dB so calibrated thresholds keep meaning.
    k = min(top_n, len(prominences))
    top = np.sort(prominences)[-k:]
    return float(np.mean(top))


def band_spectral_flatness(
    mean_power_spectrum: np.ndarray,
    freqs: np.ndarray,
    low_hz: float,
    high_hz: float,
) -> float:
    """某频段内的谱平坦度 = 几何均值 / 算术均值（线性功率谱），值域 0~1。

    用途：区分「AI 分离电音/金属环」和「普通闷糊人声」——这两类用 peakiness
    和频段能量占比都区分不开（汤刚黄昏 vs 乐园几乎一样）。它们的差别在 upper
    频段的“能量结构”上：

      - 电音/金属：upper 是少数窄尖峰主导，能量集中 -> flatness 偏低（接近 0）。
      - 干净/闷糊人声：upper 能量分布宽而平滑 -> flatness 偏高（接近 1）。

    peakiness 抓的是“最尖的几个峰有多突出”，flatness 抓的是“整段能量铺得多平”，
    两者互补：电音的尖峰可能不算特别突出（peakiness 不高），但整段被几根金属环
    霸占（flatness 很低），正好补上 peakiness 的盲区。
    """
    mask = band_mask(freqs, low_hz, high_hz)
    band = mean_power_spectrum[mask]
    # 至少要几根谱线、且必须是正功率，几何均值的 log 才有意义。
    band = band[band > 0.0]
    if band.size < 3:
        # 频段为空或全是 0：没有可分析的结构，返回 1.0（视为“最平坦/无电音特征”），
        # 避免把空频段误判成“能量极度集中的电音”。
        return 1.0
    # 几何均值用 log 域求和再 exp，避免大量小数连乘下溢。
    geo_mean = float(np.exp(np.mean(np.log(band))))
    arith_mean = float(np.mean(band))
    if arith_mean <= 0.0:
        return 1.0
    return geo_mean / arith_mean


def b_evidence_groups(metrics: dict[str, Any]) -> list[str]:
    """Independent high-frequency evidence groups supporting template_B.

    Counting raw rule hits over-counted B: several of its rules read the same
    metric (harsh ratio appears as both a normal and a strong rule), so one
    acoustic fact could produce several "hits". These groups are deliberately
    built from *distinct* kinds of evidence, so two groups means two genuinely
    independent reasons to believe the top end is harsh.
    """
    ratios = metrics["ratios"]
    hf_energy_bands = [
        band
        for band, threshold in (("upper", 0.26), ("harsh", 0.16), ("sib", 0.12))
        if ratios.get(band, 0.0) >= threshold
    ]
    upper_spiky = metrics["peakiness_upper"] >= 9.0
    harsh_spiky = metrics["peakiness_harsh"] >= 9.0

    # Peak shape is only meaningful where there is enough energy to shape. In a
    # presence-starved mix the whole presence region can sit near 3% of total
    # energy; peaks measured there describe a collapsed top end, not harshness,
    # so peak-based evidence is not counted at all below this floor.
    presence_alive = metrics["group_ratios"].get("presence", 0.0) >= B_PRESENCE_FLOOR_RATIO

    groups: list[str] = []
    if hf_energy_bands:
        groups.append("hf_energy")
    if presence_alive and (upper_spiky or harsh_spiky):
        groups.append("hf_peak")
    # Two independent bands carrying excess energy is its own evidence group:
    # broadband HF excess is not the same fact as a single band being hot.
    if len(hf_energy_bands) >= 2:
        groups.append("hf_energy_multiband")
    # Both presence regions spiking, with the harsh spike at strong level.
    if (
        presence_alive
        and upper_spiky
        and harsh_spiky
        and metrics["peakiness_harsh"] >= STRONG_HARSH_PEAK_DB
    ):
        groups.append("dual_peak_strong")
    return groups


def classify(metrics: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}

    for template, config in CLASSIFICATION_RULES.items():
        hits = [
            name
            for name, predicate in config["rules"].items()
            if bool(predicate(metrics))
        ]
        strong_hits = [
            name
            for name, predicate in config["strong_rules"].items()
            if bool(predicate(metrics))
        ]
        results[template] = {
            "name": config["name"],
            "tags": config["tags"],
            "hits": len(hits),
            "hit_rules": hits,
            "strong_hits": len(strong_hits),
            "strong_rules": strong_hits,
            "qualified": len(hits) >= config["minimum_hits"],
        }

    # B's evidence is judged by independent groups rather than by rule count,
    # so a single sibilant spike can no longer masquerade as a broad HF problem.
    b_groups = b_evidence_groups(metrics)
    b_qualifies = len(b_groups) >= 2
    b_is_strong = len(b_groups) >= 3 or "dual_peak_strong" in b_groups

    a_qualified = results["template_A"]["qualified"]
    c_qualified = results["template_C"]["qualified"]

    label: str
    reason: str
    fallback = False
    secondary: list[str] = []

    if c_qualified and not (b_qualifies and b_is_strong):
        # C outranks B unless B has genuinely multi-source evidence: a
        # body-dominant, presence-starved mix needs its structure fixed first,
        # and a lone harsh peak must never overturn it.
        label = "template_C"
        reason = "qualified C structure takes priority over non-strong B evidence"
        if b_qualifies:
            secondary.append("hf_harshness")
    elif c_qualified and b_qualifies and b_is_strong:
        label = "template_B"
        reason = "strong multi-group B evidence competes with C structure"
        secondary.append("body_heavy_structure")
    elif b_qualifies and b_is_strong:
        label = "template_B"
        reason = "strong multi-group B evidence"
        if a_qualified:
            secondary.append("muddy_body")
    elif a_qualified:
        # A plain B alongside A stays a secondary issue: the existing dynamic
        # de-esser / HF guard handles it without a fixed EQ cut on top.
        label = "template_A"
        reason = "qualified A; non-strong B (if any) handled as secondary issue"
        if b_qualifies:
            secondary.append("hf_harshness")
    elif b_qualifies:
        label = "template_B"
        reason = "B is the only qualified template"
    else:
        label = "template_A"
        reason = "no template qualified; falling back to the maintained A chain"
        fallback = True

    if fallback:
        confidence = "low"
    elif results[label].get("strong_rules") or b_is_strong:
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "label": label,
        "label_name": results[label]["name"] if label in results else None,
        "selection_reason": reason,
        "fallback": fallback,
        "confidence": confidence,
        "secondary_issues": secondary,
        "b_evidence_groups": b_groups,
        "template_A": results["template_A"],
        "template_B": results["template_B"],
        "template_C": results["template_C"],
    }


def analyze_audio(
    audio_path: str | Path,
    sr: int = 44100,
    n_fft: int = 4096,
    hop_length: int | None = None,
    top_db: float = 40.0,
    trim: bool = True,
    peak_prominence_db: float = 6.0,
) -> dict[str, Any]:
    path = Path(audio_path).expanduser()
    native_sr = int(librosa.get_samplerate(path))
    y, used_sr = librosa.load(path, sr=sr, mono=True)
    if trim:
        y = strip_silence(y, top_db=top_db)

    # A band-limited source (e.g. 24 kHz native upsampled to 44.1 kHz) has no
    # real content above its own Nyquist. Counting empty high bands in the
    # denominator inflates low-band ratios and breaks calibrated thresholds.
    effective_nyquist = min(used_sr, native_sr) / 2.0
    active_bands: "OrderedDict[str, tuple[float, float]]" = OrderedDict()
    dropped_bands: list[str] = []
    for name, (low_hz, high_hz) in BANDS.items():
        if low_hz >= effective_nyquist:
            dropped_bands.append(name)
            continue
        active_bands[name] = (low_hz, min(high_hz, effective_nyquist))

    if hop_length is None:
        hop_length = n_fft // 4

    stft = librosa.stft(y, n_fft=n_fft, hop_length=hop_length, center=True)
    power = np.abs(stft) ** 2
    freqs = librosa.fft_frequencies(sr=used_sr, n_fft=n_fft)

    energies = {
        name: band_power(power, freqs, low_hz, high_hz)
        for name, (low_hz, high_hz) in active_bands.items()
    }
    for name in dropped_bands:
        energies[name] = 0.0
    total_energy = float(sum(energies.values()))
    ratios = {
        name: (energy / total_energy if total_energy > 0 else 0.0)
        for name, energy in energies.items()
    }

    body_energy = energies["lowmid"] + energies["mid"]
    presence_energy = energies["upper"] + energies["harsh"]
    body_to_presence = (
        body_energy / presence_energy if presence_energy > 0 else None
    )

    group_ratios = {
        "body": ratios["lowmid"] + ratios["mid"],
        "presence": ratios["upper"] + ratios["harsh"],
    }

    mean_power = np.mean(power, axis=1)
    mean_db_spectrum = librosa.power_to_db(mean_power, ref=np.max)
    peakiness_upper = band_peakiness(
        mean_db_spectrum,
        freqs,
        *active_bands.get("upper", BANDS["upper"]),
        prominence_db=peak_prominence_db,
    ) if "upper" in active_bands else 0.0
    peakiness_harsh = band_peakiness(
        mean_db_spectrum,
        freqs,
        *active_bands.get("harsh", BANDS["harsh"]),
        prominence_db=peak_prominence_db,
    ) if "harsh" in active_bands else 0.0
    peakiness_sib = band_peakiness(
        mean_db_spectrum,
        freqs,
        *active_bands.get("sib", BANDS["sib"]),
        prominence_db=peak_prominence_db,
    ) if "sib" in active_bands else 0.0

    # Noise-floor hygiene: ignore peakiness when its band has no real content,
    # otherwise find_peaks invents spikes from quantization/codec residue.
    # See PEAKINESS_NOISE_FLOOR_DB — gated on absolute level, not band ratio,
    # so a narrow sib peak inside a body-dominated mix still registers.
    def _band_is_empty(low_hz: float, high_hz: float) -> bool:
        mask = band_mask(freqs, low_hz, high_hz)
        if not mask.any():
            return True
        return float(mean_db_spectrum[mask].max()) <= PEAKINESS_NOISE_FLOOR_DB

    if "upper" not in active_bands or _band_is_empty(*active_bands.get("upper", BANDS["upper"])):
        peakiness_upper = 0.0
    if "harsh" not in active_bands or _band_is_empty(*active_bands.get("harsh", BANDS["harsh"])):
        peakiness_harsh = 0.0
    if "sib" not in active_bands or _band_is_empty(*active_bands.get("sib", BANDS["sib"])):
        peakiness_sib = 0.0

    # upper 频段谱平坦度：电音/金属环 -> 低（能量被窄尖峰霸占）；干净人声 -> 高。
    # 用线性 mean_power（不是 dB），因为 flatness 定义在线性功率谱上。空频段同样
    # 跳过，返回 1.0（“最平坦/无电音”），避免空频段被误判成电音。
    if "upper" in active_bands and not _band_is_empty(*active_bands.get("upper", BANDS["upper"])):
        flatness_upper = band_spectral_flatness(
            mean_power, freqs, *active_bands.get("upper", BANDS["upper"])
        )
    else:
        flatness_upper = 1.0

    metrics: dict[str, Any] = {
        "audio_path": str(path),
        "sample_rate": used_sr,
        "native_sample_rate": native_sr,
        "effective_nyquist_hz": effective_nyquist,
        "dropped_bands": dropped_bands,
        "n_fft": n_fft,
        "hop_length": hop_length,
        "trim_silence": trim,
        "top_db": top_db,
        "band_energies": energies,
        "ratios": ratios,
        "group_ratios": group_ratios,
        "body_to_presence": body_to_presence,
        "peakiness_upper": peakiness_upper,
        "peakiness_harsh": peakiness_harsh,
        "peakiness_sib": peakiness_sib,
        # upper 谱平坦度（0~1）：低=电音/金属环，高=干净/闷糊人声。
        "flatness_upper": flatness_upper,
    }
    metrics["classification"] = classify(metrics)
    return metrics


def numpy_json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        scalar = value.item()
        if isinstance(scalar, float) and not math.isfinite(scalar):
            return None
        return scalar
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute spectral band ratios, peakiness, and template classification."
    )
    parser.add_argument("audio_path", help="Input wav/mp3 audio file.")
    parser.add_argument("--sr", type=int, default=44100, help="Target sample rate.")
    parser.add_argument("--n-fft", type=int, default=4096, help="STFT FFT size.")
    parser.add_argument(
        "--hop-length",
        type=int,
        default=None,
        help="STFT hop length. Defaults to n_fft / 4.",
    )
    parser.add_argument(
        "--top-db",
        type=float,
        default=40.0,
        help="librosa silence split threshold in dB.",
    )
    parser.add_argument(
        "--no-trim",
        action="store_true",
        help="Disable simple silence removal before analysis.",
    )
    parser.add_argument(
        "--peak-prominence-db",
        type=float,
        default=6.0,
        help="find_peaks prominence threshold in dB.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = analyze_audio(
        audio_path=args.audio_path,
        sr=args.sr,
        n_fft=args.n_fft,
        hop_length=args.hop_length,
        top_db=args.top_db,
        trim=not args.no_trim,
        peak_prominence_db=args.peak_prominence_db,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=numpy_json_default))


if __name__ == "__main__":
    main()
