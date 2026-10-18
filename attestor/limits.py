"""Request-size cap enforced while streaming, and client-address bucketing for the rate limiter."""
from __future__ import annotations

import ipaddress
import json


class _TooLarge(BaseException):
    """BaseException on purpose: FastAPI turns any `Exception` raised while reading the body into a 400."""


class BodyLimitMiddleware:
    """Pure ASGI middleware: refuses request bodies above `max_bytes` with 413.

    A Content-Length above the cap is rejected before reading anything. Otherwise bytes are counted as the
    app pulls them from `receive`, so chunked (Transfer-Encoding) bodies and lying Content-Length headers are
    cut off at the cap too; the remaining chunks are never read."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                if value.isdigit() and int(value) > self.max_bytes:
                    return await self._reject(send)
                break

        total, started = 0, False

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise _TooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send):
        body = json.dumps({"detail": "request body too large"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


def client_key(address: str) -> str:
    """Rate-limit key for a client address. IPv6 clients are bucketed by /64 (one customer = one /64, so a
    single host cannot dodge the limiter by rotating addresses inside its prefix); IPv4-mapped IPv6 is
    treated as the IPv4 address; anything unparseable is used verbatim."""
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return address.strip()[:64] or "unknown"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)
