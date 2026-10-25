"""Offline SQLite/LangGraph journey, using separate processes across restart boundaries.

Runs locally or inside the M3 image. All inputs are authored synthetic development
data; no network, credentials, hidden labels, listeners or cleanup operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from rfp_matrix.evidence import build_matrix, make_corpus
from rfp_matrix.export import export_matrix, verify_export
from rfp_matrix.review import LangGraphReviewDriver, ReviewError, ReviewStore
from rfp_matrix.source import SourceRegistry, parse_text_pages


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def save(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def hashes(directory: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir()) if p.is_file()}


def prepare(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    artifact = parse_text_pages([
        "The vendor must provide encrypted daily backups.\n"
        "The vendor must provide audit log exports."
    ], source_id="m3-public-authored-e2e", terms="Project-authored fictional development fixture")
    corpus = make_corpus([])
    store = ReviewStore(root / "m3-state")
    SourceRegistry(store.root / "sources").save(artifact)
    snapshot = store.create_run("m3-e2e", artifact, corpus, build_matrix(artifact, corpus)["rows"])
    require(len(snapshot["rows"]) == 2, "Expected both authored obligations")
    row = snapshot["rows"][0]
    paused = LangGraphReviewDriver().pause(store, "m3-e2e", row["row_id"])
    require(paused["status"] == "interrupted" and bool(paused["checkpoint_id"]), "Real durable graph pause required")
    event = store.decide("m3-e2e", row["row_id"], "accept_gap",
        expected_assessment_generation=snapshot["assessment_generation"],
        expected_row_revision=row.get("decision_revision", row["row_revision"]),
        request_id=paused["presentation"]["request_id"], reason="Acknowledge missing fictional corpus evidence only")
    require(store.pending_intents("m3-e2e") == [event], "Decision must commit before graph resume")
    saved = {"event": event, "pause": paused, "store_id": store.store_id}
    save(root / "m3-prepared.json", saved)
    return {"status": "passed", "phase": "decision_committed_before_exit", "event_id": event["event_id"]}


def resume_export(root: Path) -> dict:
    prior = read(root / "m3-prepared.json")
    store = ReviewStore(root / "m3-state")
    event = prior["event"]
    require(store.store_id == prior["store_id"], "Restart must reopen the same SQLite authority")
    require(store.events("m3-e2e") == [event], "Restart lost or duplicated decision")
    driver = LangGraphReviewDriver()
    resumed = driver.apply(store, "m3-e2e", event)
    require(resumed["status"] == "resumed_checkpoint", "Existing persisted interrupt must resume without rebuild")
    require(driver.apply(store, "m3-e2e", event)["status"] == "already_applied", "Duplicate resume must be idempotent")
    require(not store.pending_intents("m3-e2e"), "Resume intent must settle")
    snapshot = store.load_run("m3-e2e")
    require(sorted(r["approval"] for r in snapshot["rows"]) == ["approved_gap", "pending"], "Gap and unresolved row must stay distinct")
    receipt = export_matrix(store, "m3-e2e", root / "m3-exports", request_id="m3-export",
        expected_matrix_generation=snapshot["matrix_generation"])
    directory = root / "m3-exports" / receipt["export_id"]
    manifest = read(directory / "manifest.json")
    require(manifest["row_count"] == 2 and len(manifest["unresolved_inventory"]) == 1, "Export dropped unresolved inventory")
    require(verify_export(directory)["status"] == "structurally_verified", "Export verification failed")
    value = {"matrix_generation": snapshot["matrix_generation"], "export_id": receipt["export_id"],
        "artifact_hashes": hashes(directory), "resume": resumed}
    save(root / "m3-exported.json", value)
    return {"status": "passed", "phase": "reopened_resumed_exported", "export_id": receipt["export_id"]}


def retry_invalidate(root: Path) -> dict:
    previous = read(root / "m3-exported.json")
    store = ReviewStore(root / "m3-state")
    retried = export_matrix(store, "m3-e2e", root / "m3-exports", request_id="m3-export",
        expected_matrix_generation=previous["matrix_generation"])
    directory = root / "m3-exports" / retried["export_id"]
    require(retried["export_id"] == previous["export_id"], "Retry created duplicate export")
    require(hashes(directory) == previous["artifact_hashes"], "Restart/retry changed export bytes")
    snapshot = store.load_run("m3-e2e")
    corpus = make_corpus([{"record_id": "m3-new-evidence", "text": "Fictional vendor cannot provide audit log exports.",
        "terms": "Project-authored development data", "fictional": True}])
    changed = store.update_run("m3-e2e", snapshot["artifact"], corpus,
        build_matrix(snapshot["artifact"], corpus)["rows"], expected_matrix_generation=snapshot["matrix_generation"])
    require(all(row["approval"] == "stale" for row in changed["rows"]), "Corpus change retained current approval")
    try:
        export_matrix(store, "m3-e2e", root / "m3-exports", request_id="m3-stale-export",
            expected_matrix_generation=previous["matrix_generation"])
    except ReviewError as error:
        require(error.code == "stale_generation", "Expected stale generation refusal")
    else:
        raise RuntimeError("Stale generation export was accepted")
    require(hashes(directory) == previous["artifact_hashes"], "Invalidation rewrote historical export")
    return {"status": "passed", "phase": "retry_identical_and_corpus_invalidated", "stale_rows": len(changed["rows"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New m3-owned generated state directory")
    parser.add_argument("--phase", choices=("prepare", "resume_export", "retry_invalidate"))
    args = parser.parse_args()
    if args.phase:
        value = globals()[args.phase](args.root)
        print(json.dumps(value, sort_keys=True))
        return 0
    require(args.root.name.startswith("m3-"), "Generated state directory must have m3- prefix")
    require(not args.root.exists(), "Preserve prior artifacts; select a new output directory")
    phases = []
    for phase in ("prepare", "resume_export", "retry_invalidate"):
        output = subprocess.check_output([sys.executable, str(Path(__file__).resolve()),
            "--root", str(args.root), "--phase", phase], text=True, timeout=180)
        phases.append(json.loads(output))
    receipt = {"status": "passed", "scope": "synthetic offline SQLite/LangGraph integration only",
        "process_boundaries": 3, "phases": phases, "model_quality_acceptance": False,
        "live_models": "not_run", "heldout": "not_run"}
    save(args.root / "m3-e2e-receipt.json", receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
