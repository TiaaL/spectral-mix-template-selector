#!/usr/bin/env python3
"""Run spectrum_template_analyzer over a directory and summarize results."""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))
from spectrum_template_analyzer import analyze_audio  # noqa: E402

AUDIO_DIR = Path("/Users/xy/Desktop/code/claude/music/spectrum/downloads/reconstruct_audio")
OUT_CSV = Path(__file__).parent / "batch_results.csv"


def main() -> None:
    wavs = sorted(p for p in AUDIO_DIR.iterdir() if p.suffix.lower() in {".wav", ".mp3"})
    print(f"Found {len(wavs)} audio files\n")

    rows = []
    label_counts: Counter[str] = Counter()

    for wav in wavs:
        try:
            r = analyze_audio(wav)
        except Exception as e:
            print(f"[ERR] {wav.name}: {e}")
            continue

        cls = r["classification"]
        label = cls["label"]
        label_counts[label] += 1
        ratios = r["ratios"]
        a_hits = cls["template_A"]["hits"]
        b_hits = cls["template_B"]["hits"]
        c_hits = cls["template_C"]["hits"]
        a_strong = cls["template_A"]["strong_hits"]
        b_strong = cls["template_B"]["strong_hits"]
        c_strong = cls["template_C"]["strong_hits"]

        rows.append({
            "file": wav.name,
            "label": label,
            "A_hits": a_hits,
            "A_strong": a_strong,
            "B_hits": b_hits,
            "B_strong": b_strong,
            "C_hits": c_hits,
            "C_strong": c_strong,
            "lowmid": ratios["lowmid"],
            "mid": ratios["mid"],
            "upper": ratios["upper"],
            "harsh": ratios["harsh"],
            "sib": ratios["sib"],
            "air": ratios["air"],
            "body_to_presence": r["body_to_presence"] if r["body_to_presence"] is not None else float("nan"),
            "peakiness_upper": r["peakiness_upper"],
            "peakiness_harsh": r["peakiness_harsh"],
        })

        print(f"{wav.name[:40]:40s} {label:14s} A={a_hits}(s{a_strong}) B={b_hits}(s{b_strong}) C={c_hits}(s{c_strong}) "
              f"lm={ratios['lowmid']:.2f} mid={ratios['mid']:.2f} up={ratios['upper']:.2f} "
              f"hsh={ratios['harsh']:.2f} sib={ratios['sib']:.2f} "
              f"b/p={r['body_to_presence']:.2f} "
              f"pkU={r['peakiness_upper']:.1f} pkH={r['peakiness_harsh']:.1f}")

    print("\n=== Label distribution ===")
    for k, v in label_counts.most_common():
        print(f"  {k:14s} {v:3d}  ({100*v/len(rows):.1f}%)")

    if rows:
        with OUT_CSV.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV written to {OUT_CSV}")


if __name__ == "__main__":
    main()
