"""UNRUN route source with an injected fake application; no server or real store."""
import asyncio
import hashlib
import json

import pytest

from rfp_matrix.web import create_app
from rfp_matrix.web.security import LocalSessionAuthority, ReviewerIdentity

# create_app/run_case need the optional `web` extra plus dev httpx; skip, not fail, without them.
for _optional in ("fastapi", "jinja2", "httpx"):
    pytest.importorskip(_optional)

ORIGIN = "http://127.0.0.1:25001"  # Future in-process ASGI test scope, never a listener.


class FakeApplication:
    reviewer_subject = "test-reviewer"

    def __init__(self):
        self.calls = []
        self.historical = False
        self.bad_hash = False
        self.conflicting_context = False
        self.unavailable_export = False
        self.correspondence_events = {}
        self.correspondence_revision = None
        self.approvals = {"old-row": "approved_positive", "new-row": "pending"}
        self.promotions = {}
        self.promotion_requests = {}

    def list_runs(self):
        return {"runs": [{"run_id": "owned"}, {"run_id": "not-granted"}]}

    def get_run(self, run_id):
        self.calls.append(("get_run", run_id))
        return {"run_id": run_id, "matrix_generation": "a" * 64, "assessment_generation": "b" * 64,
            "rows": [], "coverage": {"effective": {"complete": False, "artifact_sha": "d" * 64, "pages": []}}}

    def get_row(self, run_id, row_id):
        return {"snapshot": self.get_run(run_id), "row": {"row_id": row_id, "row_revision": "c" * 64,
                "assessment_generation": "b" * 64, "approval": "pending"}}

    def source_context(self, run_id, row_id):
        return {"matrix_generation": "changed" if self.conflicting_context else "a" * 64,
                "row_revision": "c" * 64, "context": {"anchors": []}}

    def coverage(self, run_id):
        return {"run_id": run_id, "matrix_generation": "a" * 64, "effective": {
            "artifact_sha": "d" * 64, "complete": False, "pages": []}}

    def decide(self, run_id, row_id, payload):
        self.calls.append(("decide", run_id, row_id, payload))
        return {"event": {"action": payload["action"]}}

    def inspect_coverage(self, run_id, payload):
        self.calls.append(("inspect", run_id, payload))
        return {"event": {"page_index": payload["page_index"]}}

    def download_export(self, run_id, request_id, filename):
        self.calls.append(("download", run_id, request_id, filename))
        if self.unavailable_export:
            class Unpublished(Exception):
                code = "export_unavailable"
            raise Unpublished()
        content = b"synthetic committed export"
        return {"filename": filename, "content": content, "current": not self.historical,
                "sha256": "wrong" if self.bad_hash else hashlib.sha256(content).hexdigest()}

    def amendments(self, run_id, *, previous_matrix_generation=None):
        self.calls.append(("amendments", run_id, previous_matrix_generation))
        old = {name: "a" * 64 for name in ["artifact_sha", "raw_sha", "assessment_generation",
                                          "matrix_generation", "corpus_generation", "snapshot_pin"]}
        new = {name: "b" * 64 for name in old}
        comparison = {"schema_version": 1, "run_id": run_id, "comparison_id": "c" * 64,
            "report_sha": "d" * 64, "old_identity": old, "new_identity": new,
            "old_rows": [{"row_id": "old-row", "row_revision": "e" * 64, "retired": False}],
            "new_rows": [{"row_id": "new-row", "row_revision": "f" * 64, "retired": False}],
            "candidates": [], "counts": {"old_active": 1, "new_active": 1},
            "approval_carry_allowed": False, "human_events": list(self.correspondence_events.values())}
        return {"schema_version": 1, "run_id": run_id, "comparison": comparison,
                "human_events": list(self.correspondence_events.values()),
                "correspondence_revision": self.correspondence_revision}

    def record_correspondence(self, run_id, payload):
        class Rejected(ValueError):
            def __init__(self, code):
                self.code = code
                super().__init__(code)
        if payload["expected_matrix_generation"] != "b" * 64:
            raise Rejected("stale_correspondence")
        if payload["old_rows"] != [{"row_id": "old-row", "row_revision": "e" * 64}] or payload["new_rows"] != [{"row_id": "new-row", "row_revision": "f" * 64}]:
            raise Rejected("stale_correspondence_row")
        previous = self.correspondence_events.get(payload["request_id"])
        if previous:
            if previous["payload"] != payload:
                raise Rejected("idempotency_conflict")
            return previous
        if payload["expected_correspondence_revision"] != self.correspondence_revision:
            raise Rejected("stale_correspondence_revision")
        self.calls.append(("record_correspondence", run_id, payload))
        event = {"event_id": "human-mapping-" + str(len(self.correspondence_events) + 1),
                 "payload": dict(payload), "previous_revision": self.correspondence_revision,
                 "revision": "9" * 64, "approval_carry_allowed": False}
        self.correspondence_events[payload["request_id"]] = event
        self.correspondence_revision = event["revision"]
        return event

    def inventory_audit(self, run_id):
        self.calls.append(("inventory_audit", run_id))
        snapshot = self.get_run(run_id)
        snapshot["artifact"] = {"artifact_sha": "d" * 64}
        snapshot["rows"] = [result["row"] for result in self.promotions.values()]
        audit = {"artifact_sha": "d" * 64, "audit_sha": "1" * 64, "registry_sha": "2" * 64,
            "inventory_report_sha": "3" * 64, "entries": [{"candidate_id": "candidate-one",
                "disposition": "rejected", "machine_disposition": "rejected", "machine_reason": "context",
                "promotable": True, "literal_clause": "Synthetic source clause", "mandatory": False,
                "conditional": False, "administrative": False,
                "anchor": {"artifact_sha": "d" * 64, "page_index": 0, "start": 0, "end": 23}}],
            "counts": {"candidates": 1, "rejected": 1, "retained": 0, "uncertain": 0, "unattributed": 0},
            "segments": [], "structural_gaps": [], "page_coverage": [], "coverage_complete": False}
        return {"snapshot": snapshot, "audit": audit, "promotions": [result["event"] for result in self.promotions.values()]}

    def promote_inventory_candidate(self, run_id, payload):
        class Rejected(ValueError):
            def __init__(self, code):
                self.code = code
                super().__init__(code)
        previous = self.promotion_requests.get(payload["request_id"])
        if previous:
            if previous[0] != payload:
                raise Rejected("idempotency_conflict")
            return previous[1]
        if payload["candidate_id"] != "candidate-one":
            raise Rejected("unknown_inventory_candidate")
        if payload["candidate_id"] in self.promotions:
            raise Rejected("duplicate_inventory_promotion")
        expected = {"expected_artifact_sha": "d" * 64, "expected_assessment_generation": "b" * 64,
                    "expected_matrix_generation": "a" * 64}
        if any(payload[key] != value for key, value in expected.items()):
            raise Rejected("stale_inventory_promotion")
        self.calls.append(("promote_inventory_candidate", run_id, payload))
        row = {"row_id": "human-pending-one", "approval": "pending", "decision_event_id": None,
               "human_origin": {"candidate_id": payload["candidate_id"], "artifact_sha": "d" * 64}}
        result = {"row": row, "event": {"event_id": "promotion-event", "candidate_id": payload["candidate_id"],
                                       "row_id": row["row_id"], "reason": payload["reason"]}}
        self.promotions[payload["candidate_id"]] = result
        self.promotion_requests[payload["request_id"]] = (dict(payload), result)
        return result

    def run_envelope(self, run_id, policy_context):
        self.calls.append(("run_envelope", run_id, policy_context))
        return {"status": "needs_attention", "conditions": ["live_disabled_zero_budget"],
                "dispatch_performed": False, "runtime_verified": False}


def run_case(scenario):
    """Called only by future pytest execution; imports no network client globally."""
    import httpx
    domain = FakeApplication()
    authority = LocalSessionAuthority()
    app = create_app(domain, port=25001, authority=authority)
    async def exercise():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1000))
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN,
                                     headers={"accept": "application/json"}) as client:
            await scenario(client, domain, authority)
    asyncio.run(exercise())


async def login(client, domain, authority):
    credential = authority.issue_login_challenge(ReviewerIdentity(domain.reviewer_subject, frozenset({"owned"})))
    csrf = (await client.get("/login")).json()["data"]["csrf_token"]
    response = await client.post("/login", headers={"origin": ORIGIN}, json={"credential": credential, "csrf_token": csrf})
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=900" in cookie
    return (await client.get("/")).json()["data"]["csrf_token"]


def decision(csrf):
    return {"action": "defer", "expected_assessment_generation": "b" * 64,
            "expected_row_revision": "c" * 64, "request_id": "test-request", "csrf_token": csrf}


def test_authentication_denied_by_default_and_cross_run_grants_enforced():
    """what_bug_this_catches: local requests or a valid cookie grant every run/store object."""
    async def case(client, domain, authority):
        assert (await client.get("/runs/owned")).status_code == 401
        await login(client, domain, authority)
        domain.calls.clear()
        assert (await client.get("/runs/not-granted")).status_code == 404
        assert not domain.calls
        visible = (await client.get("/")).json()["data"]["runs"]
        assert [item["run_id"] for item in visible] == ["owned"]
    run_case(case)


def test_csrf_origin_and_logout_apply_to_every_mutation():
    """what_bug_this_catches: a mutation/logout succeeds from a cross-site or tokenless form."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        path = "/runs/owned/rows/row-one/decide"
        assert (await client.post(path, json=decision(csrf))).status_code == 403
        assert (await client.post(path, headers={"origin": "http://evil.test"}, json=decision(csrf))).status_code == 403
        assert (await client.post(path, headers={"origin": ORIGIN}, json=decision("wrong"))).status_code == 403
        response = await client.post(path, headers={"origin": ORIGIN}, json=decision(csrf))
        assert response.status_code == 200 and response.json()["status"] == "committed"
        assert (await client.post("/logout", headers={"origin": ORIGIN}, json={"csrf_token": "wrong"})).status_code == 403
        assert (await client.post("/logout", headers={"origin": ORIGIN}, json={"csrf_token": csrf})).status_code == 303
        assert (await client.get("/runs/owned")).status_code == 401
    run_case(case)


@pytest.mark.parametrize("field,value", [("actor", "model-actor"), ("path", "untrusted"),
    ("provider_config", {}), ("expected_generation", "noncanonical-alias")])
def test_closed_mutation_rejects_actor_path_and_configuration_injection(field, value):
    """what_bug_this_catches: untrusted HTTP/model fields reach actor/path/provider authority."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        payload = {**decision(csrf), field: value}
        response = await client.post("/runs/owned/rows/row-one/decide", headers={"origin": ORIGIN}, json=payload)
        assert response.status_code == 422
        assert not any(call[0] == "decide" for call in domain.calls)
    run_case(case)


def test_body_and_duplicate_field_limits_fail_before_domain_mutation():
    """what_bug_this_catches: oversize/duplicate bodies bypass the closed parser and overwrite intent."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        path = "/runs/owned/rows/row-one/decide"
        headers = {"origin": ORIGIN, "content-type": "application/json"}
        assert (await client.post(path, headers=headers, content=b"x" * 65537)).status_code == 413
        assert (await client.post(path, headers=headers, content='{"action":"defer","action":"reject"}')).status_code == 422
        nested = b"[" * 1100 + b"0" + b"]" * 1100
        assert (await client.post(path, headers=headers, content=nested)).status_code == 422
        invalid_formats = {"request_id": "invalid-format", "expected_matrix_generation": "a" * 64,
                           "csrf_token": csrf, "formats": [{}]}
        assert (await client.post("/runs/owned/export", headers={"origin": ORIGIN}, json=invalid_formats)).status_code == 422
        assert not any(call[0] == "decide" for call in domain.calls)
    run_case(case)


def test_form_boolean_and_page_conversion_preserve_exact_domain_fields():
    """what_bug_this_catches: checkbox strings or one-based display pages alter domain decisions."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        payload = {"csrf_token": csrf, "page_index": "0", "action": "accept_blank",
            "expected_artifact_sha": "d" * 64, "expected_page_hash": "e" * 64,
            "expected_matrix_generation": "a" * 64, "expected_coverage_revision": "",
            "request_id": "inspect-test", "reason": "synthetic blank inspected", "inspected_original": "on"}
        response = await client.post("/runs/owned/coverage/inspect", headers={"origin": ORIGIN}, data=payload)
        assert response.status_code == 200
        sent = next(call[2] for call in domain.calls if call[0] == "inspect")
        assert sent["page_index"] == 0 and sent["inspected_original"] is True
        assert sent["expected_coverage_revision"] is None and "actor" not in sent
    run_case(case)


def test_download_is_committed_allowlisted_and_historical_not_silently_current():
    """what_bug_this_catches: arbitrary file paths or historical/unverified exports look current."""
    async def case(client, domain, authority):
        await login(client, domain, authority)
        path = "/runs/owned/exports/request-one/"
        assert (await client.get(path + "auth.json")).status_code == 404
        assert not any(call[0] == "download" for call in domain.calls)
        inventory = await client.get(path + "inventory-audit.json")
        assert inventory.status_code == 200
        assert inventory.headers["content-type"].startswith("application/json")
        assert (await client.get(path + "other-audit.json")).status_code == 404
        domain.historical = True
        historical = await client.get(path + "matrix.html")
        assert historical.status_code == 200
        assert "HISTORICAL-matrix.html" in historical.headers["content-disposition"]
        assert historical.headers["x-m3-currentness"] == "historical"
        assert "sandbox" in historical.headers["content-security-policy"]
        domain.bad_hash = True
        assert (await client.get(path + "matrix.html")).status_code == 409
        domain.bad_hash, domain.unavailable_export = False, True
        assert (await client.get(path + "matrix.html")).status_code == 503
    run_case(case)


def test_source_context_race_returns_conflict_without_mixing_revisions():
    """what_bug_this_catches: row revision and source context from different commits are shown together."""
    async def case(client, domain, authority):
        await login(client, domain, authority)
        domain.conflicting_context = True
        response = await client.get("/runs/owned/rows/row-one")
        assert response.status_code == 409
        assert response.json()["data"]["code"] == "view_generation_changed"
    run_case(case)


def mapping_payload(csrf):
    return {"csrf_token": csrf, "relation": "one_to_one",
        "old_rows": [{"row_id": "old-row", "row_revision": "e" * 64}],
        "new_rows": [{"row_id": "new-row", "row_revision": "f" * 64}],
        "expected_old_matrix_generation": "a" * 64, "expected_matrix_generation": "b" * 64,
        "expected_old_assessment_generation": "a" * 64, "expected_new_assessment_generation": "b" * 64,
        "expected_old_artifact_sha": "a" * 64, "expected_new_artifact_sha": "b" * 64,
        "expected_correspondence_revision": None,
        "request_id": "mapping-request", "reason": "Human compares the two registered clauses."}


def test_amendment_read_is_scoped_and_query_has_no_path_or_cross_run_selector():
    """what_bug_this_catches: amendment history leaks other runs or accepts arbitrary source paths."""
    async def case(client, domain, authority):
        assert (await client.get("/runs/owned/amendments")).status_code == 401
        await login(client, domain, authority)
        domain.calls.clear()
        assert (await client.get("/runs/not-granted/amendments")).status_code == 404
        assert not domain.calls
        assert (await client.get("/runs/owned/amendments?previous_matrix_generation=not-a-hash")).status_code == 422
        assert (await client.get("/runs/owned/amendments?source_path=untrusted")).status_code == 422
        response = await client.get("/runs/owned/amendments?previous_matrix_generation=" + "a" * 64)
        assert response.status_code == 200
        assert response.json()["data"]["approval_carry_allowed"] is False
    run_case(case)


def test_mapping_csrf_cross_run_and_actor_span_approval_injection_are_denied():
    """what_bug_this_catches: human mapping forms bypass session/CSRF or smuggle an approval/actor/span."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        path = "/runs/owned/amendments/correspondence"
        payload = mapping_payload(csrf)
        assert (await client.post(path, json=payload)).status_code == 403
        assert (await client.post(path, headers={"origin": ORIGIN}, json=mapping_payload("wrong"))).status_code == 403
        assert (await client.post("/runs/not-granted/amendments/correspondence", headers={"origin": ORIGIN}, json=payload)).status_code == 404
        for field in ["actor", "approval", "source_span", "answer_text", "provider_config", "comparison_id"]:
            assert (await client.post(path, headers={"origin": ORIGIN}, json={**payload, field: "untrusted"})).status_code == 422
        assert not domain.correspondence_events
        assert not any(call[0] == "decide" for call in domain.calls)
    run_case(case)


def test_stale_and_unknown_registered_mapping_references_preserve_conflict():
    """what_bug_this_catches: stale/current-generation or fabricated row references become confirmed lineage."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        path, headers = "/runs/owned/amendments/correspondence", {"origin": ORIGIN}
        stale = {**mapping_payload(csrf), "expected_matrix_generation": "9" * 64}
        assert (await client.post(path, headers=headers, json=stale)).status_code == 409
        unknown = {**mapping_payload(csrf), "old_rows": [{"row_id": "unknown", "row_revision": "e" * 64}]}
        assert (await client.post(path, headers=headers, json=unknown)).status_code == 409
        wrong_revision = {**mapping_payload(csrf), "new_rows": [{"row_id": "new-row", "row_revision": "9" * 64}]}
        assert (await client.post(path, headers=headers, json=wrong_revision)).status_code == 409
        assert not domain.correspondence_events
        assert domain.approvals == {"old-row": "approved_positive", "new-row": "pending"}
    run_case(case)


def test_correspondence_is_separate_idempotent_lineage_never_approval_carry():
    """what_bug_this_catches: accepting a lexical correspondence silently approves new-generation answers."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        path, headers = "/runs/owned/amendments/correspondence", {"origin": ORIGIN}
        payload = mapping_payload(csrf)
        first = await client.post(path, headers=headers, json=payload)
        repeated = await client.post(path, headers=headers, json=payload)
        assert first.status_code == repeated.status_code == 200
        assert first.json()["status"] == "correspondence_recorded"
        assert first.json()["decision_changed"] is False and first.json()["approval_carry_allowed"] is False
        assert first.json()["data"]["event_id"] == repeated.json()["data"]["event_id"]
        assert len(domain.correspondence_events) == 1
        assert domain.approvals == {"old-row": "approved_positive", "new-row": "pending"}
        assert not any(call[0] == "decide" for call in domain.calls)
        assert (await client.post(path, headers=headers, json={**payload, "reason": "Changed replay payload"})).status_code == 409
        next_payload = {**payload, "request_id": "mapping-next"}
        assert (await client.post(path, headers=headers, json=next_payload)).status_code == 409
        next_payload["expected_correspondence_revision"] = domain.correspondence_revision
        assert (await client.post(path, headers=headers, json=next_payload)).status_code == 200
        assert len(domain.correspondence_events) == 2
        assert domain.approvals == {"old-row": "approved_positive", "new-row": "pending"}
    run_case(case)


def test_closed_mapping_form_references_and_relation_bounds():
    """what_bug_this_catches: form JSON accepts raw spans, duplicates or split relations with one output."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        path, headers = "/runs/owned/amendments/correspondence", {"origin": ORIGIN}
        payload = mapping_payload(csrf)
        assert (await client.post(path, headers=headers, json={**payload, "relation": "split"})).status_code == 422
        assert (await client.post(path, headers=headers, json={**payload, "old_rows": payload["old_rows"] * 2})).status_code == 422
        supplied = [{**payload["old_rows"][0], "quote": "Arbitrary source text"}]
        assert (await client.post(path, headers=headers, json={**payload, "old_rows": supplied})).status_code == 422
        form = {**payload, "old_rows": json.dumps(payload["old_rows"]), "new_rows": json.dumps(payload["new_rows"])}
        assert (await client.post(path, headers=headers, data=form)).status_code == 200
    run_case(case)


def promotion_payload(csrf):
    return {"csrf_token": csrf, "candidate_id": "candidate-one", "expected_artifact_sha": "d" * 64,
            "expected_assessment_generation": "b" * 64, "expected_matrix_generation": "a" * 64,
            "request_id": "promotion-request", "reason": "Human retains a source-linked obligation."}


def test_inventory_read_and_promotion_require_grant_origin_csrf_and_closed_fields():
    """what_bug_this_catches: inventory correction bypasses ownership/CSRF or accepts source/actor/approval input."""
    async def case(client, domain, authority):
        assert (await client.get("/runs/owned/inventory")).status_code == 401
        csrf = await login(client, domain, authority)
        assert (await client.get("/runs/not-granted/inventory")).status_code == 404
        path, payload = "/runs/owned/inventory/promote", promotion_payload(csrf)
        assert (await client.post(path, json=payload)).status_code == 403
        assert (await client.post(path, headers={"origin": ORIGIN}, json=promotion_payload("wrong"))).status_code == 403
        assert (await client.post("/runs/not-granted/inventory/promote", headers={"origin": ORIGIN}, json=payload)).status_code == 404
        for field in ["actor", "source_span", "condition", "approval", "provider_config", "human_validated"]:
            assert (await client.post(path, headers={"origin": ORIGIN}, json={**payload, field: "untrusted"})).status_code == 422
        assert not domain.promotions
    run_case(case)


def test_inventory_unknown_stale_and_duplicate_are_visible_and_do_not_approve():
    """what_bug_this_catches: arbitrary/stale/duplicate candidates become approved rows or disappear as503."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        path, headers, payload = "/runs/owned/inventory/promote", {"origin": ORIGIN}, promotion_payload(csrf)
        assert (await client.post(path, headers=headers, json={**payload, "candidate_id": "not-registered"})).status_code == 404
        assert (await client.post(path, headers=headers, json={**payload, "expected_matrix_generation": "9" * 64})).status_code == 409
        first = await client.post(path, headers=headers, json=payload)
        repeated = await client.post(path, headers=headers, json=payload)
        assert first.status_code == repeated.status_code == 200
        assert first.json()["status"] == "pending_requirement_created"
        assert first.json()["data"]["row"]["approval"] == "pending"
        assert first.json()["data"]["row"]["decision_event_id"] is None
        assert first.json()["promotion_confers_approval"] is False
        assert len(domain.promotions) == 1
        assert (await client.post(path, headers=headers, json={**payload, "request_id": "another-request"})).status_code == 409
        assert (await client.post(path, headers=headers, json={**payload, "reason": "Changed retry reason"})).status_code == 409
        assert domain.approvals == {"old-row": "approved_positive", "new-row": "pending"}
        assert not any(call[0] == "decide" for call in domain.calls)
    run_case(case)


def test_human_promotion_preserves_machine_audit_and_typed_independent_overrides():
    """what_bug_this_catches: a human correction rewrites blind predictions or collapses independent flags."""
    async def case(client, domain, authority):
        csrf = await login(client, domain, authority)
        before = (await client.get("/runs/owned/inventory")).json()["data"]
        assert before["counts"]["rejected"] == 1 and before["all_requirements_found"] is None
        payload = {**promotion_payload(csrf), "mandatory": "off", "conditional": "on", "administrative": "",
                   "interpretation": "Human interpretation", "applicability": "unknown"}
        response = await client.post("/runs/owned/inventory/promote", headers={"origin": ORIGIN}, data=payload)
        assert response.status_code == 200
        forwarded = next(call[2] for call in domain.calls if call[0] == "promote_inventory_candidate")
        assert forwarded["mandatory"] is False and forwarded["conditional"] is True
        assert forwarded["administrative"] is None and "actor" not in forwarded and "csrf_token" not in forwarded
        after = (await client.get("/runs/owned/inventory")).json()["data"]
        assert after["counts"]["rejected"] == 1
        assert after["entries"][0]["already_promoted"] and not after["entries"][0]["eligible"]
        assert after["promotions"][0]["confers_approval"] is False
    run_case(case)


def test_preflight_is_read_only_and_request_cannot_supply_live_policy():
    """what_bug_this_catches: a diagnostic GET/POST becomes a policy or paid-provider enable endpoint."""
    async def case(client, domain, authority):
        await login(client, domain, authority)
        assert (await client.get("/runs/not-granted/preflight")).status_code == 404
        assert (await client.get("/runs/owned/preflight?enabled=true")).status_code == 422
        assert (await client.post("/runs/owned/preflight", headers={"origin": ORIGIN}, json={"enabled": True})).status_code == 405
        response = await client.get("/runs/owned/preflight")
        assert response.status_code == 200 and response.json()["status"] == "preview_only"
        assert not response.json()["dispatch_performed"] and not response.json()["creates_authorization"]
        assert next(call[2] for call in domain.calls if call[0] == "run_envelope") == {}
    run_case(case)
