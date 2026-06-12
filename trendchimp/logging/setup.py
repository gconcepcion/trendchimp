from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trendchimp.config.settings import LoggingSettings

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging(settings: "LoggingSettings") -> None:
    level = getattr(logging, settings.level.upper(), logging.INFO)
    root = logging.getLogger("trendchimp")
    root.setLevel(level)
    root.handlers.clear()

    if settings.format == "console":
        try:
            from rich.logging import RichHandler

            handler: logging.Handler = RichHandler(
                level=level, show_time=True, show_path=False, rich_tracebacks=True,
            )
        except ImportError:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())

    handler.setLevel(level)
    root.addHandler(handler)

    # Audit trail: structured JSONL for all trade events.
    audit_path = settings.audit_log_path
    if audit_path:
        os.makedirs(os.path.dirname(os.path.abspath(audit_path)), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            audit_path, maxBytes=50 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        file_handler.setFormatter(_JsonFormatter())
        file_handler.setLevel(logging.DEBUG)
        audit_logger = logging.getLogger("trendchimp.audit")
        audit_logger.handlers.clear()
        audit_logger.addHandler(file_handler)
        audit_logger.propagate = False
