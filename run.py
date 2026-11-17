#!/usr/bin/env python3
"""One-step launcher for FantasyCoach. No virtual environment needed.

    python3 run.py              # start the web app and open your browser
    python3 run.py lineup       # any CLI command works too (lineup, waivers, setup, ...)
    python3 run.py web --host 0.0.0.0 --no-open

On first run, missing packages are installed into a local `.deps/` folder inside this
project (nothing is installed system-wide, so it works on Ubuntu's managed Python too).
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 10)
ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements.txt"
# Separate folder per OS/Python version, since some packages ship compiled code.
DEPS = ROOT / ".deps" / f"{platform.system().lower()}-{platform.machine().lower()}-py{sys.version_info[0]}{sys.version_info[1]}"
MODULES = ["httpx", "typer", "rich", "fastapi", "uvicorn"]


def missing_modules() -> list[str]:
    import importlib.util

    return [m for m in MODULES if importlib.util.find_spec(m) is None]


def install_deps() -> None:
    print(f"First run: installing dependencies into {DEPS.relative_to(ROOT)} ...", flush=True)
    try:
        import pip  # noqa: F401
    except ImportError:
        sys.exit(
            "pip is not available for this Python.\n"
            "On Ubuntu/Debian run:  sudo apt install python3-pip\n"
            "then run this script again."
        )
    DEPS.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
           "--upgrade", "--target", str(DEPS), "-r", str(REQUIREMENTS)]
    if subprocess.call(cmd) != 0:
        sys.exit("Dependency install failed (see errors above).")


def main() -> None:
    if sys.version_info < MIN_PYTHON:
        sys.exit(f"FantasyCoach needs Python {'.'.join(map(str, MIN_PYTHON))}+; you have {platform.python_version()}.")

    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(1, str(DEPS))

    if missing_modules():
        install_deps()
        import importlib

        importlib.invalidate_caches()
        still = missing_modules()
        if still:
            sys.exit(f"Still missing after install: {', '.join(still)}")

    from fantasycoach.cli import main as cli_main

    if len(sys.argv) == 1:
        sys.argv.append("web")
    cli_main()


if __name__ == "__main__":
    main()
