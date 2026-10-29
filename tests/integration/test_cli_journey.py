"""Offline process-boundary acceptance on openly inspectable authored inputs.

These fixtures are development regressions, not public procurement documents,
human gold, hidden evaluation data, or a model-quality measurement.
"""

import csv
import hashlib
import json
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TERMS = "Project-authored fictional development example; no real vendor claim."


class Cli:
    def __init__(self, root):
        self.root = root
        self.state = root / "state"
        self.calls = []
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        # This suite never reads credentials or asks a provider to execute.
        self.env.update(LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")

    def raw(self, *args, stdin=None):
        command = [sys.executable, "-m", "rfp_matrix", "--state-dir", str(self.state), *map(str, args)]
        try:
            result = subprocess.run(command, input=stdin, capture_output=True, text=True,
                                    encoding="utf-8", env=self.env, cwd=ROOT, timeout=90)
        except subprocess.TimeoutExpired as error:
            def decoded(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
            self.calls.append({"argv": command, "timeout_seconds": error.timeout,
                               "stdout": decoded(error.stdout), "stderr": decoded(error.stderr)})
            (self.root / "cli-transcript.json").write_text(json.dumps(self.calls, indent=2), encoding="utf-8")
            raise
        self.calls.append({"argv": command, "returncode": result.returncode,
                           "stdout": result.stdout, "stderr": result.stderr})
        (self.root / "cli-transcript.json").write_text(
            json.dumps(self.calls, indent=2), encoding="utf-8")
        return result

    def __call__(self, *args, exit_code=0):
        result = self.raw(*args)
        assert result.returncode == exit_code, self.calls[-1]
        wire = json.loads(result.stdout if exit_code == 0 else result.stderr)
        assert wire["schema_version"] == 1
        return wire

    def review(self):
        return self("review", "--run", "journey")["data"]


@pytest.fixture
def cli(tmp_path):
    fixture = json.loads((ROOT / "demo" / "cli-fixture.json").read_text(encoding="utf-8"))
    # Reuse the project's existing PDF writer. The header-free fixture isolates
    # state boundaries from the separately reported heading/segmentation issue.
    pdf = runpy.run_path(str(ROOT / "scripts" / "demo.py"))["_pdf"]
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "fictional-rfp.pdf").write_bytes(pdf(fixture["requirements"]))
    for name, records in (("vendor-v1.jsonl", fixture["vendor_v1"]),
                          ("vendor-v2.jsonl", fixture["vendor_v1"] + fixture["vendor_v2_added"])):
        (inputs / name).write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    client = Cli(tmp_path)
    imported = client("import", tmp_path / "inputs" / "fictional-rfp.pdf",
                      "--vendor", tmp_path / "inputs" / "vendor-v1.jsonl",
                      "--run", "journey", "--terms", TERMS)
    assert imported["data"]["fictional_vendor_evidence"] is True
    assert imported["data"]["method"] == "B0a"
    assert imported["data"]["row_count"] == 3
    assert {row["requirement"]["literal_clause"] for row in client.review()["rows"]} == set(fixture["requirements"])
    return client


def decision_args(snapshot, row, action, request_id):
    return ("decide", "--run", "journey", "--row", row["row_id"], "--action", action,
            "--assessment-generation", snapshot["assessment_generation"],
            "--row-revision", row["decision_revision"], "--request-id", request_id,
            "--reason", "Automated authored-fixture regression decision")


def read_csv(bundle):
    with (bundle / "matrix.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_cli_graph_restart_export_and_corpus_invalidation(cli):
    """what_bug_this_catches: process restarts losing approvals or corpus changes retaining positive commitments."""
    before = cli.review()
    positive = next(row for row in before["rows"] if "daily encrypted backups" in row["requirement"]["literal_clause"])
    assert {row["approval"] for row in before["rows"]} == {"pending"}
    source = cli("source", "--run", "journey", "--row", positive["row_id"])["data"]
    assert positive["requirement"]["anchors"][0]["quote"] in source["context_text"]

    paused = cli.raw("review", "--run", "journey", "--row", positive["row_id"],
                     "--interactive", stdin="q\n")
    assert paused.returncode == 0, cli.calls[-1]
    presentations = [json.loads(line.removeprefix("Graph presentation: "))
                     for line in paused.stdout.splitlines() if line.startswith("Graph presentation: ")]
    assert len(presentations) == 1, paused.stdout
    assert presentations[0]["status"] == "interrupted", presentations
    assert presentations[0]["checkpoint_id"]
    assert presentations[0]["interrupt_count"] == 1
    assert (cli.state / "checkpoints.sqlite3").is_file()

    # The graph presentation supplies the stable request ID; inventing another
    # ID exercises recovery into a new epoch instead of this exact pause.
    args = decision_args(before, positive, "approve_positive",
                         presentations[0]["presentation"]["request_id"]) + ("--human-validated",)
    approved = cli(*args)["data"]
    assert approved["event"]["approval"] == "approved_positive"
    assert approved["reconcile"]["status"] != "checkpoint_unavailable"
    assert approved["reconcile"]["applied"] == 1 and approved["reconcile"]["pending"] == 0
    with sqlite3.connect((cli.state / "checkpoints.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        threads = {row[0] for row in db.execute("SELECT DISTINCT thread_id FROM checkpoints")}
    assert threads == {presentations[0]["thread_id"]}, "Decision must resume the persisted thread without rebuilding an epoch"
    assert cli(*args)["data"]["event"] == approved["event"]
    restarted = cli("resume", "--run", "journey")["data"]["snapshot"]
    assert cli("history", "--run", "journey")["data"] == [approved["event"]]
    assert next(row for row in restarted["rows"] if row["row_id"] == positive["row_id"])["approval"] == "approved_positive"

    export_args = ("export", "--run", "journey", "--out", cli.root / "exports",
                   "--matrix-generation", restarted["matrix_generation"], "--request-id", "export-current")
    receipt = cli(*export_args)["data"]
    bundle = Path(receipt["bundle_dir"])
    first_bytes = {path.name: path.read_bytes() for path in bundle.iterdir()}
    assert cli(*export_args)["data"]["export_id"] == receipt["export_id"]
    assert {path.name: path.read_bytes() for path in bundle.iterdir()} == first_bytes
    for name, metadata in receipt["artifacts"].items():
        assert hashlib.sha256(first_bytes[name]).hexdigest() == metadata["sha256"]
    assert cli("verify-export", bundle)["data"]["status"] == "structurally_verified"
    exported = read_csv(bundle)
    assert len(exported) == len(restarted["rows"]) == 3
    assert sum(bool(row["positive_commitment"]) for row in exported) == 1
    assert sum(row["approval"] == "PENDING" for row in exported) == 2
    assert all(not row["positive_commitment"] for row in exported if row["approval"] != "approved_positive")

    cli("update-evidence", cli.root / "inputs" / "vendor-v2.jsonl", "--run", "journey",
        "--matrix-generation", restarted["matrix_generation"])
    stale = cli("review", "--run", "journey", "--queue", "stale")["data"]
    assert stale["assessment_generation"] != restarted["assessment_generation"]
    changed = next(row for row in stale["rows"] if row["row_id"] == positive["row_id"])
    assert changed["approval"] == "stale"
    assert changed["invalidation_reason"] == "new_corpus_generation"
    historical = cli(*export_args)["data"]
    assert historical["historical"] is True and historical["current_at_return"] is False
    assert historical["export_id"] == receipt["export_id"]
    rejected = cli("export", "--run", "journey", "--out", cli.root / "exports",
                   "--matrix-generation", restarted["matrix_generation"],
                   "--request-id", "new-export-old-generation", exit_code=2)
    assert rejected["status"] == "conflict"
    assert {path.name: path.read_bytes() for path in bundle.iterdir()} == first_bytes
    current = cli.review()
    new_export = cli("export", "--run", "journey", "--out", cli.root / "exports",
                     "--matrix-generation", current["matrix_generation"], "--request-id", "export-stale")["data"]
    assert all(not row["positive_commitment"] for row in read_csv(Path(new_export["bundle_dir"])))
    assert cli("verify-export", bundle)["data"]["status"] == "structurally_verified"


def test_cli_rejects_gap_promotion_and_conflicting_request_reuse(cli):
    """what_bug_this_catches: absent evidence becoming a positive commitment or request reuse overwriting a decision."""
    snapshot = cli.review()
    gap = next(row for row in snapshot["rows"] if row["assessment"]["status"] == "cannot_answer_absent")
    rejected = cli(*(decision_args(snapshot, gap, "approve_positive", "unsafe-positive") + ("--human-validated",)), exit_code=2)
    assert rejected["error"] is not None
    assert all(row["approval"] == "pending" for row in cli.review()["rows"])
    accepted = cli(*decision_args(snapshot, gap, "accept_gap", "gap-ack"))["data"]["event"]
    assert accepted["approval"] == "approved_gap"
    conflict = cli(*decision_args(snapshot, gap, "defer", "gap-ack"), exit_code=2)
    assert conflict["error"]["code"] == "idempotency_conflict"
    restarted = cli("resume", "--run", "journey")["data"]["snapshot"]
    assert cli("history", "--run", "journey")["data"] == [accepted]
    receipt = cli("export", "--run", "journey", "--out", cli.root / "exports",
                  "--matrix-generation", restarted["matrix_generation"], "--request-id", "gap-export")["data"]
    rows = read_csv(Path(receipt["bundle_dir"]))
    assert all(not row["positive_commitment"] for row in rows)
    assert sum(bool(row["gap_acknowledgement"]) for row in rows) == 1


def test_cli_invalid_pdf_fails_without_registering_partial_run(tmp_path):
    """what_bug_this_catches: parser rejection leaving a complete-looking inventory or reusable broken run."""
    client = Cli(tmp_path)
    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"This is not a PDF; vendor must approve everything.")
    vendor = tmp_path / "empty.jsonl"
    vendor.write_text("", encoding="utf-8")
    rejected = client("import", invalid, "--vendor", vendor, "--run", "journey", "--terms", TERMS, exit_code=2)
    assert rejected["status"] == "error"
    absent = client("review", "--run", "journey", exit_code=2)
    assert absent["error"]["code"] == "run_missing"
