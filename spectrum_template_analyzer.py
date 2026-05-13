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
        "rules": {
            "lowmid_ratio_high": lambda m: m["ratios"]["lowmid"] >= 0.28,
            "mid_ratio_high": lambda m: m["ratios"]["mid"] >= 0.20,
            "body_to_presence_high": lambda m: safe_ge(m["body_to_presence"], 1.15),
        },
        "strong_rules": {
            "very_high_lowmid": lambda m: m["ratios"]["lowmid"] >= 0.34,
            "strong_body_to_presence": lambda m: safe_ge(m["body_to_presence"], 1.35),
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

    a = results["template_A"]
    b = results["template_B"]
    label = "undetermined"

    # Decisive override: a real harsh spike outranks body-heavy / band-limited
    # evidence — de-essing the spike is always the priority when one exists.
    if metrics["peakiness_harsh"] >= DECISIVE_HARSH_PEAK_DB:
        label = "template_B"
    elif a["qualified"] and not b["qualified"]:
        label = "template_A"
    elif b["qualified"] and not a["qualified"]:
        label = "template_B"
    elif a["qualified"] and b["qualified"]:
        # Method 2: any B strong_hit beats A unless A also has strong_hits.
        if b["strong_hits"] > 0 and a["strong_hits"] == 0:
            label = "template_B"
        elif a["strong_hits"] > 0 and b["strong_hits"] == 0:
            label = "template_A"
        elif a["hits"] != b["hits"]:
            label = "template_A" if a["hits"] > b["hits"] else "template_B"
        elif a["strong_hits"] != b["strong_hits"]:
            label = "template_A" if a["strong_hits"] > b["strong_hits"] else "template_B"

    return {
        "label": label,
        "label_name": results[label]["name"] if label in results else None,
        "template_A": results["template_A"],
        "template_B": results["template_B"],
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
    y, used_sr = librosa.load(path, sr=sr, mono=True)
    if trim:
        y = strip_silence(y, top_db=top_db)

    if hop_length is None:
        hop_length = n_fft // 4

    stft = librosa.stft(y, n_fft=n_fft, hop_length=hop_length, center=True)
    power = np.abs(stft) ** 2
    freqs = librosa.fft_frequencies(sr=used_sr, n_fft=n_fft)

    energies = {
        name: band_power(power, freqs, low_hz, high_hz)
        for name, (low_hz, high_hz) in BANDS.items()
    }
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
        *BANDS["upper"],
        prominence_db=peak_prominence_db,
    )
    peakiness_harsh = band_peakiness(
        mean_db_spectrum,
        freqs,
        *BANDS["harsh"],
        prominence_db=peak_prominence_db,
    )

    # Noise-floor hygiene: ignore peakiness when its band has no real content,
    # otherwise find_peaks invents spikes from quantization/codec residue.
    if ratios["upper"] < PEAKINESS_NOISE_FLOOR_RATIO:
        peakiness_upper = 0.0
    if ratios["harsh"] < PEAKINESS_NOISE_FLOOR_RATIO:
        peakiness_harsh = 0.0

    metrics: dict[str, Any] = {
        "audio_path": str(path),
        "sample_rate": used_sr,
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
