#!/usr/bin/env python3
"""Bridge from analyzer JSON to the music project's template-plan resolver."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def music_project_root() -> Path:
    candidates = [
        ROOT.parent / "music_auto_mix1" / "music_auto_mix1",
        Path(r"D:\code\music_auto_mix1\music_auto_mix1"),
    ]
    for candidate in candidates:
        if (candidate / "scripts" / "plan_mix_template.py").exists():
            return candidate
    raise SystemExit(
        "music_auto_mix1 renderer project was not found. "
        "Expected scripts/plan_mix_template.py under D:\\code\\music_auto_mix1\\music_auto_mix1."
    )


def local_python() -> str:
    candidates = [
        ROOT / "python" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def absolutize_first_positional(args: list[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            normalized.extend(args[index:])
            break
        if arg.startswith("-"):
            normalized.append(arg)
            index += 1
            if arg in {"--output", "--fallback"} and index < len(args):
                normalized.append(args[index])
                index += 1
            continue
        normalized.append(str(Path(arg).expanduser().resolve(strict=False)))
        normalized.extend(args[index + 1 :])
        break
    return normalized


def main() -> None:
    mix_root = music_project_root()
    cmd = [
        local_python(),
        str(mix_root / "scripts" / "plan_mix_template.py"),
        *absolutize_first_positional(sys.argv[1:]),
    ]
    raise SystemExit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()
