"""Explicit future local serve entry point. Importing this module never listens.

Before invocation, the operator must use agent-ports claim/check for this project
and retain its reference. This product records an attestation, not a fabricated
verification of a machine-specific external coordination ledger. No auto-claim,
guessed/default port, browser launch, reload worker or background service exists.
"""
from __future__ import annotations

import json
import sys
from typing import Sequence

from .application import ReviewerApplication
from .store import ReviewError, ReviewStore, safe_id


def _announce_private_delivery(path, *, port: int, lifetime: int) -> None:
    """Announce a NONSECRET path only; never read/display the capability file."""
    print(json.dumps({"status": "private_login_delivery_ready", "private_login_file": str(path),
                      "login_url": f"http://127.0.0.1:{port}/login", "expires_in_seconds": lifetime}), file=sys.stderr)


def serve_local(store: ReviewStore, *, port: int, port_claim_reference: str,
                run_ids: Sequence[str], private_login_delivery: bool = False) -> dict:
    """Future explicitly authorized foreground invocation; never called at import."""
    if type(port) is not int or not 20000 <= port <= 29999 or port in {20178, 20179}:
        raise ReviewError("claimed_port_required", "Supply the app's claimed port in 20000..29999; protected PracticeHub ports are forbidden.")
    if not isinstance(port_claim_reference, str) or not port_claim_reference.strip() or len(port_claim_reference) > 512:
        raise ReviewError("port_claim_reference_required", "Run agent-ports claim/check first and supply its nonsecret reference.")
    if (not isinstance(run_ids, (list, tuple)) or not 1 <= len(run_ids) <= 200
            or any(not isinstance(run_id, str) for run_id in run_ids)
            or len(set(run_ids)) != len(run_ids)):
        raise ReviewError("invalid_run_grant", "Explicitly name 1..200 distinct existing runs for the local reviewer.")
    if private_login_delivery is not True:
        raise ReviewError("private_bootstrap_required", "Local authentication requires explicit --private-login-delivery; terminal/login-secret output is forbidden.")
    application = ReviewerApplication(store)
    for run_id in run_ids:
        safe_id(run_id)
        application.get_run(run_id)
    try:
        import uvicorn
        from rfp_matrix.web import create_app
        from rfp_matrix.web.security import LocalSessionAuthority, ReviewerIdentity
    except ImportError as exc:
        raise ReviewError("optional_web_unavailable", "Optional web dependencies are absent. Activate declared web extras only after Dirty Mode closes; no automatic install was attempted.") from exc
    authority = LocalSessionAuthority()
    app = create_app(application, port=port, authority=authority)
    from .private_delivery import private_delivery
    # Verify owner-only directory/file privacy BEFORE issuing a capability. A
    # platform/ACL failure aborts with no insecure fallback and no listener.
    with private_delivery(store.root) as delivery:
        capability = authority.issue_login_challenge(ReviewerIdentity(application.reviewer_subject, frozenset(run_ids)))
        path = delivery.write(capability, port=port, lifetime=authority.challenge_seconds)
        del capability
        _announce_private_delivery(path, port=port, lifetime=authority.challenge_seconds)
        # No access logs, proxy forwarding, reload child, remote bind or browser open.
        uvicorn.run(app, host="127.0.0.1", port=port, reload=False, workers=1,
                    access_log=False, proxy_headers=False, server_header=False)
    return {"status": "server_stopped", "host": "127.0.0.1", "port": port,
            "port_claim_reference": port_claim_reference, "port_claim_verified_by_product": False,
            "authentication": "one_time_local_capability", "runtime_verified": False}
