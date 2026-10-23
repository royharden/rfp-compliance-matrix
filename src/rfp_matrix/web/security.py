"""Local capabilities, bounded sessions and ASGI boundary; no real credentials here."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import re
import secrets
import threading
import time
from typing import Any


class WebError(ValueError):
    def __init__(self, code: str, status: int = 403):
        self.code, self.status = code, status
        super().__init__(code)


def safe_id(value: Any) -> str:
    if not isinstance(value, str) or value in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value):
        raise WebError("invalid_object_id", 422)
    return value


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewerIdentity:
    subject: str
    run_ids: frozenset[str]

    def __post_init__(self):
        safe_id(self.subject)
        if not isinstance(self.run_ids, frozenset) or len(self.run_ids) > 1000:
            raise WebError("invalid_run_grants", 422)
        for run_id in self.run_ids:
            safe_id(run_id)


@dataclass(frozen=True)
class ReviewerSession:
    identity: ReviewerIdentity
    csrf_token: str = field(repr=False)
    expires_at: float


class LocalSessionAuthority:
    """Trusted caller issues one-time local login capabilities out of band.

    No default credential/authenticator or credential file read exists. Only hashes
    of login capabilities/cookies are kept. Restart revokes every in-memory session.
    Secret delivery is a trusted CLI/OS integration obligation, never a URL/log.
    """
    def __init__(self, *, session_seconds: int = 900, challenge_seconds: int = 120):
        if type(session_seconds) is not int or not 60 <= session_seconds <= 3600 or type(challenge_seconds) is not int or not 10 <= challenge_seconds <= 300:
            raise WebError("invalid_session_lifetime", 422)
        self.session_seconds, self.challenge_seconds = session_seconds, challenge_seconds
        self._sessions: dict[str, ReviewerSession] = {}
        self._challenges: dict[str, tuple[ReviewerIdentity, float]] = {}
        self._preauth: dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self):
        now = time.monotonic()
        self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > now}
        self._challenges = {key: value for key, value in self._challenges.items() if value[1] > now}
        self._preauth = {key: value for key, value in self._preauth.items() if value > now}

    def issue_login_challenge(self, identity: ReviewerIdentity) -> str:
        if not isinstance(identity, ReviewerIdentity):
            raise WebError("trusted_identity_required", 422)
        with self._lock:
            self._prune()
            if len(self._challenges) >= 32:
                raise WebError("challenge_capacity", 429)
            credential = secrets.token_urlsafe(32)
            self._challenges[_secret_hash(credential)] = (identity, time.monotonic() + self.challenge_seconds)
            return credential

    def issue_preauth(self) -> str:
        with self._lock:
            self._prune()
            if len(self._preauth) >= 128:
                raise WebError("login_capacity", 429)
            value = secrets.token_urlsafe(32)
            self._preauth[_secret_hash(value)] = time.monotonic() + self.challenge_seconds
            return value

    def login(self, credential: str, *, cookie_csrf: str, form_csrf: str) -> tuple[str, ReviewerSession]:
        if any(not isinstance(value, str) or not value.isascii() or not 20 <= len(value) <= 200 for value in [credential, cookie_csrf, form_csrf]):
            raise WebError("authentication_failed", 401)
        with self._lock:
            self._prune()
            if not secrets.compare_digest(cookie_csrf, form_csrf) or self._preauth.pop(_secret_hash(cookie_csrf), None) is None:
                raise WebError("csrf_failed")
            challenge = self._challenges.pop(_secret_hash(credential), None)
            if challenge is None:
                raise WebError("authentication_failed", 401)
            if len(self._sessions) >= 128:
                raise WebError("session_capacity", 429)
            token = secrets.token_urlsafe(32)
            session = ReviewerSession(challenge[0], secrets.token_urlsafe(32), time.monotonic() + self.session_seconds)
            self._sessions[_secret_hash(token)] = session
            return token, session

    def session(self, token: str | None) -> ReviewerSession:
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise WebError("authentication_required", 401)
        with self._lock:
            self._prune()
            session = self._sessions.get(_secret_hash(token))
            if session is None:
                raise WebError("authentication_required", 401)
            return session

    def authorize(self, session: ReviewerSession, run_id: str) -> None:
        if safe_id(run_id) not in session.identity.run_ids:
            raise WebError("object_not_found", 404)

    def verify_csrf(self, session: ReviewerSession, value: Any) -> None:
        if not isinstance(value, str) or not value.isascii() or len(value) > 200 or not secrets.compare_digest(session.csrf_token, value):
            raise WebError("csrf_failed")

    def logout(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(_secret_hash(token), None)


class LocalBoundaryMiddleware:
    """Strict fixed loopback authority; no proxy/CORS or cross-site write fallback."""
    def __init__(self, app, *, port: int, max_body_bytes: int = 65536):
        if type(port) is not int or not 20000 <= port <= 29999:
            raise WebError("claimed_port_required", 422)
        self.app, self.host = app, f"127.0.0.1:{port}"
        self.origin, self.max_body_bytes = "http://" + self.host, max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)
        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")]
            await send(message)
        try:
            headers: dict[bytes, list[bytes]] = {}
            for key, value in scope.get("headers", []):
                headers.setdefault(key.lower(), []).append(value)
            if headers.get(b"host") != [self.host.encode()] or scope.get("scheme") != "http":
                raise WebError("invalid_local_authority")
            client = scope.get("client")
            try:
                loopback = client and ipaddress.ip_address(client[0]).is_loopback
            except ValueError:
                loopback = False
            if not loopback or any(key == b"forwarded" or key.startswith(b"x-forwarded-") for key in headers):
                raise WebError("loopback_client_required")
            if b"origin" in headers and headers[b"origin"] != [self.origin.encode()]:
                raise WebError("origin_denied")
            method = scope["method"]
            if method not in {"GET", "HEAD", "POST"}:
                raise WebError("method_denied", 405)
            if method == "POST" and headers.get(b"origin") != [self.origin.encode()]:
                raise WebError("same_origin_required")
            if b"content-length" in headers:
                values = headers[b"content-length"]
                if len(values) != 1 or len(values[0]) > 10 or not values[0].isdigit() or int(values[0]) > self.max_body_bytes:
                    raise WebError("body_limit", 413)
            body = bytearray()
            async with asyncio.timeout(5):
                while True:
                    message = await receive()
                    if message["type"] != "http.request":
                        raise WebError("incomplete_request", 400)
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.max_body_bytes:
                        raise WebError("body_limit", 413)
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
            if method in {"GET", "HEAD"} and body:
                raise WebError("unexpected_body", 400)
            sent = False
            async def bounded_receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            return await self.app(scope, bounded_receive, secured_send)
        except (WebError, TimeoutError) as exc:
            status = exc.status if isinstance(exc, WebError) else 408
            code = exc.code if isinstance(exc, WebError) else "request_timeout"
            data = json.dumps({"schema_version": 1, "status": "error", "error": {"code": code}}).encode()
            await secured_send({"type": "http.response.start", "status": status,
                                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
            await send({"type": "http.response.body", "body": data})
