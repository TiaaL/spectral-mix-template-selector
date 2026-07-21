#!/usr/bin/env python3
"""Capture current classifier labels for the online regression samples.

Runs the live analyzer over each sample and records label + the metrics the
rules read. This snapshot is the reference point for the A/B/C rule rework:
any rule change must be justified against a measured diff, not an estimate.

Usage:
    python3 tests/capture_baseline.py            # write baseline.json
    python3 tests/capture_baseline.py --check    # compare against baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spectrum_template_analyzer as S  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "baseline.json"

MIX_RUN = Path(
    "/Users/xy/Desktop/1/code/music/music_auto_mix1/run_outputs"
    "/feishu_latest_1_2_1_quality_repair_results"
)

# The misclassified file that motivated the rework. Expected to flip A->C
# by design; every other sample is expected to hold.
EXTRA_SAMPLES = {
    "badcase_codec_out_8a43de53": Path(
        "/Users/xy/Downloads/1784269734_123857054037_codec_out_8a43de53_0.wav"
    ),
}

# Metrics the classification rules actually read.
RULE_METRICS = (
    "peakiness_upper",
    "peakiness_harsh",
    "body_to_presence",
)


def sample_paths() -> dict[str, Path]:
    samples: dict[str, Path] = {}
    for analysis_file in sorted(MIX_RUN.glob("row*_analysis.json")):
        row = analysis_file.name.split("_")[0]
        audio = Path(json.loads(analysis_file.read_text())["audio_path"])
        if audio.exists():
            samples[row] = audio
    for name, path in EXTRA_SAMPLES.items():
        if path.exists():
            samples[name] = path
    return samples


def summarize(audio: Path) -> dict:
    result = S.analyze_audio(audio)
    classification = result["classification"]
    per_template = {
        template: {
            "hits": classification[template]["hits"],
            "hit_rules": classification[template]["hit_rules"],
            "strong_rules": classification[template]["strong_rules"],
            "qualified": classification[template]["qualified"],
        }
        for template in ("template_A", "template_B", "template_C")
    }
    return {
        "label": classification["label"],
        "templates": per_template,
        "metrics": {
            **{k: round(float(result[k]), 4) for k in RULE_METRICS},
            "ratios": {k: round(float(v), 4) for k, v in result["ratios"].items()},
            "group_ratios": {
                k: round(float(v), 4) for k, v in result["group_ratios"].items()
            },
        },
    }


def capture() -> dict:
    return {name: summarize(path) for name, path in sample_paths().items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="diff against saved baseline")
    args = parser.parse_args()

    current = capture()

    if not args.check:
        BASELINE.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
        for name, data in current.items():
            print(f"{name:32s} {data['label']}")
        print(f"\nwrote {BASELINE} ({len(current)} samples)")
        return 0

    if not BASELINE.exists():
        print("no baseline.json; run without --check first", file=sys.stderr)
        return 2

    saved = json.loads(BASELINE.read_text())
    changed = []
    for name in sorted(set(saved) | set(current)):
        before = saved.get(name, {}).get("label", "<absent>")
        after = current.get(name, {}).get("label", "<absent>")
        mark = " " if before == after else "*"
        if before != after:
            changed.append((name, before, after))
        print(f"{mark} {name:32s} {before} -> {after}")

    print()
    if changed:
        print(f"{len(changed)} label change(s):")
        for name, before, after in changed:
            print(f"  {name}: {before} -> {after}")
    else:
        print("all labels unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
