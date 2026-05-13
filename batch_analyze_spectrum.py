#!/usr/bin/env python3
"""Batch run spectrum_template_analyzer over audio files.

Examples:
    python3 batch_analyze_spectrum.py downloads/reconstruct_audio
    python3 batch_analyze_spectrum.py audio1.wav audio2.mp3 --output-csv results.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from spectrum_template_analyzer import BANDS, analyze_audio, numpy_json_default


DEFAULT_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".aiff", ".aif"}


def collect_audio_files(paths: list[Path], recursive: bool, extensions: set[str]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        path = raw_path.expanduser()
        if path.is_file() and path.suffix.lower() in extensions:
            files.append(path)
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.iterdir()
            files.extend(
                candidate
                for candidate in iterator
                if candidate.is_file() and candidate.suffix.lower() in extensions
            )
    return sorted(dict.fromkeys(files))


def default_output_paths(input_paths: list[Path]) -> tuple[Path, Path]:
    if len(input_paths) == 1 and input_paths[0].expanduser().is_dir():
        output_dir = input_paths[0].expanduser()
    else:
        output_dir = Path.cwd()
    return (
        output_dir / "spectrum_classification_results.csv",
        output_dir / "spectrum_classification_summary.json",
    )


def ratio_fields(result: dict[str, Any]) -> dict[str, float]:
    return {f"{name}_ratio": result["ratios"][name] for name in BANDS}


def template_fields(classification: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in classification.items():
        if not key.startswith("template_") or not isinstance(value, dict):
            continue
        fields[f"{key}_hits"] = value.get("hits", 0)
        fields[f"{key}_strong_hits"] = value.get("strong_hits", 0)
        fields[f"{key}_rules"] = ";".join(value.get("hit_rules", []))
        fields[f"{key}_strong_rules"] = ";".join(value.get("strong_rules", []))
    return fields


def analyze_file(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    result = analyze_audio(
        audio_path=path,
        sr=args.sr,
        n_fft=args.n_fft,
        hop_length=args.hop_length,
        top_db=args.top_db,
        trim=not args.no_trim,
        peak_prominence_db=args.peak_prominence_db,
    )
    classification = result["classification"]
    row: dict[str, Any] = {
        "filename": path.name,
        "path": str(path),
        "classification": classification["label"],
        "label_name": classification.get("label_name"),
        "body_to_presence": result["body_to_presence"],
        "peakiness_upper": result["peakiness_upper"],
        "peakiness_harsh": result["peakiness_harsh"],
    }
    row.update(ratio_fields(result))
    row.update(template_fields(classification))
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2, default=numpy_json_default)
        file.write("\n")


def finite_number(value: Any) -> float:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return 0.0


def build_summary(rows: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    label_counts = Counter(row["classification"] for row in rows)
    harsh_top = sorted(rows, key=lambda r: finite_number(r["peakiness_harsh"]), reverse=True)[:10]
    upper_top = sorted(rows, key=lambda r: finite_number(r["peakiness_upper"]), reverse=True)[:10]

    return {
        "total_files": len(rows) + len(errors),
        "analyzed": len(rows),
        "failed": len(errors),
        "label_counts": dict(label_counts),
        "failed_files": errors,
        "top_harsh_peakiness": [
            {
                "filename": row["filename"],
                "classification": row["classification"],
                "peakiness_harsh": row["peakiness_harsh"],
            }
            for row in harsh_top
        ],
        "top_upper_peakiness": [
            {
                "filename": row["filename"],
                "classification": row["classification"],
                "peakiness_upper": row["peakiness_upper"],
            }
            for row in upper_top
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch analyze audio files and select spectrum-based mix templates."
    )
    parser.add_argument("paths", nargs="+", type=Path, help="Audio files or directories.")
    parser.add_argument("--recursive", action="store_true", help="Scan directories recursively.")
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(DEFAULT_AUDIO_EXTENSIONS)),
        help="Comma-separated audio extensions to scan.",
    )
    parser.add_argument("--output-csv", type=Path, default=None, help="CSV output path.")
    parser.add_argument("--summary-json", type=Path, default=None, help="Summary JSON output path.")
    parser.add_argument("--limit", type=int, default=None, help="Analyze only the first N files.")
    parser.add_argument("--sr", type=int, default=44100, help="Target sample rate.")
    parser.add_argument("--n-fft", type=int, default=4096, help="STFT FFT size.")
    parser.add_argument("--hop-length", type=int, default=None, help="STFT hop length.")
    parser.add_argument("--top-db", type=float, default=40.0, help="Silence split threshold in dB.")
    parser.add_argument("--no-trim", action="store_true", help="Disable silence trimming.")
    parser.add_argument(
        "--peak-prominence-db",
        type=float,
        default=6.0,
        help="find_peaks prominence threshold in dB.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    extensions = {
        ext if ext.startswith(".") else f".{ext}"
        for ext in (item.strip().lower() for item in args.extensions.split(","))
        if ext
    }
    files = collect_audio_files(args.paths, recursive=args.recursive, extensions=extensions)
    if args.limit is not None:
        files = files[: args.limit]

    default_csv, default_summary = default_output_paths(args.paths)
    output_csv = (args.output_csv or default_csv).expanduser()
    summary_json = (args.summary_json or default_summary).expanduser()

    print(f"Found {len(files)} audio file(s).")
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for index, path in enumerate(files, start=1):
        try:
            row = analyze_file(path, args)
        except Exception as exc:  # noqa: BLE001 - keep batch reports complete.
            errors.append({"filename": path.name, "path": str(path), "error": str(exc)})
            print(f"[{index:02d}/{len(files)}] ERROR {path.name}: {exc}")
            continue

        rows.append(row)
        print(
            f"[{index:02d}/{len(files)}] {path.name} -> {row['classification']} "
            f"body/pres={finite_number(row['body_to_presence']):.3f} "
            f"upper_peak={finite_number(row['peakiness_upper']):.2f} "
            f"harsh_peak={finite_number(row['peakiness_harsh']):.2f}"
        )

    if rows:
        write_csv(output_csv, rows)
    summary = build_summary(rows, errors)
    write_summary(summary_json, summary)

    print(f"Summary: {summary['label_counts']}")
    print(f"Wrote CSV: {output_csv}")
    print(f"Wrote summary: {summary_json}")


if __name__ == "__main__":
    main()
