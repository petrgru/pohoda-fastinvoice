"""Central logging setup: structlog bridge + JSON/console rendering.

Call :func:`configure_logging` once per process entry point (``app.main``,
``app.tasks``, standalone scripts). Everything goes to stdout without file
handlers or in-process rotation - in Kubernetes the kubelet rotates and
Alloy/Loki collects (see docs/logging-loki.md).

The development mode renders human-readable console lines; production renders
one JSON object per line (``LOG_JSON`` overrides the FASTAPI_ENV heuristic).
stdlib loggers (uvicorn, procrastinate, our own ``logging.getLogger``) are
captured through ProcessorFormatter, so a single pipeline formats everything:
contextvars + correlation id + redaction + timestamps.
"""

import logging
import logging.config
import sys
import time
from collections.abc import MutableMapping
from typing import Any

import structlog
from asgi_correlation_id import correlation_id
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

#: keys whose values never reach the log output unmasked
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "smtp_password",
        "token",
        "authorization",
        "api_key",
        "export_api_key",
        "email",
        "email_to",
        "recipient",
    }
)

#: access-log lines for these paths are noise (probes, monitoring)
_LOG_SKIP_PATHS = frozenset({"/api/v1/utils/health-check/", "/metrics"})

#: uvicorn internals - the console color template, useless in JSON logs
_DROP_KEYS = frozenset({"color_message"})


class _StdoutStream:
    """Stream that resolves ``sys.stdout`` on every write.

    ``dictConfig`` would otherwise freeze the stream object found at
    configuration time, which detaches the handler from per-test capture
    (capsys/capfd) and from anything else that swaps stdout at runtime.
    """

    def write(self, message: str) -> int:
        return sys.stdout.write(message)

    def flush(self) -> None:
        sys.stdout.flush()

    def isatty(self) -> bool:
        return sys.stdout.isatty()


_timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)


def redact_sensitive(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask values of known-sensitive keys (works for struct and std logs)."""
    for key in SENSITIVE_KEYS.intersection(event_dict):
        event_dict[key] = "***"
    return event_dict


def drop_noise(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Remove internal extras that would only add noise to every line."""
    for key in _DROP_KEYS.intersection(event_dict):
        del event_dict[key]
    return event_dict


def add_request_id(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Attach the asgi-correlation-id of the running request, if any."""
    cid = correlation_id.get(None)
    if cid is not None and "correlation_id" not in event_dict:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging() -> None:
    """Configure structlog + stdlib logging for this process (idempotent)."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    level_name = logging.getLevelName(level)
    json_logs = (
        settings.LOG_JSON
        if settings.LOG_JSON is not None
        else settings.FASTAPI_ENV != "development"
    )

    # applied to every foreign (stdlib) LogRecord before rendering
    pre_chain: list[Any] = [
        structlog.contextvars.merge_contextvars,
        add_request_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.stdlib.PositionalArgumentsFormatter(),
        redact_sensitive,
        drop_noise,
        _timestamper,
    ]
    if json_logs:
        renderers: list[Any] = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        renderers = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()),
        ]

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structlog": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processors": renderers,
                    "foreign_pre_chain": pre_chain,
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "stream": _StdoutStream(),
                    "formatter": "structlog",
                }
            },
            "loggers": {
                # the root covers our modules, procrastinate, boto3, ...
                "": {"handlers": ["default"], "level": level_name},
                # uvicorn configures its own loggers on startup - take them over
                "uvicorn": {
                    "handlers": ["default"],
                    "level": level_name,
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["default"],
                    "level": level_name,
                    "propagate": False,
                },
                # the access log is replaced by RequestLogMiddleware below
                "uvicorn.access": {
                    "handlers": ["default"],
                    "level": "WARNING",
                    "propagate": False,
                },
                "uvicorn.asgi": {
                    "handlers": ["default"],
                    "level": level_name,
                    "propagate": False,
                },
            },
        }
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            add_request_id,
            redact_sensitive,
            drop_noise,
            structlog.stdlib.PositionalArgumentsFormatter(),
            _timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


class RequestLogMiddleware:
    """One structured line per request, replacing uvicorn's access log.

    Pure ASGI on purpose: BaseHTTPMiddleware runs the downstream app in a
    separate task, so contextvars bound in the request handlers (user_id,
    invoice_id) would not be visible when this middleware logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or path in _LOG_SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # sync dependencies/endpoints run in a threadpool: contextvars they
            # bind (user_id) do not propagate back up, so read the value from
            # the shared scope state as well
            state = scope.get("state") or {}
            context: dict[str, Any] = {
                "method": scope.get("method", ""),
                "path": path,
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            if state.get("user_id"):
                context["user_id"] = state["user_id"]
            structlog.get_logger("app.request").info("request", **context)
