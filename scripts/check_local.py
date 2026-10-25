"""Run offline acceptance with preserved logs, exit status and an explicit interpreter.

Use a fresh machine-local --out directory. This script neither installs packages
nor starts listeners, Docker, providers, or hidden/holdout evaluation.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Pinned test environment's interpreter")
    parser.add_argument("--out", required=True, type=Path, help="New directory outside OneDrive; artifacts are preserved")
    parser.add_argument("--suite", choices=("all", "cli"), default="all")
    parser.add_argument("--dependency-lock", type=Path, help="Resolved lockfile used to provision the interpreter")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.out.expanduser().resolve()
    if output.exists() or any(part.casefold() == "onedrive" for part in output.parts):
        parser.error("--out must be a new machine-local directory outside OneDrive")
    output.mkdir(parents=True)
    suite = ["tests/integration/test_cli_journey.py"] if args.suite == "cli" else ["tests", "--ignore=tests/eval"]
    command = [args.python, "-m", "pytest", *suite, "-q", "--import-mode=importlib",
               "--basetemp", str(output / "pytest-tmp"), "--junitxml", str(output / "junit.xml")]
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(root), "status", "--short"], capture_output=True, text=True, check=True).stdout
    def input_hashes():
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for folder in (root / "src", root / "tests", root / "scripts", root / "demo")
                for path in folder.rglob("*")
                if path.is_file() and path.suffix in {".py", ".json", ".jsonl", ".html", ".css", ".md"}
                and "__pycache__" not in path.parts}
    hashes = input_hashes()
    started = datetime.now(timezone.utc).isoformat()
    metadata = subprocess.run(
        [args.python, "-c", "import importlib.metadata as m,json,sys; print(json.dumps({'python':sys.version,'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))"],
        capture_output=True, text=True, check=True)
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
    after_hashes = input_hashes()
    unchanged = hashes == after_hashes
    exit_code = result.returncode if unchanged else 3
    receipt = {"schema_version": 1, "scope": "offline development integration only",
               "commit": commit, "working_tree_status": dirty, "source_sha256": hashes,
               "command": command, "started_at": started,
               "finished_at": datetime.now(timezone.utc).isoformat(), "exit_code": exit_code,
               "pytest_exit_code": result.returncode, "inputs_unchanged": unchanged,
               "source_sha256_after": after_hashes,
               "suite": args.suite, "log": "pytest.log", "junit": "junit.xml",
               "live_model_quality": "not_run", "hidden_holdout": "not_run"}
    receipt["environment"] = json.loads(metadata.stdout)
    if args.dependency_lock:
        receipt["dependency_lock"] = {"path": str(args.dependency_lock.resolve()),
            "sha256": hashlib.sha256(args.dependency_lock.read_bytes()).hexdigest()}
    if (output / "junit.xml").is_file():
        receipt["counts"] = [{key: suite.attrib.get(key) for key in ("tests", "failures", "errors", "skipped")}
                             for suite in ET.parse(output / "junit.xml").getroot().iter("testsuite")]
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"exit_code": exit_code, "receipt": str(output / "receipt.json")}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
