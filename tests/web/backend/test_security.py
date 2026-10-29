"""Unrun backend-only security source; no server/browser or external account.

Kept in the backend regression directory. These definitions do not
establish runtime browser/HTTP/cookie/security acceptance.
"""
import asyncio
from types import SimpleNamespace

import pytest

from rfp_matrix.web.security import (LocalBoundaryMiddleware, LocalSessionAuthority,
                                    ReviewerIdentity, WebError)
import rfp_matrix.web.security as security_module


def test_session_requires_one_time_local_capability_and_revokes_on_logout():
    """what_bug_this_catches: local access is mistaken for authenticated reviewer authority."""
    authority = LocalSessionAuthority()
    identity = ReviewerIdentity("domain-reviewer", frozenset({"run-one"}))
    credential = authority.issue_login_challenge(identity)
    nonce = authority.issue_preauth()
    token, session = authority.login(credential, cookie_csrf=nonce, form_csrf=nonce)
    assert authority.session(token).identity == identity
    authority.authorize(session, "run-one")
    with pytest.raises(WebError, match="not_found"):
        authority.authorize(session, "other-run")
    with pytest.raises(WebError, match="csrf_failed"):
        authority.verify_csrf(session, authority.issue_preauth())
    with pytest.raises(WebError, match="csrf_failed"):
        authority.verify_csrf(session, "\u00e9" * 32)
    next_nonce = authority.issue_preauth()
    with pytest.raises(WebError, match="authentication_failed"):
        authority.login(credential, cookie_csrf=next_nonce, form_csrf=next_nonce)
    authority.logout(token)
    with pytest.raises(WebError, match="authentication_required"):
        authority.session(token)


def test_session_expires_without_sliding_or_default_credentials(monkeypatch):
    """what_bug_this_catches: a session silently lasts forever or login bypasses issued capability."""
    clock = [0.0]
    monkeypatch.setattr(security_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    authority = LocalSessionAuthority(session_seconds=60)
    identity = ReviewerIdentity("domain-reviewer", frozenset({"run-one"}))
    nonce = authority.issue_preauth()
    credential = authority.issue_login_challenge(identity)
    token, _ = authority.login(credential, cookie_csrf=nonce, form_csrf=nonce)
    clock[0] = 61
    with pytest.raises(WebError, match="authentication_required"):
        authority.session(token)


def boundary_case(*, headers=None, body=b"", client="127.0.0.1", method="POST"):
    received, sent = [], []
    async def app(scope, receive, send):
        received.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    middleware = LocalBoundaryMiddleware(app, port=25001)  # Test-only value, never claimed/listened.
    pending = [{"type": "http.request", "body": body, "more_body": False}]
    async def receive():
        return pending.pop(0)
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "scheme": "http", "method": method, "client": (client, 1),
             "headers": headers if headers is not None else [(b"host", b"127.0.0.1:25001"),
                (b"origin", b"http://127.0.0.1:25001")]}
    asyncio.run(middleware(scope, receive, send))
    return received, sent


@pytest.mark.parametrize("headers", [
    [(b"host", b"evil.test:25001"), (b"origin", b"http://127.0.0.1:25001")],
    [(b"host", b"127.0.0.1:25001"), (b"origin", b"http://evil.test")],
    [(b"host", b"127.0.0.1:25001")],
    [(b"host", b"127.0.0.1:25001"), (b"origin", b"http://127.0.0.1:25001"), (b"x-forwarded-for", b"127.0.0.1")],
])
def test_rebinding_cross_origin_missing_origin_and_forwarded_headers_denied(headers):
    """what_bug_this_catches: loopback DNS/CORS/proxy assumptions allow cross-site mutation."""
    called, sent = boundary_case(headers=headers)
    assert not called and sent[0]["status"] == 403


def test_actual_stream_size_is_bounded_without_content_length():
    """what_bug_this_catches: chunked bodies bypass a Content-Length-only guard."""
    called, sent = boundary_case(body=b"x" * 65537)
    assert not called and sent[0]["status"] == 413


def test_valid_boundary_attaches_no_store_and_frame_denial_headers():
    """what_bug_this_catches: authenticated source/CSRF content is cached or framed."""
    called, sent = boundary_case()
    assert called and sent[0]["status"] == 200
    headers = dict(sent[0]["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"x-frame-options"] == b"DENY"
    assert b"form-action 'self'" in headers[b"content-security-policy"]
