#!/usr/bin/env python3
"""Batch pair downloaded dry/accompaniment files, run auto template mix, and write sheet rows."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from render_pipeline import (
    ACCOMP_ROLE,
    DEFAULT_DOWNLOAD_ROOT,
    DEFAULT_OUTPUT_DIR,
    DRY_ROLE,
    RenderCase,
    default_python,
    load_json,
    render_case,
    sheet_row_from_summary,
)

ROOT = Path(__file__).resolve().parent.parent


def role_from_path(path: str) -> str:
    parts = Path(path).parts
    return DRY_ROLE if DRY_ROLE in parts else ACCOMP_ROLE if ACCOMP_ROLE in parts else ""


def existing_path(base_dir: Path, rel_path: str) -> Path:
    candidate = base_dir / rel_path
    if candidate.exists():
        return candidate
    fixed = rel_path.replace("骞插０", DRY_ROLE).replace("浼村", ACCOMP_ROLE)
    return base_dir / fixed


def build_pairs(download_root: Path) -> tuple[list[RenderCase], list[dict[str, Any]]]:
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

    pairs: list[RenderCase] = []
    for (row, case_name, extra_name), roles in sorted(grouped.items()):
        dry = roles.get(DRY_ROLE)
        accomp = roles.get(ACCOMP_ROLE)
        if dry and accomp and dry.exists() and accomp.exists():
            pairs.append(RenderCase(row, case_name, extra_name, dry, accomp))
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
    parser.add_argument("--download-root", type=Path, default=DEFAULT_DOWNLOAD_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--python", default=default_python())
    parser.add_argument("--auto-mix-script", type=Path, default=ROOT / "scripts" / "auto_template_mix.py")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--row", type=int, action="append", dest="rows", help="Only render the selected Feishu row number. Can be repeated.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--with-volume-automation", action="store_true")
    args = parser.parse_args()

    pairs, skipped = build_pairs(args.download_root)
    if args.rows:
        selected_rows = set(args.rows)
        pairs = [pair for pair in pairs if pair.row in selected_rows]
    if args.limit is not None:
        pairs = pairs[: args.limit]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "skipped_pairs.json").write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8")
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    print(f"Found {len(pairs)} pair(s), skipped {len(skipped)} incomplete record(s).")
    for index, pair in enumerate(pairs, start=1):
        try:
            summary = render_case(
                pair,
                download_root=args.download_root,
                output_dir=args.output_dir,
                python_exe=args.python,
                auto_mix_script=args.auto_mix_script,
                with_volume_automation=args.with_volume_automation,
                force=args.force,
            )
            row = sheet_row_from_summary(pair, summary)
            rows.append(row)
            print(
                f"[{index:02d}/{len(pairs)}] row {pair.row}: "
                f"{pair.case_name} / {pair.extra_name} -> {row['模版']} -> {row['混音结果']}"
            )
        except Exception as exc:  # noqa: BLE001 - keep batch moving and report all failures.
            errors.append({"row": str(pair.row), "case_name": pair.case_name, "extra_name": pair.extra_name, "error": str(exc)})
            print(f"[{index:02d}/{len(pairs)}] ERROR row {pair.row}: {exc}", file=sys.stderr)

    output_suffix = ""
    if args.rows:
        output_suffix = "_" + "_".join(f"row_{row:03d}" for row in sorted(set(args.rows)))
    write_rows(args.output_dir / f"feishu_sheet_rows{output_suffix}.csv", rows)
    write_tsv(args.output_dir / f"feishu_sheet_rows{output_suffix}.tsv", rows)
    (args.output_dir / f"batch_errors{output_suffix}.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "errors": len(errors), "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
