"""Process logging: a daily JSON-lines file plus human-readable stdout (TD-27, NFR-OBS-1).

Each process (`web`, `watch`, ...) calls `configure_logging` once at startup. The file is structured so a monitoring
tool (Loki, SigNoz, ...) can ingest it later without parsing free text.

Rotation is by day and never renames a file: the file for a day is simply named `<process>-YYYY-MM-DD.log` (UTC date,
like the `time` field), and files older than `keep_days` are deleted. Why not `TimedRotatingFileHandler`: it renames
`<process>.log` at midnight, which fails on Windows while another process (or an editor, `tail`) has the file open.
"""

import contextlib
import json
import logging
import sys
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol, TextIO

from il2ks.config import DEFAULT_LOG_KEEP_DAYS

_STDOUT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_FEED_FORMATTER = logging.Formatter("%(levelname)s %(message)s")

# Attributes every LogRecord has; anything else on a record came from `extra=` and goes into the JSON line.
_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime", "taskName"}


class _Il2ksHandler:
    """Marker mixin: handlers we installed, so a second `configure_logging` replaces them instead of adding more."""


class _DailyFileHandler(logging.FileHandler, _Il2ksHandler):
    """Appends to `<dir>/<process>-<UTC date of the record>.log`, switching file at UTC midnight and deleting files
    older than `keep_days` (the day's own file counts as one). The file is created on the first record."""

    def __init__(self, log_dir: Path, process: str, keep_days: int) -> None:
        super().__init__(log_dir / f"{process}.log", encoding="utf-8", delay=True)
        self._dir = log_dir
        self._process = process
        self._keep_days = keep_days
        self._day: date | None = None

    def path_for(self, day: date) -> Path:
        return self._dir / f"{self._process}-{day.isoformat()}.log"

    def _switch_to(self, day: date) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self._day = day
        self.baseFilename = str(self.path_for(day).absolute())
        self._prune(day)

    def _prune(self, today: date) -> None:
        """Delete this process's day files older than `keep_days`. A file that's still open elsewhere stays until
        the next day (Windows refuses to delete it)."""
        oldest = today - timedelta(days=self._keep_days - 1)
        prefix = f"{self._process}-"
        for path in self._dir.glob(f"{prefix}????-??-??.log"):
            try:
                day = date.fromisoformat(path.name[len(prefix) : -len(".log")])
            except ValueError:
                continue
            if day < oldest:
                with contextlib.suppress(OSError):
                    path.unlink()

    def emit(self, record: logging.LogRecord) -> None:
        day = datetime.fromtimestamp(record.created, UTC).date()
        if day != self._day:
            self._switch_to(day)
        super().emit(record)


class LiveDisplay(Protocol):
    """What takes over the console while a long run shows its progress in place (`il2ks.progress`)."""

    def feed(self, text: str) -> None: ...

    def phase(self, text: str) -> None: ...


def _one_line(text: str) -> str:
    """The first line and the last, which for a traceback are the message and its cause."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(lines[:1] + lines[1:][-1:])


class _StdoutHandler(logging.StreamHandler[TextIO], _Il2ksHandler):
    """Human-readable stdout. While a `display` is attached (`live_stdout`) a line of its own would break the display:
    warnings and errors go to it as one line, a record logged with `extra={"phase": True}` goes to it as what the run
    is doing, and the rest stays out of the console."""

    display: LiveDisplay | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if self.display is None:
            super().emit(record)
        elif record.levelno >= logging.WARNING:
            self.display.feed(_one_line(_FEED_FORMATTER.format(record)))
        elif getattr(record, "phase", False):
            self.display.phase(record.getMessage())


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time (UTC ISO 8601), level, logger, message, process, `server_uid` (when known),
    `extra` fields, exception."""

    def __init__(self, process: str, server_uid: uuid.UUID | None = None) -> None:
        super().__init__()
        self._process = process
        self._server_uid = None if server_uid is None else str(server_uid)

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process": self._process,
        }
        if self._server_uid is not None:
            entry["server_uid"] = self._server_uid
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRS and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(
    process: str,
    log_dir: Path,
    level: str = "INFO",
    *,
    server_uid: uuid.UUID | None = None,
    keep_days: int = DEFAULT_LOG_KEEP_DAYS,
) -> None:
    """Daily structured (JSON lines) log file `<log_dir>/<process>-YYYY-MM-DD.log` plus human-readable stdout.

    Keeps `keep_days` days of files (TD-27). `server_uid` goes on every JSON line when given. Idempotent."""
    numeric = logging.getLevelNamesMapping().get(level.upper())
    if numeric is None:
        raise ValueError(f"unknown log level {level!r}")
    if keep_days < 1:
        raise ValueError(f"keep_days must be at least 1, got {keep_days}")
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for old in [h for h in root.handlers if isinstance(h, _Il2ksHandler)]:
        root.removeHandler(old)
        old.close()

    file_handler = _DailyFileHandler(log_dir, process, keep_days)
    file_handler.setFormatter(JsonFormatter(process, server_uid))
    stdout_handler = _StdoutHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter(_STDOUT_FORMAT))

    root.addHandler(file_handler)
    root.addHandler(stdout_handler)
    root.setLevel(numeric)


@contextmanager
def live_stdout(display: LiveDisplay) -> Generator[None]:
    """Hands the console to `display` for the block. The log file keeps getting every record."""
    handlers = [h for h in logging.getLogger().handlers if isinstance(h, _StdoutHandler)]
    for handler in handlers:
        handler.display = display
    try:
        yield
    finally:
        for handler in handlers:
            handler.display = None


def make_file_handler(log_dir: str, process: str, keep_days: int) -> logging.Handler:
    """`dictConfig` factory for the daily file handler (the dict form can't carry a `Path`)."""
    return _DailyFileHandler(Path(log_dir), process, keep_days)


def make_json_formatter(process: str, server_uid: str | None = None) -> logging.Formatter:
    """`dictConfig` factory for `JsonFormatter` (the dict form carries the UID as text)."""
    return JsonFormatter(process, None if server_uid is None else uuid.UUID(server_uid))


def dict_config(
    process: str,
    log_dir: Path,
    level: str = "INFO",
    *,
    server_uid: uuid.UUID | None = None,
    keep_days: int = DEFAULT_LOG_KEEP_DAYS,
) -> dict[str, object]:
    """The same setup as `configure_logging`, as a `logging.config.dictConfig` dict.

    For servers that configure logging themselves in every worker process they spawn (hypercorn passes this dict to each
    worker), so the workers write the same daily JSON file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "il2ks.logsetup.make_json_formatter",
                "process": process,
                "server_uid": None if server_uid is None else str(server_uid),
            },
            "console": {"format": _STDOUT_FORMAT},
        },
        "handlers": {
            "file": {
                "()": "il2ks.logsetup.make_file_handler",
                "log_dir": str(log_dir),
                "process": process,
                "keep_days": keep_days,
                "formatter": "json",
            },
            "console": {"class": "logging.StreamHandler", "stream": "ext://sys.stdout", "formatter": "console"},
        },
        "root": {"handlers": ["file", "console"], "level": level.upper()},
    }
