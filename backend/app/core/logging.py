"""Structured logs: one JSON object per line (LOG_FORMAT=json) or a readable console line, and one
line per HTTP request."""

import json
import logging
import sys
import time
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_logger = logging.getLogger("app.http")

_STANDARD = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in _extras(record).items():
            line.setdefault(key, value)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={v}" for k, v in _extras(record).items())
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        base = f"{stamp} {record.levelname:<7} {record.name}: {record.getMessage()}"
        text = f"{base}  {extras}" if extras else base
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    # Libraries stay at WARNING; our own loggers (the app and the engine) speak at LOG_LEVEL.
    root.setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(level.upper())
    logging.getLogger("engine").setLevel(level.upper())
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)


class RequestLog:
    """One log line per request. Raw ASGI rather than BaseHTTPMiddleware, so bodies stream through."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].endswith(("/healthz", "/api/health")):
            await self.app(scope, receive, send)
            return
        started, status = time.perf_counter(), 0

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            _request_logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
            )
