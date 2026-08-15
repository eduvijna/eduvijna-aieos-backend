"""Test-only ASGI probe for PED-I06 OCI HTTP smoke validation.

NOT the product application. Mounted into the probe container during CI only.
Exposes GET /livez -> 200 without AIEOS production /livez semantics.
"""

from __future__ import annotations


async def app(scope, receive, send):  # noqa: ANN001
    if scope["type"] != "http":
        return
    path = scope.get("path", "")
    method = scope.get("method", "GET")
    if method == "GET" and path == "/livez":
        body = b"ok"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return
    body = b"not found"
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
