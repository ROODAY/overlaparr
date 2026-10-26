"""Structured JSON logging with secret redaction and file rotation."""

import json
import logging
import re
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from overlaparr.config.schema import LoggingConfig

if TYPE_CHECKING:
    from overlaparr.config.store import ConfigStore

REPLACEMENT = "***"
MIN_SECRET_LEN = 6

_KEYED_RE = re.compile(
    r"(?P<key>X-Plex-Token|X-Api-Key|apikey|api_key|token|password)"
    r"(?P<sep>\s*[=:]\s*)"
    r"(?P<val>[^\s&\"',;]+)",
    re.IGNORECASE,
)
_WEBHOOK_RE = re.compile(r"/api/webhooks/\d+/[\w-]+")

_SECRETS: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Redact this exact value everywhere (ignored when shorter than 6 chars)."""
    if value and len(value) >= MIN_SECRET_LEN:
        with _lock:
            _SECRETS.add(value)


def redact(text: str) -> str:
    text = _WEBHOOK_RE.sub(f"/api/webhooks/{REPLACEMENT}", text)
    text = _KEYED_RE.sub(lambda m: f"{m['key']}{m['sep']}{REPLACEMENT}", text)
    with _lock:
        secrets = sorted(_SECRETS, key=len, reverse=True)
    for s in secrets:
        text = text.replace(s, REPLACEMENT)
    return text


def install_secret_redaction(store: "ConfigStore") -> None:
    """Register the store's secrets now and again after every reload."""

    def register_all(_cfg: object = None) -> None:
        for value in store.secret_values():
            register_secret(value)

    register_all()
    store.subscribe(register_all)


class RedactingFilter(logging.Filter):
    """Redacts message, args and formatted exception text of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            data["exc"] = record.exc_text
        if record.stack_info:
            data["stack"] = record.stack_info
        return json.dumps(data, ensure_ascii=False)


def configure_logging(cfg: LoggingConfig, log_path: Path) -> None:
    """(Re)configure root logging; replaces handlers installed by earlier calls."""
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_overlaparr", False)]:
        root.removeHandler(h)
        h.close()

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            log_path,
            maxBytes=cfg.max_bytes,
            backupCount=cfg.backup_count,
            encoding="utf-8",
        ),
    ]
    for h in handlers:
        h.setFormatter(JsonFormatter())
        h.addFilter(RedactingFilter())
        h._overlaparr = True  # type: ignore[attr-defined]
        root.addHandler(h)
    root.setLevel(cfg.level)
