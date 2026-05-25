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


# Peakiness in a band is only trusted when the band itself has real energy.
# Below this ratio it's noise-floor structure (typical for codec-banded files).
PEAKINESS_NOISE_FLOOR_RATIO = 0.02

# A harsh peak this prominent forces template_B regardless of body indicators —
# "de-ess before de-mud" is the perceptual priority.
DECISIVE_HARSH_PEAK_DB = 12.0

# Treat vocals with little energy below ~200Hz but a crowded 180Hz-1kHz body as
# a separate problem. They are not truly "thick"; they are hollow/boxy and use
# template_B in the downstream service.
SPARSE_LOW_FOUNDATION_RATIO = 0.06
HOLLOW_BOXY_BODY_RATIO = 0.75
HOLLOW_BOXY_LOWMID_RATIO = 0.35


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
        # C (extreme zone) and B (hollow-boxy zone).
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
        "rules": {
            "extreme_lowmid": lambda m: m["ratios"]["lowmid"] >= 0.55,
            "very_high_body_to_presence": lambda m: safe_ge(m["body_to_presence"], 5.0),
            # Combined: a band-limited top end is one observation, not two.
            "band_limited_highs": lambda m: m["ratios"]["upper"] <= 0.06 and m["ratios"]["harsh"] <= 0.005,
        },
        "strong_rules": {
            "mega_lowmid": lambda m: m["ratios"]["lowmid"] >= 0.70,
            "extreme_body_to_presence": lambda m: safe_ge(m["body_to_presence"], 10.0),
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
            "hollow_boxy_body_without_lows": lambda m: is_hollow_boxy_body_without_lows(m),
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


def low_foundation_ratio(metrics: dict[str, Any]) -> float:
    return metrics["ratios"]["sub"] + metrics["ratios"]["low"]


def is_hollow_boxy_body_without_lows(metrics: dict[str, Any]) -> bool:
    return (
        low_foundation_ratio(metrics) <= SPARSE_LOW_FOUNDATION_RATIO
        and metrics["group_ratios"]["body"] >= HOLLOW_BOXY_BODY_RATIO
        and metrics["ratios"]["lowmid"] >= HOLLOW_BOXY_LOWMID_RATIO
    )


def in_a_territory(metrics: dict[str, Any]) -> bool:
    """A fires only outside C's extreme zone and outside B's hollow-boxy zone.

    Without this gate A's loose thresholds would steal samples that structurally
    belong to C (extreme lowmid / body) or B (hollow boxy without lows).
    """
    if metrics["ratios"]["lowmid"] >= 0.55:
        return False
    if metrics["body_to_presence"] is not None and metrics["body_to_presence"] >= 5.0:
        return False
    if is_hollow_boxy_body_without_lows(metrics):
        return False
    return True


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

    # Decisive override: a real harsh spike outranks every other signal —
    # de-essing the spike is always the priority when one exists.
    if metrics["peakiness_harsh"] >= DECISIVE_HARSH_PEAK_DB:
        label = "template_B"
    else:
        # Equal-priority three-way selection.
        # 1. If exactly one template has any strong_hits, that "smoking gun"
        #    template wins.
        # 2. Otherwise rank by (hits, strong_hits); ties break A > B > C.
        candidates = ["template_A", "template_B", "template_C"]
        with_strong = [t for t in candidates if results[t]["strong_hits"] > 0]
        if len(with_strong) == 1:
            label = with_strong[0]
        else:
            label = max(
                candidates,
                key=lambda t: (
                    results[t]["hits"],
                    results[t]["strong_hits"],
                    -candidates.index(t),
                ),
            )

    return {
        "label": label,
        "label_name": results[label]["name"] if label in results else None,
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

    # Noise-floor hygiene: ignore peakiness when its band has no real content,
    # otherwise find_peaks invents spikes from quantization/codec residue.
    if ratios["upper"] < PEAKINESS_NOISE_FLOOR_RATIO:
        peakiness_upper = 0.0
    if ratios["harsh"] < PEAKINESS_NOISE_FLOOR_RATIO:
        peakiness_harsh = 0.0

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
