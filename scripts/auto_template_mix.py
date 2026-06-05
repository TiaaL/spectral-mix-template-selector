#!/usr/bin/env python3
"""Bridge from this analyzer repo to the music auto-mix renderer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALUE_OPTIONS = {
    "--analyzer",
    "--analyzer-python",
    "--renderer",
    "--render-backend",
    "--report-dir",
    "--report-prefix",
    "--reference-audio",
    "--reference-vocal",
    "--reference-accomp",
}


def music_project_root() -> Path:
    candidates = [
        ROOT.parent / "music_auto_mix1" / "music_auto_mix1",
        Path(r"D:\code\music_auto_mix1\music_auto_mix1"),
    ]
    for candidate in candidates:
        if (candidate / "scripts" / "auto_template_mix.py").exists():
            return candidate
    raise SystemExit(
        "music_auto_mix1 renderer project was not found. "
        "Expected scripts/auto_template_mix.py under D:\\code\\music_auto_mix1\\music_auto_mix1."
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


def has_option(args: list[str], option: str) -> bool:
    return any(arg == option or arg.startswith(f"{option}=") for arg in args)


def absolutize_mix_positionals(args: list[str]) -> list[str]:
    normalized: list[str] = []
    positionals = 0
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            normalized.extend(args[index:])
            break
        if arg.startswith("--"):
            normalized.append(arg)
            option = arg.split("=", 1)[0]
            if "=" not in arg and option in VALUE_OPTIONS and index + 1 < len(args):
                normalized.append(args[index + 1])
                index += 2
                continue
            index += 1
            continue
        if arg.startswith("-"):
            normalized.append(arg)
            index += 1
            continue

        if positionals < 3:
            normalized.append(str(Path(arg).expanduser().resolve(strict=False)))
        else:
            normalized.append(arg)
        positionals += 1
        index += 1
    return normalized


def main() -> None:
    mix_root = music_project_root()
    forwarded = absolutize_mix_positionals(sys.argv[1:])

    if not has_option(forwarded, "--analyzer"):
        forwarded.extend(["--analyzer", str(ROOT / "spectrum_template_analyzer.py")])
    if not has_option(forwarded, "--analyzer-python"):
        forwarded.extend(["--analyzer-python", local_python()])

    cmd = [local_python(), str(mix_root / "scripts" / "auto_template_mix.py"), *forwarded]
    raise SystemExit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()
