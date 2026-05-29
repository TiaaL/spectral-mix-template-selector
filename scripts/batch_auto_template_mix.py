#!/usr/bin/env python3
"""Batch pair downloaded dry/accompaniment files, run auto template mix, and write sheet rows."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MUSIC_ROOT = ROOT.parent / "music_auto_mix1" / "music_auto_mix1"
DRY_ROLE = "干声"
ACCOMP_ROLE = "伴奏"


@dataclass
class Pair:
    row: int
    case_name: str
    extra_name: str
    dry_path: Path
    accomp_path: Path


def sanitize(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value.strip())
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ._") or "unnamed"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def role_from_path(path: str) -> str:
    parts = Path(path).parts
    return DRY_ROLE if DRY_ROLE in parts else ACCOMP_ROLE if ACCOMP_ROLE in parts else ""


def existing_path(base_dir: Path, rel_path: str) -> Path:
    candidate = base_dir / rel_path
    if candidate.exists():
        return candidate
    fixed = rel_path.replace("骞插０", DRY_ROLE).replace("浼村", ACCOMP_ROLE)
    return base_dir / fixed


def build_pairs(download_root: Path) -> tuple[list[Pair], list[dict[str, Any]]]:
    manifest_path = download_root / "download_manifest.json"
    base_dir = download_root.parent.parent if download_root.name == "feishu_long_audio_screened" else Path.cwd()
    manifest = load_json(manifest_path)
    grouped: dict[tuple[int, str, str], dict[str, Path]] = {}
    skipped: list[dict[str, Any]] = []

    for item in manifest:
        if not str(item.get("status", "")).startswith("ok"):
            continue
        path = existing_path(base_dir, item["path"])
        role = role_from_path(str(path))
        key = (int(item["row"]), str(item["case_name"]), str(item["extra_name"]))
        grouped.setdefault(key, {})[role] = path

    pairs: list[Pair] = []
    for (row, case_name, extra_name), roles in sorted(grouped.items()):
        dry = roles.get(DRY_ROLE)
        accomp = roles.get(ACCOMP_ROLE)
        if dry and accomp and dry.exists() and accomp.exists():
            pairs.append(Pair(row, case_name, extra_name, dry, accomp))
        else:
            skipped.append(
                {
                    "row": row,
                    "case_name": case_name,
                    "extra_name": extra_name,
                    "has_dry": bool(dry and dry.exists()),
                    "has_accomp": bool(accomp and accomp.exists()),
                }
            )
    return pairs, skipped


def compact_rules(classification: dict[str, Any], label: str) -> str:
    template = classification.get(label) or {}
    rules = list(template.get("hit_rules") or [])
    strong = list(template.get("strong_rules") or [])
    return ", ".join([*rules, *[f"strong:{name}" for name in strong]]) or "no explicit rule hits"


def processing_note(analysis: dict[str, Any], selected_template: str) -> str:
    cls = analysis["classification"]
    label = cls["label"]
    ratios = analysis["ratios"]
    body_to_presence = analysis.get("body_to_presence")
    rules = compact_rules(cls, label)
    metrics = (
        f"lowmid={ratios.get('lowmid', 0):.3f}, mid={ratios.get('mid', 0):.3f}, "
        f"upper={ratios.get('upper', 0):.3f}, harsh={ratios.get('harsh', 0):.3f}, "
        f"body/pres={(body_to_presence if body_to_presence is not None else 0):.3f}, "
        f"upper_peak={analysis.get('peakiness_upper', 0):.2f}dB, "
        f"harsh_peak={analysis.get('peakiness_harsh', 0):.2f}dB, "
        f"sib_peak={analysis.get('peakiness_sib', 0):.2f}dB"
    )
    chains = {
        "template_a": "C1 Gate -> template_a Pro-Q3 去浑浊/箱感 -> C1 Comp -> Sibilance -> Vocal Group FX；伴奏/总线使用 A/B 模板 EQ、GW MixCentric、L2",
        "template_b": "RBass -> F6-RTA 动态修高频/刺耳点 -> C1 Comp -> Sibilance -> L1 -> Vocal Group FX；伴奏/总线使用 A/B 模板 EQ、GW MixCentric、L2",
        "template_c": "template_c Pro-Q3 调整低中频/存在感 -> Vocal Rider -> C1 Comp -> OneKnob Brighter -> Vocal Group FX；伴奏/总线使用 C 模板 EQ、GW MixCentric、L2",
        "template_d": "回退当前默认 full_fx_mix 链路",
    }
    return f"特征: {metrics}; 命中: {rules}; 处理: {chains.get(selected_template, selected_template)}"


def run_mix(
    python_exe: str,
    auto_mix_script: Path,
    pair: Pair,
    output_dir: Path,
    with_volume_automation: bool,
    force: bool,
) -> dict[str, Any]:
    label = f"row_{pair.row:03d}_{sanitize(pair.case_name + pair.extra_name)}"
    item_dir = output_dir / label
    item_dir.mkdir(parents=True, exist_ok=True)
    output_wav = item_dir / f"{label}_mix.wav"
    summary_path = item_dir / "summary.json"
    render_vocal = prepare_render_vocal(pair.dry_path, pair.accomp_path, item_dir, force)

    if output_wav.exists() and summary_path.exists() and not force:
        return load_json(summary_path)

    cmd = [
        python_exe,
        str(auto_mix_script),
        str(render_vocal),
        str(pair.accomp_path),
        str(output_wav),
        "--report-dir",
        str(item_dir),
    ]
    if with_volume_automation:
        cmd.append("--with-volume-automation")

    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"mix failed for row {pair.row}: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return load_json(summary_path)


def audio_sample_rate(path: Path) -> int:
    cmd = [
        str(tool_exe("ffprobe")),
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    return int(proc.stdout.strip())


def prepare_render_vocal(dry_path: Path, accomp_path: Path, item_dir: Path, force: bool) -> Path:
    dry_sr = audio_sample_rate(dry_path)
    accomp_sr = audio_sample_rate(accomp_path)
    if dry_sr == accomp_sr:
        return dry_path

    aligned = item_dir / f"{dry_path.stem}_sr{accomp_sr}.wav"
    if aligned.exists() and not force:
        return aligned

    cmd = [
        str(tool_exe("ffmpeg")),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(dry_path),
        "-ar",
        str(accomp_sr),
        "-ac",
        "1",
        str(aligned),
    ]
    proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg resample failed for {dry_path}: {proc.stderr.strip()}")
    return aligned


def tool_exe(name: str) -> Path:
    candidates = [
        DEFAULT_MUSIC_ROOT / ".tools" / "msys64" / "ucrt64" / "bin" / f"{name}.exe",
        DEFAULT_MUSIC_ROOT / ".tools" / "msys64" / "usr" / "bin" / f"{name}.exe",
        Path(f"{name}.exe"),
        Path(name),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["row", "名称", "B列", "干声", "背景音", "模版", "处理说明", "混音结果", "analysis_json", "plan_json", "summary_json"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["名称", "B列", "干声", "背景音", "模版", "处理说明", "混音结果"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, dialect="excel-tab")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row[column] for column in columns})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run batch template auto-mix and generate Feishu sheet rows.")
    parser.add_argument("--download-root", type=Path, default=DEFAULT_MUSIC_ROOT / "downloads" / "feishu_long_audio_screened")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_MUSIC_ROOT / "calibration_outputs" / "feishu_long_audio_screened_auto_mix")
    parser.add_argument("--python", default=str(ROOT / ".venv" / "Scripts" / "python.exe"))
    parser.add_argument("--auto-mix-script", type=Path, default=ROOT / "scripts" / "auto_template_mix.py")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--with-volume-automation", action="store_true")
    args = parser.parse_args()

    pairs, skipped = build_pairs(args.download_root)
    if args.limit is not None:
        pairs = pairs[: args.limit]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "skipped_pairs.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8")
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    print(f"Found {len(pairs)} pair(s), skipped {len(skipped)} incomplete record(s).")
    for index, pair in enumerate(pairs, start=1):
        try:
            summary = run_mix(args.python, args.auto_mix_script, pair, args.output_dir, args.with_volume_automation, args.force)
            analysis = load_json(Path(summary["analysis_json"]))
            selected_template = str(summary.get("selected_template") or "")
            output_wav = str(Path(summary["output_wav"]).resolve(strict=False))
            row = {
                "row": pair.row,
                "名称": pair.case_name,
                "B列": pair.extra_name,
                "干声": str(pair.dry_path.resolve(strict=False)),
                "背景音": str(pair.accomp_path.resolve(strict=False)),
                "模版": str(summary.get("classification_label") or ""),
                "处理说明": processing_note(analysis, selected_template),
                "混音结果": output_wav,
                "analysis_json": str(Path(summary["analysis_json"]).resolve(strict=False)),
                "plan_json": str(Path(summary["resolved_mix_plan"]).resolve(strict=False)),
                "summary_json": str((Path(summary["analysis_json"]).parent / "summary.json").resolve(strict=False)),
            }
            rows.append(row)
            print(f"[{index:02d}/{len(pairs)}] row {pair.row}: {pair.case_name} / {pair.extra_name} -> {row['模版']} -> {output_wav}")
        except Exception as exc:  # noqa: BLE001 - keep batch moving and report all failures.
            errors.append({"row": str(pair.row), "case_name": pair.case_name, "extra_name": pair.extra_name, "error": str(exc)})
            print(f"[{index:02d}/{len(pairs)}] ERROR row {pair.row}: {exc}", file=sys.stderr)

    write_rows(args.output_dir / "feishu_sheet_rows.csv", rows)
    write_tsv(args.output_dir / "feishu_sheet_rows.tsv", rows)
    (args.output_dir / "batch_errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "errors": len(errors), "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
