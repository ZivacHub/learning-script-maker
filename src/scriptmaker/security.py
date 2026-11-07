"""Request guard for a local-only app.

A server on 127.0.0.1 can still be attacked from a web page open in the same browser:
- DNS rebinding: the page's own domain is re-pointed at 127.0.0.1, so requests carry a foreign
  Host header -> every request whose Host is not a well-formed local name is refused.
- Drive-by requests: another site, or another program's page on a different local port
  (a dev server, Docker's backend), sends a state-changing request here -> for POST/PUT/PATCH/DELETE
  the Origin must be exactly this app's own origin (same scheme, host AND port as the Host header),
  and `Sec-Fetch-Site`, when the browser sends it, must say same-origin (or none).
Non-browser clients (curl, tests) send neither header and are allowed on local Hosts.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import PlainTextResponse

logger = logging.getLogger("scriptmaker.security")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
DEFAULT_PORTS = {"http": 80, "https": 443}

_HOST_NAME = re.compile(r"([a-z0-9._-]+)(?::(\d{1,5}))?")
_HOST_IPV6 = re.compile(r"(\[[0-9a-f:.]+\])(?::(\d{1,5}))?")

HostPort = tuple[str, int | None]


def parse_host(header: str) -> HostPort | None:
    """Parse a Host header into (hostname, port|None); None when it is not exactly `name[:port]`.

    Rejects lists ("a, b"), userinfo ("a@b"), paths, whitespace inside, non-numeric or out-of-range ports.
    """
    value = header.strip().lower()
    match = _HOST_IPV6.fullmatch(value) if value.startswith("[") else _HOST_NAME.fullmatch(value)
    if not match:
        return None
    port = int(match.group(2)) if match.group(2) else None
    if port is not None and not 1 <= port <= 65535:
        return None
    return match.group(1), port


def hostname_of(header: str) -> str | None:
    parsed = parse_host(header)
    return parsed[0] if parsed else None


def parse_origin(origin: str) -> tuple[str, str, int] | None:
    """-> (scheme, hostname, port) with the scheme's default port filled in; None for "null"/garbage."""
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in DEFAULT_PORTS or not parts.hostname or parts.path not in ("", "/") or parts.query:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname.lower()
    return parts.scheme, host, port or DEFAULT_PORTS[parts.scheme]


def is_same_origin(origin: str, host: HostPort, scheme: str = "http") -> bool:
    parsed = parse_origin(origin)
    if parsed is None:
        return False
    return parsed == (scheme, host[0], host[1] or DEFAULT_PORTS[scheme])


CallNext = Callable[[Request], Awaitable[Response]]


def make_guard(allowed: frozenset[str]) -> Callable[[Request, CallNext], Awaitable[Response]]:
    async def guard(request: Request, call_next: CallNext) -> Response:
        host = parse_host(request.headers.get("host", ""))
        if host is None or host[0] not in allowed:
            logger.warning("rejected request: bad or foreign Host header")
            return PlainTextResponse("Forbidden: unexpected Host header", status_code=403)

        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and not is_same_origin(origin, host):
                logger.warning("rejected %s: Origin %r is not this app", request.method, origin)
                return PlainTextResponse("Forbidden: cross-origin request", status_code=403)
            fetch_site = request.headers.get("sec-fetch-site")
            if fetch_site is not None and fetch_site.lower() not in ALLOWED_FETCH_SITES:
                logger.warning("rejected %s: Sec-Fetch-Site %r", request.method, fetch_site)
                return PlainTextResponse("Forbidden: cross-site request", status_code=403)

        return await call_next(request)

    return guard
