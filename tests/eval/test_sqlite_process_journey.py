"""Process-level regression for the same offline journey packaged by Docker."""

import json
from pathlib import Path
import subprocess
import sys


def test_sqlite_restart_resume_export_and_invalidation(tmp_path):
    """what_bug_this_catches: process restart loses a committed decision, resumes
    twice, changes export bytes, or retains approvals after corpus replacement.
    """
    script = Path(__file__).resolve().parents[2] / "scripts/docker_sqlite_e2e.py"
    root = tmp_path / "m3-process-e2e"
    result = subprocess.run([sys.executable, str(script), "--root", str(root)],
        capture_output=True, text=True, timeout=540)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "passed"
    assert receipt["process_boundaries"] == 3
    assert receipt["phases"][-1]["stale_rows"] == 2
    assert receipt["model_quality_acceptance"] is False
    assert (root / "m3-state/review.sqlite3").is_file()
    assert (root / "m3-state/checkpoints.sqlite3").is_file()
