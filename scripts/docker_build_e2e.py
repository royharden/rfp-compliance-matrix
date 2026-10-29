"""Build only committed allowlisted M3 source; never send a whole checkout context."""

import argparse
import io
from pathlib import Path
import re
import subprocess
import tarfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="Full exact reviewed candidate commit")
    parser.add_argument("--base-image", required=True, help="Exact python@sha256:... base image")
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{40}", args.candidate):
        parser.error("Full immutable commit hash required")
    if not re.fullmatch(r"python@sha256:[a-f0-9]{64}", args.base_image):
        parser.error("Digest-pinned official Python image required")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.owner):
        parser.error("Bounded callsign required")
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "--name-only", args.candidate], text=True).splitlines()
    selected = [name for name in names if (
        name.startswith("src/rfp_matrix/") and Path(name).suffix in {".py", ".html", ".css"}
    ) or name in {"Dockerfile.e2e", "eval/docker-requirements.txt", "scripts/docker_sqlite_e2e.py"}]
    if not {"Dockerfile.e2e", "eval/docker-requirements.txt", "scripts/docker_sqlite_e2e.py"}.issubset(selected):
        parser.error("Candidate lacks Docker packaging inputs")
    context = io.BytesIO()
    with tarfile.open(fileobj=context, mode="w") as archive:
        for name in sorted(selected):
            data = subprocess.check_output(["git", "-C", str(root), "show", f"{args.candidate}:{name}"])
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    command = ["docker", "build", "--file", "Dockerfile.e2e", "--build-arg", f"BASE_IMAGE={args.base_image}",
        "--label", "agentports.project=m3", "--label", "agentports.instance=r03-rv2-e2e",
        "--label", f"agentports.owner={args.owner}", "--label", f"m3.candidate={args.candidate}",
        "--tag", f"m3-r03-rv2-e2e:{args.candidate[:12]}", "-"]
    return subprocess.run(command, input=context.getvalue(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
