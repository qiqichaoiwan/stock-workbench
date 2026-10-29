"""ASGI middleware rejecting oversized JSON bodies on heavy evaluation routes.

FastAPI buffers the whole request body before it solves dependencies, so an
endpoint-level check can only reject after the bytes are already in memory.
Limiting the ASGI receive stream while reading stops a large upload before it is
buffered or parsed, including chunked requests that declare no Content-Length.
"""
import json

EVALUATION_BODY_LIMIT = 131072

LIMITS = {
    "/api/screener/dual-core/evaluate-v2": EVALUATION_BODY_LIMIT,
    "/api/screener/dual-core/checklist": EVALUATION_BODY_LIMIT,
}


class PathBodyLimitMiddleware:
    def __init__(self, app, limits: dict[str, int] | None = None):
        self.app = app
        self.limits = LIMITS if limits is None else limits

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in self.limits:
            await self.app(scope, receive, send)
            return
        limit = self.limits[scope["path"]]
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await self._reject(send, limit)
            return
        chunks, size, complete = [], 0, False
        while not complete:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await self._reject(send, limit)
                return
            chunks.append(chunk)
            complete = not message.get("more_body", False)
        body = b"".join(chunks)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send, limit: int):
        payload = json.dumps({"detail": f"请求体超过 {limit // 1024} KiB 上限"},
                             ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(payload)).encode())]})
        await send({"type": "http.response.body", "body": payload})
