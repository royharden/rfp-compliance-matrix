"""Execute the bundled public arithmetic fixture; never discover evaluation inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from rfp_matrix.eval.scoring import score_packet


def evaluate(root: Path) -> dict:
    fixture = root / "eval" / "hand-oracle.v1.json"
    raw = fixture.read_bytes()
    result = score_packet(json.loads(raw))
    baseline = result["methods"]["B0a"]
    expected = {
        "inventory_precision": (baseline["inventory"]["precision"]["value"], 2 / 3),
        "inventory_recall": (baseline["inventory"]["recall"]["value"], 2 / 3),
        "mandatory_recall": (baseline["inventory"]["mandatory_recall"]["value"], 1),
        "retrieval_recall": (baseline["retrieval"]["recall_at_10"], 3 / 4),
        "retrieval_mrr": (baseline["retrieval"]["mrr_at_10"], 3 / 4),
        "citation_precision": (baseline["citations"]["semantic_precision"]["value"], 1 / 2),
        "cannot_answer_precision": (baseline["cannot_answer"]["combined"]["precision"]["value"], 1 / 2),
        "cannot_answer_coverage": (baseline["cannot_answer"]["combined"]["coverage"]["value"], 1 / 2),
    }
    checks = {name: actual == wanted for name, (actual, wanted) in expected.items()}
    checks["one_to_one_count"] = len(baseline["inventory"]["matches"]) == 2
    checks["missing_arms_unmeasured"] = all(
        result["methods"][arm]["status"] == "not_yet_measured" for arm in ("B0b", "F1", "F2")
    )
    checks["no_quality_claim"] = result["public_quality_claim"] is False
    candidate = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    return {
        "schema_version": 1,
        "scope": "public authored development arithmetic only",
        "status": "passed" if all(checks.values()) else "failed",
        "candidate_commit": candidate,
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "scorer_sha256": hashlib.sha256((root / "src/rfp_matrix/eval/scoring.py").read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checks": checks,
        "result": result,
        "heldout": "not_run: no authorization or private inputs",
        "live_models": "not_run: no keys, budget or provider calls",
        "model_quality_acceptance": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="New receipt file; never overwritten")
    args = parser.parse_args()
    result = evaluate(Path(__file__).resolve().parents[1])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "receipt": str(args.out)}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
