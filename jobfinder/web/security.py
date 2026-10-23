"""Security middleware — the product spec (T15).

The server is unauthenticated by design, so these are
load-bearing, not optional:

  1. Host-header allowlist (DNS-rebinding defense — an Origin check alone
     does not cover this: under rebinding, the browser still sends the
     *attacker's* hostname as Host, but the request otherwise looks
     same-origin to the application, so Origin matches and would pass).
  2. Origin allowlist on every state-changing request. Exact match only.
  3. A per-launch random token, delivered in the opened URL, exchanged
     once by the SPA for an HttpOnly/SameSite=Strict/host-only session
     cookie, required on every other API and stream route.

Deliberately not Starlette's ``TrustedHostMiddleware`` for item 1, even
though it is the obvious off-the-shelf choice and the product spec references it by
name. Checked against the installed Starlette (1.9.0, pulled in by the
fastapi>=0.156.0 pin) before using it:

  - It returns HTTP 400 for a rejected Host, not the 403 this task's DoD
    (and the product spec's own test list) requires.
  - It matches on ``parse_host_header(...).host`` — the host component
    with the port already stripped off (starlette/_utils.py). An
    allowlist entry written with a port, e.g. "127.0.0.1:8000" (the product spec's
    literal wording for item 1), would therefore *never* match anything:
    the comparison value never has a port on it to match against.

``HostAllowlistMiddleware`` below does a plain exact-string match against
the full, unparsed ``Host`` header value instead, so a spec-literal
allowlist entry like ``"127.0.0.1:8756"`` means what it says, and returns
403 as required.
"""

from __future__ import annotations

import logging
import secrets
from http.cookies import SimpleCookie

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("jobfinder.web.security")

SESSION_COOKIE_NAME = "jobfinder_session"

# Methods that can mutate state — the Origin allowlist (item 2) applies to
# these only. GET/HEAD/OPTIONS are left alone: GET has no side effects to
# protect, and OPTIONS is the CORS preflight, handled (if dev CORS is on at
# all) by CORSMiddleware before this middleware ever sees it.
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Routes reachable with no session cookie: the health check (so the
# lifecycle heartbeat / an external monitor doesn't need a cookie just to
# learn the process is alive, the product spec) and the token-exchange route
# itself (it's what *creates* the cookie in the first place).
PUBLIC_PATHS = frozenset({"/api/health", "/api/auth/exchange"})


def host_allowlist(port: int) -> frozenset[str]:
    """Exact Host-header values this server accepts, per the product spec."""
    return frozenset(
        {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }
    )


def origin_allowlist(port: int, *, extra: str | None = None) -> frozenset[str]:
    """Exact Origin-header values this server accepts for state-changing
    requests. Includes the server's own origin for every Host form it
    accepts — once T21 serves the SPA from this same process, a same-origin
    POST still carries an Origin header in modern browsers, so the
    allowlist must include the origin the user is actually loading the
    page from, not just a hypothetical separate frontend origin.

    ``extra`` is the exact Vite dev-server origin, added only when the dev
    CORS flag (item 5) is explicitly enabled — never a wildcard, never
    combined with ``allow_credentials`` unless it's this one exact origin.
    """
    origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    }
    if extra:
        origins.add(extra)
    return frozenset(origins)


async def _json_reject(send: Send, *, status_code: int, detail: str) -> None:
    body = f'{{"detail":"{detail}"}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class HostAllowlistMiddleware:
    """Rejects (403) any request whose ``Host`` header isn't in the
    allowlist. See module docstring for why this isn't
    ``starlette.middleware.trustedhost.TrustedHostMiddleware``.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: frozenset[str]) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        host = Headers(scope=scope).get("host", "")
        if host not in self.allowed_hosts:
            logger.warning("Rejected request with disallowed Host header: %r", host)
            await _json_reject(send, status_code=403, detail="invalid host")
            return

        await self.app(scope, receive, send)


class OriginAllowlistMiddleware:
    """Rejects (403) any state-changing request whose ``Origin`` header is
    present but not an exact match in the allowlist. A request with no
    ``Origin`` header at all (same-origin, non-CORS navigation) is
    accepted — browsers don't always send one, and its absence alone is
    not evidence of cross-origin intent.
    """

    def __init__(self, app: ASGIApp, allowed_origins: frozenset[str]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in STATE_CHANGING_METHODS:
            await self.app(scope, receive, send)
            return

        origin = Headers(scope=scope).get("origin")
        # Exact match only — no startswith/endswith, and "null" is never in
        # the allowlist so it's rejected by the same exact-match check (no
        # special case needed, but called out here because a `startswith`
        # mistake is exactly the bug that would let "null" or
        # "https://evil.example.com.attacker.com" slip through).
        if origin is not None and origin not in self.allowed_origins:
            logger.warning("Rejected %s request with disallowed Origin: %r", scope["method"], origin)
            await _json_reject(send, status_code=403, detail="invalid origin")
            return

        await self.app(scope, receive, send)


class SessionAuthMiddleware:
    """Requires a valid session cookie (item 3) on every API and stream
    route except ``PUBLIC_PATHS``. Non-``/api``/``/events`` paths are left
    alone — those are future SPA static-asset routes (T21+), out of scope
    for this task and not security-sensitive the same way (no data access).
    """

    def __init__(self, app: ASGIApp, launch_token: "LaunchToken") -> None:
        self.app = app
        self.launch_token = launch_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if path in PUBLIC_PATHS or not (path.startswith("/api/") or path.startswith("/events")):
            await self.app(scope, receive, send)
            return

        cookie_header = Headers(scope=scope).get("cookie", "")
        jar: SimpleCookie = SimpleCookie()
        jar.load(cookie_header)
        morsel = jar.get(SESSION_COOKIE_NAME)
        session_token = morsel.value if morsel is not None else None

        if not self.launch_token.is_valid_session(session_token):
            await _json_reject(send, status_code=401, detail="missing or invalid session")
            return

        await self.app(scope, receive, send)


class LaunchToken:
    """Per-process-launch random token and the session tokens it has been
    exchanged for.

    The launcher (not yet built — a later task) reads ``.value`` to build
    the URL it opens in the browser, e.g.
    ``http://127.0.0.1:8756/?token=<value>``. The SPA (T21+) reads the
    ``token`` query param on load, POSTs it once to
    ``POST /api/auth/exchange``, receives back an HttpOnly/SameSite=Strict
    cookie, and strips the token from the URL via
    ``history.replaceState`` so it never ends up in browser history or a
    referrer header.
    """

    def __init__(self) -> None:
        self.value = secrets.token_urlsafe(32)
        self._session_tokens: set[str] = set()

    def exchange(self, candidate: str) -> str | None:
        """Consume the launch token once validated, return a fresh,
        unrelated session token. Returns ``None`` (never raises) if
        ``candidate`` doesn't match — constant-time compare via
        ``secrets.compare_digest`` so response timing doesn't leak how
        much of the token was correct.
        """
        if not secrets.compare_digest(candidate, self.value):
            return None
        session_token = secrets.token_urlsafe(32)
        self._session_tokens.add(session_token)
        return session_token

    def is_valid_session(self, token: str | None) -> bool:
        if token is None:
            return False
        return any(secrets.compare_digest(token, known) for known in self._session_tokens)


class TokenExchangeRequest(BaseModel):
    token: str


def install_security(
    app: ASGIApp,
    *,
    port: int,
    dev_cors_origin: str | None = None,
) -> LaunchToken:
    """Wire the Host/Origin/session middleware and the token-exchange
    route onto ``app``. Returns the ``LaunchToken`` so the caller can store
    it (``app.state.launch_token``) and a future launcher can read
    ``.value`` to build the opened URL.

    Middleware add order matters: Starlette's ``add_middleware`` makes each
    newly-added middleware the new outermost layer (it's prepended to the
    list that's later wrapped in reverse). Adding Session, then Origin,
    then Host, then CORS last produces, outer to inner on every request:
    CORS (dev only) -> Host -> Origin -> Session -> route. Host is checked
    before anything else gets a chance to run real logic; CORS (when
    enabled) only needs to sit outside Host so it can still intercept
    preflight OPTIONS, which never carries a session cookie.
    """
    assert isinstance(app, FastAPI)

    launch_token = LaunchToken()
    app.state.launch_token = launch_token

    app.add_middleware(SessionAuthMiddleware, launch_token=launch_token)
    app.add_middleware(OriginAllowlistMiddleware, allowed_origins=origin_allowlist(port, extra=dev_cors_origin))
    app.add_middleware(HostAllowlistMiddleware, allowed_hosts=host_allowlist(port))

    if dev_cors_origin:
        # Dev-only CORS (item 5): a single exact origin, never a wildcard,
        # and never enabled unless dev_cors_origin is explicitly passed in
        # (the packaged build never sets it — see jobfinder/web/app.py).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[dev_cors_origin],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.post("/api/auth/exchange")
    async def exchange_token(payload: TokenExchangeRequest, response: Response) -> dict:
        session_token = launch_token.exchange(payload.token)
        if session_token is None:
            raise HTTPException(status_code=401, detail="invalid launch token")
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            httponly=True,
            samesite="strict",
            path="/",
            # No `domain=` — host-only cookie, per the product spec.
            # No `secure=True`: this server is loopback-only HTTP by design
            # never TLS, and the `Secure` attribute's
            # same-origin-potentially-trustworthy carve-out for loopback
            # isn't reliably implemented for every loopback host form
            # (127.0.0.1 / localhost / [::1]) across browsers; HttpOnly +
            # SameSite=Strict + host-only already keep it off the network
            # to anything but this same loopback server.
        )
        return {"status": "ok"}

    return launch_token
