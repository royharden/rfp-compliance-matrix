"""Install the locked local test environment without enabling live providers."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default="3.12.13", help="Python version or interpreter path")
    parser.add_argument("--uv", default="uv", help="uv executable name or absolute path")
    parser.add_argument("--offline", action="store_true", help="Use only cached dependencies")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    uv = shutil.which(args.uv)
    if uv is None:
        parser.error("uv is required; install it before running bootstrap")
    if not (project / "uv.lock").is_file():
        parser.error("uv.lock is missing; restore the committed lockfile before installing")
    command = [uv, "sync", "--project", str(project), "--python", args.python,
               "--locked", "--extra", "test"]
    if args.offline:
        command.extend(["--offline", "--no-python-downloads"])
    result = subprocess.run(command, check=False)
    if result.returncode == 0:
        python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt"
                                       else "bin/python")
        print(f"Locked keyless test environment ready: {python}")
        print("Live-provider extras are excluded. No service has been started.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
