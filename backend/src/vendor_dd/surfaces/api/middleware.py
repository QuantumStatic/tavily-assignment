from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from vendor_dd.logs import correlation_id_var, get_logger

_LOG = get_logger("http")


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request + response and sets a per-request correlation id.
    Never buffers a streaming response body (SSE stays untouched)."""

    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = correlation_id_var.set(cid)
        try:
            started = time.monotonic()
            _LOG.info("http.request", extra={"payload": {
                "method": request.method, "path": request.url.path,
                "query": str(request.url.query), "client": request.client.host if request.client else None,
            }})
            try:
                response: Response = await call_next(request)
            except Exception as exc:
                _LOG.error("http.error", extra={"payload": {
                    "method": request.method, "path": request.url.path, "error": str(exc)}})
                raise
            latency_ms = round((time.monotonic() - started) * 1000)
            is_stream = response.headers.get("content-type", "").startswith("text/event-stream")
            _LOG.info("http.response", extra={"payload": {
                "method": request.method, "path": request.url.path,
                "status": response.status_code, "latency_ms": latency_ms,
                "body": "<streaming>" if is_stream else None,
            }})
            response.headers["x-request-id"] = cid
            return response
        finally:
            correlation_id_var.reset(token)
