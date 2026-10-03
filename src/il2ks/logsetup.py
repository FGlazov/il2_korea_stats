"""Process logging: rotating JSON-lines file plus human-readable stdout (TD-27, NFR-OBS-1).

Each process (`web`, `watch`, ...) calls `configure_logging` once at startup. The file is structured so a monitoring
tool (Loki, SigNoz, ...) can ingest it later without parsing free text.
"""

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
_STDOUT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Attributes every LogRecord has; anything else on a record came from `extra=` and goes into the JSON line.
_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime", "taskName"}


class _Il2ksHandler:
    """Marker mixin: handlers we installed, so a second `configure_logging` replaces them instead of adding more."""


class _FileHandler(logging.handlers.RotatingFileHandler, _Il2ksHandler):
    pass


class _StdoutHandler(logging.StreamHandler[TextIO], _Il2ksHandler):
    pass


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time (UTC ISO 8601), level, logger, message, process, `extra` fields, exception."""

    def __init__(self, process: str) -> None:
        super().__init__()
        self._process = process

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process": self._process,
        }
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRS and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(process: str, log_dir: Path, level: str = "INFO") -> None:
    """Rotating structured (JSON lines) log file `<log_dir>/<process>.log` plus human-readable stdout. Idempotent."""
    numeric = logging.getLevelNamesMapping().get(level.upper())
    if numeric is None:
        raise ValueError(f"unknown log level {level!r}")
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for old in [h for h in root.handlers if isinstance(h, _Il2ksHandler)]:
        root.removeHandler(old)
        old.close()

    file_handler = _FileHandler(
        log_dir / f"{process}.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(JsonFormatter(process))
    stdout_handler = _StdoutHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter(_STDOUT_FORMAT))

    root.addHandler(file_handler)
    root.addHandler(stdout_handler)
    root.setLevel(numeric)
