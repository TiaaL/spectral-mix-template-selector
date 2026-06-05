#!/usr/bin/env python3
"""Unified case-render pipeline for analyzer + renderer orchestration.

This module is intentionally thin around the existing projects: the spectrum
analyzer still lives here, music_auto_mix1 still renders audio, and DelayVerb
stays an external dependency. The goal is to keep path resolution, reference
resolution, render invocation, and sheet-row formatting in one place.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MUSIC_ROOT = ROOT.parent / "music_auto_mix1" / "music_auto_mix1"
DEFAULT_OUTPUT_DIR = DEFAULT_MUSIC_ROOT / "calibration_outputs" / "feishu_long_audio_screened_auto_mix"
DEFAULT_DOWNLOAD_ROOT = DEFAULT_MUSIC_ROOT / "downloads" / "feishu_long_audio_screened"
DRY_ROLE = "干声"
ACCOMP_ROLE = "伴奏"
REFERENCE_FULL_MIX_DIR = "原曲"
REFERENCE_VOCAL_DIR = "原曲人声"
REFERENCE_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a"}


@dataclass(frozen=True)
class RenderCase:
    row: int | None
    case_name: str
    extra_name: str
    dry_path: Path
    accomp_path: Path


@dataclass(frozen=True)
class ReferenceFiles:
    full_mix: Path | None
    vocal: Path | None
    accomp: Path | None

    @property
    def complete(self) -> bool:
        return bool(self.full_mix and self.vocal and self.accomp)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "full_mix": str(self.full_mix) if self.full_mix else None,
            "vocal": str(self.vocal) if self.vocal else None,
            "accomp": str(self.accomp) if self.accomp else None,
        }


def default_python() -> str:
    candidates = [
        ROOT / "python" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sanitize(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value.strip())
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ._") or "unnamed"


def normalize_token(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value).lower()


def song_token(extra_name: str) -> str:
    value = extra_name.strip()
    if "-" in value:
        value = value.rsplit("-", 1)[-1]
    value = value.replace("中文歌曲", "")
    return normalize_token(value)


def find_reference_audio(folder: Path, token: str) -> Path | None:
    if not folder.exists() or not token:
        return None
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in REFERENCE_EXTENSIONS and token in normalize_token(path.stem):
            return path
    return None


def resolve_reference_files(case: RenderCase, download_root: Path) -> ReferenceFiles:
    token = song_token(case.extra_name)
    return ReferenceFiles(
        full_mix=find_reference_audio(download_root / REFERENCE_FULL_MIX_DIR, token),
        vocal=find_reference_audio(download_root / REFERENCE_VOCAL_DIR, token),
        accomp=case.accomp_path if case.accomp_path.exists() else None,
    )


def tool_exe(name: str, music_root: Path = DEFAULT_MUSIC_ROOT) -> Path:
    candidates = [
        music_root / ".tools" / "msys64" / "ucrt64" / "bin" / f"{name}.exe",
        music_root / ".tools" / "msys64" / "usr" / "bin" / f"{name}.exe",
        Path(f"{name}.exe"),
        Path(name),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def audio_sample_rate(path: Path, music_root: Path = DEFAULT_MUSIC_ROOT) -> int:
    cmd = [
        str(tool_exe("ffprobe", music_root)),
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


def prepare_render_vocal(dry_path: Path, accomp_path: Path, item_dir: Path, force: bool, music_root: Path) -> Path:
    dry_sr = audio_sample_rate(dry_path, music_root)
    accomp_sr = audio_sample_rate(accomp_path, music_root)
    if dry_sr == accomp_sr:
        return dry_path

    aligned = item_dir / f"{dry_path.stem}_sr{accomp_sr}.wav"
    if aligned.exists() and not force:
        return aligned

    cmd = [
        str(tool_exe("ffmpeg", music_root)),
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


def case_label(case: RenderCase) -> str:
    row_prefix = f"row_{case.row:03d}_" if case.row is not None else ""
    name = sanitize(f"{case.case_name}{case.extra_name}" or case.dry_path.stem)
    return f"{row_prefix}{name}"


def build_render_command(
    python_exe: str,
    auto_mix_script: Path,
    render_vocal: Path,
    accomp_path: Path,
    output_wav: Path,
    item_dir: Path,
    references: ReferenceFiles,
    with_volume_automation: bool,
) -> list[str]:
    cmd = [
        python_exe,
        str(auto_mix_script),
        str(render_vocal),
        str(accomp_path),
        str(output_wav),
        "--report-dir",
        str(item_dir),
    ]
    if references.complete:
        cmd.extend(
            [
                "--reference-audio",
                str(references.full_mix),
                "--reference-vocal",
                str(references.vocal),
                "--reference-accomp",
                str(references.accomp),
            ]
        )
    if with_volume_automation:
        cmd.append("--with-volume-automation")
    return cmd


def render_case(
    case: RenderCase,
    *,
    download_root: Path = DEFAULT_DOWNLOAD_ROOT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    python_exe: str | None = None,
    auto_mix_script: Path | None = None,
    music_root: Path = DEFAULT_MUSIC_ROOT,
    with_volume_automation: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    label = case_label(case)
    item_dir = output_dir / label
    output_wav = item_dir / f"{label}_mix.wav"
    summary_path = item_dir / "summary.json"

    if output_wav.exists() and summary_path.exists() and not force:
        return load_json(summary_path)

    item_dir.mkdir(parents=True, exist_ok=True)
    python_exe = python_exe or default_python()
    auto_mix_script = auto_mix_script or ROOT / "scripts" / "auto_template_mix.py"
    render_vocal = prepare_render_vocal(case.dry_path, case.accomp_path, item_dir, force, music_root)
    references = resolve_reference_files(case, download_root)
    cmd = build_render_command(
        python_exe,
        auto_mix_script,
        render_vocal,
        case.accomp_path,
        output_wav,
        item_dir,
        references,
        with_volume_automation,
    )

    write_json(
        item_dir / "pipeline_invocation.json",
        {
            "case": {
                "row": case.row,
                "case_name": case.case_name,
                "extra_name": case.extra_name,
                "dry_path": str(case.dry_path),
                "accomp_path": str(case.accomp_path),
            },
            "render_vocal": str(render_vocal),
            "references": references.as_dict(),
            "command": cmd,
        },
    )

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
        row = f"row {case.row}" if case.row is not None else case.case_name
        raise RuntimeError(
            f"mix failed for {row}: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return load_json(summary_path)


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
    current_tail = "；当前 renderer 追加 DelayVerb BPM/space_profile send、伴奏 transient safety、参考动态平衡、master loudness finalizer"
    return f"特征: {metrics}; 命中: {rules}; 处理: {chains.get(selected_template, selected_template)}{current_tail}"


def sheet_row_from_summary(case: RenderCase, summary: dict[str, Any]) -> dict[str, Any]:
    analysis = load_json(Path(summary["analysis_json"]))
    selected_template = str(summary.get("selected_template") or "")
    output_wav = str(Path(summary["output_wav"]).resolve(strict=False))
    return {
        "row": case.row,
        "名称": case.case_name,
        "B列": case.extra_name,
        "干声": str(case.dry_path.resolve(strict=False)),
        "背景音": str(case.accomp_path.resolve(strict=False)),
        "模版": str(summary.get("classification_label") or ""),
        "处理说明": processing_note(analysis, selected_template),
        "混音结果": output_wav,
        "analysis_json": str(Path(summary["analysis_json"]).resolve(strict=False)),
        "plan_json": str(Path(summary["resolved_mix_plan"]).resolve(strict=False)),
        "summary_json": str((Path(summary["analysis_json"]).parent / "summary.json").resolve(strict=False)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one case through the unified analyzer + renderer pipeline.")
    parser.add_argument("--dry", type=Path, required=True, help="Dry vocal file.")
    parser.add_argument("--accomp", type=Path, required=True, help="Accompaniment file.")
    parser.add_argument("--case-name", default="")
    parser.add_argument("--extra-name", default="")
    parser.add_argument("--row", type=int, default=None)
    parser.add_argument("--download-root", type=Path, default=DEFAULT_DOWNLOAD_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--music-root", type=Path, default=DEFAULT_MUSIC_ROOT)
    parser.add_argument("--python", default=default_python())
    parser.add_argument("--auto-mix-script", type=Path, default=ROOT / "scripts" / "auto_template_mix.py")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--with-volume-automation", action="store_true")
    parser.add_argument("--sheet-row", action="store_true", help="Print the generated Feishu-style row instead of summary.")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    case = RenderCase(
        row=args.row,
        case_name=args.case_name,
        extra_name=args.extra_name,
        dry_path=args.dry.expanduser().resolve(strict=False),
        accomp_path=args.accomp.expanduser().resolve(strict=False),
    )
    summary = render_case(
        case,
        download_root=args.download_root,
        output_dir=args.output_dir,
        python_exe=args.python,
        auto_mix_script=args.auto_mix_script,
        music_root=args.music_root,
        with_volume_automation=args.with_volume_automation,
        force=args.force,
    )
    payload = sheet_row_from_summary(case, summary) if args.sheet_row else summary
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
