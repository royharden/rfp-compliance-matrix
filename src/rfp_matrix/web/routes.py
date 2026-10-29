"""Closed local routes; domain services alone resolve actors/decisions/exports."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import secrets
from typing import Any
from urllib.parse import parse_qsl

from .security import WebError, safe_id

COOKIE = "m3_reviewer_session"
PREAUTH_COOKIE = "m3_reviewer_login"
DECISION_FIELDS = {"action", "expected_assessment_generation", "expected_row_revision",
                   "request_id", "reason", "answer_text", "human_validated"}
COVERAGE_FIELDS = {"page_index", "action", "expected_artifact_sha", "expected_page_hash",
    "expected_matrix_generation", "expected_coverage_revision", "request_id", "reason", "inspected_original"}
EXPORT_FIELDS = {"request_id", "expected_matrix_generation", "formats"}
PROMOTION_REQUIRED = {"candidate_id", "expected_artifact_sha", "expected_assessment_generation",
                      "expected_matrix_generation", "request_id", "reason"}
PROMOTION_OPTIONAL = {"interpretation", "mandatory", "conditional", "administrative", "applicability"}
CORRESPONDENCE_FIELDS = {"relation", "old_rows", "new_rows", "expected_old_matrix_generation",
    "expected_matrix_generation", "expected_old_assessment_generation", "expected_new_assessment_generation",
    "expected_old_artifact_sha", "expected_new_artifact_sha", "expected_correspondence_revision", "request_id", "reason"}
DOWNLOAD_TYPES = {"matrix.html": "text/html", "matrix.csv": "text/csv", "matrix.md": "text/markdown",
                  "coverage.json": "application/json", "manifest.json": "application/json",
                  "inventory-audit.json": "application/json"}


async def closed_payload(request, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if request.query_params:
        raise WebError("query_not_allowed", 422)
    body = await request.body()
    if len(body) > 65536:
        raise WebError("body_limit", 413)
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise WebError("duplicate_field", 422)
            value[key] = item
        return value
    try:
        if media == "application/json":
            value = json.loads(body, object_pairs_hook=unique, parse_constant=lambda value: (_ for _ in ()).throw(WebError("invalid_number", 422)))
        elif media == "application/x-www-form-urlencoded":
            value = unique(parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True,
                                     encoding="utf-8", errors="strict", max_num_fields=20))
        else:
            raise WebError("unsupported_content_type", 415)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, WebError):
            raise
        raise WebError("invalid_payload", 422) from exc
    if not isinstance(value, dict) or set(value) - (allowed | {"csrf_token"}) or required - set(value):
        raise WebError("closed_payload_required", 422)
    for key, item in value.items():
        if key in {"human_validated", "inspected_original", "mandatory", "conditional", "administrative"}:
            if key in {"mandatory", "conditional", "administrative"} and item in (None, ""):
                value[key] = None
                continue
            if isinstance(item, str) and item in {"true", "on", "false", "off"}:
                item = item in {"true", "on"}
                value[key] = item
            if type(item) is not bool:
                raise WebError("invalid_boolean", 422)
        elif key == "page_index":
            if isinstance(item, str) and item.isascii() and item.isdigit():
                item = int(item)
                value[key] = item
            if type(item) is not int or not 0 <= item < 100:
                raise WebError("invalid_page", 422)
        elif key == "formats":
            if isinstance(item, str):
                item = item.split(",")
                value[key] = item
            if not isinstance(item, list) or not item or len(item) > 3 or any(not isinstance(part, str) or part not in {"html", "csv", "md"} for part in item):
                raise WebError("invalid_export_formats", 422)
        elif key in {"old_rows", "new_rows"}:
            if isinstance(item, str):
                try:
                    item = json.loads(item, object_pairs_hook=unique)
                except (ValueError, RecursionError) as exc:
                    raise WebError("invalid_row_references", 422) from exc
            if not isinstance(item, list) or len(item) > 50:
                raise WebError("invalid_row_references", 422)
            seen = set()
            for row in item:
                if not isinstance(row, dict) or set(row) != {"row_id", "row_revision"}:
                    raise WebError("registered_row_references_required", 422)
                identity = safe_id(row["row_id"])
                if not isinstance(row["row_revision"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["row_revision"]):
                    raise WebError("exact_row_revision_required", 422)
                if identity in seen:
                    raise WebError("duplicate_row_reference", 422)
                seen.add(identity)
            value[key] = item
        elif key in {"expected_coverage_revision", "expected_correspondence_revision"} and item in (None, ""):
            value[key] = None
        elif not isinstance(item, str) or len(item) > (20000 if key in {"answer_text", "interpretation"} else 8000 if key == "reason" else 512):
            raise WebError("invalid_field", 422)
    return value


def install_routes(app, *, application, authority, templates):
    # Optional imports happen only when the explicitly invoked factory builds an
    # app. Assign concrete Request annotations before FastAPI route introspection.
    from fastapi import Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
    from jinja2 import TemplateNotFound, UndefinedError

    def response(request, template: str, vm: dict[str, Any], *, session=None, status=200, **extra):
        if "application/json" in request.headers.get("accept", ""):
            data = dict(vm)
            data["csrf_token"] = session.csrf_token if session else extra.get("csrf_token", "")
            return JSONResponse({"schema_version": 1, "status": "ok" if status < 400 else "error", "data": data}, status_code=status)
        context = {"request": request, "vm": vm, "csrf_token": session.csrf_token if session else extra.pop("csrf_token", ""),
                   "request_id": secrets.token_hex(16), "runtime_unverified": True, **extra}
        try:
            return HTMLResponse(templates.get_template(template).render(**context), status_code=status)
        except (TemplateNotFound, UndefinedError):
            return JSONResponse({"schema_version": 1, "status": "unavailable", "error": {"code": "reviewer_template_unavailable"}}, status_code=503)

    def session_for(request, run_id=None):
        session = authority.session(request.cookies.get(COOKIE))
        if session.identity.subject != getattr(application, "reviewer_subject", None):
            raise WebError("application_reviewer_not_bound", 503)
        if run_id is not None:
            authority.authorize(session, run_id)
        return session

    async def call(name, *args, **kwargs):
        method = getattr(application, name, None)
        if method is None:
            raise WebError("application_operation_unavailable", 503)
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except WebError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "application_unavailable")
            if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,100}", code):
                code = "application_unavailable"
            status = 409 if any(word in code for word in ["conflict", "stale", "generation", "revision", "duplicate", "historical", "corrupt"]) else 404 if any(word in code for word in ["unknown", "not_found"]) else 422 if any(word in code for word in ["invalid", "limit", "unsupported"]) else 503
            raise WebError(code, status) from None

    def present(kind, payload):
        # presentation.py owns this closed seam. No document-controlled function
        # name or template is evaluated; missing builders fail visibly.
        try:
            from . import presentation
            if kind == "runs":
                return {"schema_version": 1, "kind": "runs", "runs": payload["runs"],
                        "fictional": True, "runtime_unverified": True}
            if kind == "matrix":
                return presentation.matrix_view(payload["run"], queue=payload["queue"])
            if kind == "row":
                selected = payload["row"]
                return presentation.row_view(selected["snapshot"], selected["row"],
                    context=payload["context"], coverage=selected["snapshot"].get("coverage"))
            if kind == "coverage":
                summary = payload["coverage"]
                return {**presentation.coverage_view(summary), "run_id": payload["run_id"],
                        "matrix_generation": summary.get("matrix_generation"), "fictional": True}
            if kind == "amendments":
                return presentation.amendments_view(payload)
            if kind == "inventory":
                return presentation.inventory_audit_view(payload)
            raise WebError("unknown_presentation", 503)
        except (ImportError, AttributeError):
            raise WebError("presentation_unavailable", 503) from None

    async def error_handler(request, exc):
        return response(request, "error.html", {"code": exc.code, "http_status": exc.status}, status=exc.status)
    app.add_exception_handler(WebError, error_handler)

    async def login_get(request):
        if request.query_params:
            raise WebError("query_not_allowed", 422)
        csrf = authority.issue_preauth()
        result = response(request, "login.html", {"login_required": True}, csrf_token=csrf)
        result.set_cookie(PREAUTH_COOKIE, csrf, max_age=authority.challenge_seconds,
                          httponly=True, samesite="strict", secure=False, path="/")
        return result

    async def login_post(request):
        payload = await closed_payload(request, {"credential"}, {"credential", "csrf_token"})
        token, session = authority.login(payload["credential"], cookie_csrf=request.cookies.get(PREAUTH_COOKIE, ""), form_csrf=payload["csrf_token"])
        if session.identity.subject != getattr(application, "reviewer_subject", None):
            authority.logout(token)
            raise WebError("application_reviewer_not_bound", 503)
        result = RedirectResponse("/", status_code=303)
        result.delete_cookie(PREAUTH_COOKIE, path="/")
        result.set_cookie(COOKIE, token, max_age=authority.session_seconds, httponly=True,
                          samesite="strict", secure=False, path="/")
        return result

    async def logout(request):
        session = session_for(request)
        payload = await closed_payload(request, set(), {"csrf_token"})
        authority.verify_csrf(session, payload["csrf_token"])
        authority.logout(request.cookies[COOKIE])
        result = RedirectResponse("/login", status_code=303)
        result.delete_cookie(COOKIE, path="/")
        return result

    async def index(request):
        if request.query_params:
            raise WebError("query_not_allowed", 422)
        try:
            session = session_for(request)
        except WebError as exc:
            if exc.status == 401 and "application/json" not in request.headers.get("accept", ""):
                return RedirectResponse("/login", status_code=303)
            raise
        runs = await call("list_runs")
        if isinstance(runs, dict):
            runs = runs.get("runs", [])
        visible = [run for run in runs if isinstance(run, dict) and run.get("run_id") in session.identity.run_ids]
        return response(request, "runs.html", present("runs", {"runs": visible}), session=session)

    async def matrix(request):
        run_id = safe_id(request.path_params["run_id"])
        session = session_for(request, run_id)
        if set(request.query_params) - {"queue"} or len(request.query_params.getlist("queue")) > 1:
            raise WebError("invalid_query", 422)
        queue_name = request.query_params.get("queue", "all")
        if queue_name not in {"all", "pending", "approved_positive", "approved_gap", "rejected", "stale", "retired", "deferred", "not_applies", "unresolved"}:
            raise WebError("invalid_queue", 422)
        run = await call("get_run", run_id)
        # Application get_run binds rows and coverage to one SQLite read transaction.
        return response(request, "matrix.html", present("matrix", {"run": run, "queue": queue_name}), session=session)

    async def row(request):
        if request.query_params:
            raise WebError("query_not_allowed", 422)
        run_id, row_id = safe_id(request.path_params["run_id"]), safe_id(request.path_params["row_id"])
        session = session_for(request, run_id)
        data = await call("get_row", run_id, row_id)
        context = await call("source_context", run_id, row_id)
        if (context.get("row_revision") != data["row"].get("row_revision")
                or context.get("matrix_generation") != data["snapshot"].get("matrix_generation")):
            raise WebError("view_generation_changed", 409)
        return response(request, "row.html", present("row", {"run_id": run_id, "row": data, "context": context}), session=session)

    async def coverage_view(request):
        if request.query_params:
            raise WebError("query_not_allowed", 422)
        run_id = safe_id(request.path_params["run_id"])
        session = session_for(request, run_id)
        data = await call("coverage", run_id)
        return response(request, "coverage.html", present("coverage", {"run_id": run_id, "coverage": data}), session=session)

    async def mutation(request, operation, allowed, required):
        run_id = safe_id(request.path_params["run_id"])
        session = session_for(request, run_id)
        payload = await closed_payload(request, allowed, required | {"csrf_token"})
        authority.verify_csrf(session, payload.pop("csrf_token"))
        safe_id(payload["request_id"])
        if operation == "decide":
            row_id = safe_id(request.path_params["row_id"])
            if payload["action"] not in {"edit", "approve_positive", "accept_gap", "reject", "defer", "not_applies"}:
                raise WebError("invalid_action", 422)
            result = await call(operation, run_id, row_id, payload)
        else:
            result = await call(operation, run_id, payload)
        if isinstance(result, dict) and result.get("status") in {"unavailable", "pending", "conflict", "error"}:
            status = {"unavailable": 503, "pending": 202, "conflict": 409, "error": 422}[result["status"]]
            return response(request, "error.html", {"code": result["status"], "result": result}, session=session, status=status)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"schema_version": 1, "status": "committed", "data": result})
        if operation == "export":
            return RedirectResponse(f"/runs/{run_id}/exports", status_code=303)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    async def decide(request):
        return await mutation(request, "decide", DECISION_FIELDS,
                              {"action", "expected_assessment_generation", "expected_row_revision", "request_id"})

    async def inspect_coverage(request):
        return await mutation(request, "inspect_coverage", COVERAGE_FIELDS, COVERAGE_FIELDS - {"inspected_original"})

    async def export(request):
        return await mutation(request, "export", EXPORT_FIELDS, {"request_id", "expected_matrix_generation"})

    def artifact_response(artifact, filename):
        if not isinstance(artifact, dict):
            raise WebError("unverified_export_artifact", 409)
        content = artifact.get("content")
        current = artifact.get("current", artifact.get("current_at_read"))
        if (not isinstance(content, bytes) or len(content) > 10 * 1024 * 1024
                or artifact.get("filename") != filename or type(current) is not bool
                or ("sha256" in artifact and artifact["sha256"] != hashlib.sha256(content).hexdigest())):
            raise WebError("unverified_export_artifact", 409)
        historical = not current
        name = ("HISTORICAL-" if historical else "") + filename
        return Response(content, media_type=DOWNLOAD_TYPES[filename], headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-M3-Currentness": "historical" if historical else "current-at-read",
            "Content-Security-Policy": "sandbox; default-src 'none'"})

    async def download(request):
        run_id, request_id = safe_id(request.path_params["run_id"]), safe_id(request.path_params["request_id"])
        session_for(request, run_id)
        filename = request.path_params["filename"]
        if filename not in DOWNLOAD_TYPES or request.query_params:
            raise WebError("unknown_artifact", 404)
        artifact = await call("download_export", run_id, request_id, filename)
        if not isinstance(artifact, dict) or "sha256" not in artifact:
            raise WebError("unverified_export_artifact", 409)
        return artifact_response(artifact, filename)

    async def export_history(request):
        run_id = safe_id(request.path_params["run_id"])
        session = session_for(request, run_id)
        if request.query_params:
            raise WebError("query_not_allowed", 422)
        history = await call("export_history", run_id)
        entries = []
        for item in history.get("exports", [])[:200]:
            export_id = safe_id(item["export_id"])
            if not re.fullmatch(r"X-[0-9a-f]{32}", export_id):
                raise WebError("invalid_export_identity", 409)
            files = [name for name in item.get("files", []) if name in DOWNLOAD_TYPES]
            entries.append({"export_id": export_id, "created_at": item.get("created_at"),
                "state": item.get("state"), "matrix_generation": item.get("matrix_generation"),
                "historical": item.get("historical") is True,
                "downloads": [{"filename": name, "url": f"/runs/{run_id}/export-files/{export_id}/{name}"}
                              for name in files] if item.get("state") == "published" else []})
        return response(request, "exports.html", {"run_id": run_id, "exports": entries,
            "matrix_generation": history.get("matrix_generation"), "fictional": True}, session=session)

    async def export_file(request):
        run_id = safe_id(request.path_params["run_id"])
        session_for(request, run_id)
        export_id, filename = safe_id(request.path_params["export_id"]), request.path_params["filename"]
        if not re.fullmatch(r"X-[0-9a-f]{32}", export_id) or filename not in DOWNLOAD_TYPES or request.query_params:
            raise WebError("unknown_artifact", 404)
        artifact = await call("export_file", run_id, export_id, filename, allow_historical=True)
        return artifact_response(artifact, filename)

    async def amendments(request):
        run_id = safe_id(request.path_params["run_id"])
        session = session_for(request, run_id)
        if set(request.query_params) - {"previous_matrix_generation"} or len(request.query_params.getlist("previous_matrix_generation")) > 1:
            raise WebError("invalid_amendment_query", 422)
        previous = request.query_params.get("previous_matrix_generation")
        if previous is not None and not re.fullmatch(r"[0-9a-f]{64}", previous):
            raise WebError("exact_previous_generation_required", 422)
        report = await call("amendments", run_id, previous_matrix_generation=previous)
        view = present("amendments", report)
        view.update(proposals_not_approvals=True, approval_carry_allowed=False)
        return response(request, "amendments.html", view, session=session)

    async def correspondence(request):
        run_id = safe_id(request.path_params["run_id"])
        session = session_for(request, run_id)
        payload = await closed_payload(request, CORRESPONDENCE_FIELDS, CORRESPONDENCE_FIELDS | {"csrf_token"})
        authority.verify_csrf(session, payload.pop("csrf_token"))
        safe_id(payload["request_id"])
        if not payload["reason"].strip():
            raise WebError("correspondence_reason_required", 422)
        for name in CORRESPONDENCE_FIELDS:
            if name == "expected_correspondence_revision" and payload[name] is None:
                continue
            if name.startswith("expected_") and not re.fullmatch(r"[0-9a-f]{64}", payload[name]):
                raise WebError("exact_correspondence_generations_required", 422)
        old_count, new_count = len(payload["old_rows"]), len(payload["new_rows"])
        relation = payload["relation"]
        valid = ((relation == "one_to_one" and old_count == new_count == 1)
            or (relation == "split" and old_count == 1 and new_count >= 2)
            or (relation == "merge" and old_count >= 2 and new_count == 1)
            or (relation == "added" and old_count == 0 and new_count >= 1)
            or (relation == "removed" and old_count >= 1 and new_count == 0))
        if not valid:
            raise WebError("invalid_correspondence_relation", 422)
        # The review application owns atomic membership/CAS/history/idempotency and actor resolution.
        # This operation never calls decide(), changes source text or reapproves.
        result = await call("record_correspondence", run_id, payload)
        if isinstance(result, dict) and result.get("status") in {"conflict", "unavailable", "pending", "error"}:
            status = {"conflict": 409, "unavailable": 503, "pending": 202, "error": 422}[result["status"]]
            return response(request, "error.html", {"code": result["status"], "result": result}, session=session, status=status)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"schema_version": 1, "status": "correspondence_recorded",
                "data": result, "approval_carry_allowed": False, "decision_changed": False})
        return RedirectResponse(f"/runs/{run_id}/amendments?previous_matrix_generation={payload['expected_old_matrix_generation']}", status_code=303)

    async def inventory(request):
        run_id = safe_id(request.path_params["run_id"])
        session = session_for(request, run_id)
        if request.query_params:
            raise WebError("query_not_allowed", 422)
        audit = await call("inventory_audit", run_id)
        return response(request, "inventory.html", present("inventory", audit), session=session)

    async def promote_inventory(request):
        run_id = safe_id(request.path_params["run_id"])
        session = session_for(request, run_id)
        payload = await closed_payload(request, PROMOTION_REQUIRED | PROMOTION_OPTIONAL,
                                       PROMOTION_REQUIRED | {"csrf_token"})
        authority.verify_csrf(session, payload.pop("csrf_token"))
        safe_id(payload["candidate_id"])
        safe_id(payload["request_id"])
        for name in ["expected_artifact_sha", "expected_assessment_generation", "expected_matrix_generation"]:
            if not re.fullmatch(r"[0-9a-f]{64}", payload[name]):
                raise WebError("exact_promotion_versions_required", 422)
        if not payload["reason"].strip() or ("interpretation" in payload and not payload["interpretation"].strip()):
            raise WebError("promotion_reason_interpretation_required", 422)
        if "applicability" in payload and payload["applicability"] not in {"applies", "unknown", "not_applies"}:
            raise WebError("invalid_promotion_applicability", 422)
        # Registry ownership, duplicate/max-row prevention, CAS and actor/event
        # persistence are the application service's atomic authority. This route
        # never calls a provider or decide() and accepts no source span/approval.
        result = await call("promote_inventory_candidate", run_id, payload)
        if isinstance(result, dict) and result.get("status") in {"conflict", "unavailable", "pending", "error"}:
            status = {"conflict": 409, "unavailable": 503, "pending": 202, "error": 422}[result["status"]]
            return response(request, "error.html", {"code": result["status"], "result": result}, session=session, status=status)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"schema_version": 1, "status": "pending_requirement_created", "data": result,
                                 "resulting_approval": "pending", "promotion_confers_approval": False})
        return RedirectResponse(f"/runs/{run_id}/inventory", status_code=303)

    async def preflight(request):
        run_id = safe_id(request.path_params["run_id"])
        session_for(request, run_id)
        if request.query_params:
            raise WebError("preflight_policy_is_server_owned", 422)
        # Empty request context: the application supplies its own disabled policy
        # and deterministic registry/balance inputs. No HTTP enable/approval API.
        preview = await call("run_envelope", run_id, {})
        return JSONResponse({"schema_version": 1, "status": "preview_only", "data": preview,
                             "dispatch_performed": False, "creates_authorization": False})

    async def stylesheet(request):
        path = Path(__file__).parent / "static" / "reviewer.css"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 262144:
            raise WebError("stylesheet_unavailable", 404)
        content = await asyncio.to_thread(path.read_bytes)
        if len(content) > 262144:
            raise WebError("stylesheet_unavailable", 404)
        return Response(content, media_type="text/css")

    endpoints = [("/static/reviewer.css", stylesheet, ["GET"]), ("/login", login_get, ["GET"]), ("/login", login_post, ["POST"]),
        ("/logout", logout, ["POST"]), ("/", index, ["GET"]),
        ("/runs/{run_id}", matrix, ["GET"]), ("/runs/{run_id}/rows/{row_id}", row, ["GET"]),
        ("/runs/{run_id}/coverage", coverage_view, ["GET"]),
        ("/runs/{run_id}/rows/{row_id}/decide", decide, ["POST"]),
        ("/runs/{run_id}/coverage/inspect", inspect_coverage, ["POST"]),
        ("/runs/{run_id}/export", export, ["POST"]),
        ("/runs/{run_id}/exports", export_history, ["GET"]),
        ("/runs/{run_id}/amendments", amendments, ["GET"]),
        ("/runs/{run_id}/amendments/correspondence", correspondence, ["POST"]),
        ("/runs/{run_id}/inventory", inventory, ["GET"]),
        ("/runs/{run_id}/inventory/promote", promote_inventory, ["POST"]),
        ("/runs/{run_id}/preflight", preflight, ["GET"]),
        ("/runs/{run_id}/export-files/{export_id}/{filename}", export_file, ["GET"]),
        ("/runs/{run_id}/exports/{request_id}/{filename}", download, ["GET"])]
    for path, endpoint, methods in endpoints:
        endpoint.__annotations__ = {"request": Request}
        app.add_api_route(path, endpoint, methods=methods, response_model=None, name=endpoint.__name__)
