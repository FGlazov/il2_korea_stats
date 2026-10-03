"""Process logging setup (TD-27, NFR-OBS-1): JSON lines on file, readable stdout, idempotent."""

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from il2ks.logsetup import configure_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in root.handlers:
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.setLevel(level)


def ours(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if type(h).__module__ == "il2ks.logsetup"]


def read_lines(path: Path) -> list[dict[str, object]]:
    for h in logging.getLogger().handlers:
        h.flush()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_writes_json_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("watch", tmp_path / "logs")
    log = logging.getLogger("il2ks.test")
    log.info("ingested %s", "m1", extra={"mission_uid": "2026-09-19_22-34-13", "lines": 42})
    log.debug("not shown at INFO")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed")

    entries = read_lines(tmp_path / "logs" / "watch.log")
    assert len(entries) == 2
    first, second = entries
    assert first["level"] == "INFO"
    assert first["logger"] == "il2ks.test"
    assert first["message"] == "ingested m1"
    assert first["process"] == "watch"
    assert first["mission_uid"] == "2026-09-19_22-34-13"
    assert first["lines"] == 42
    offset = datetime.fromisoformat(str(first["time"])).utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0
    assert second["level"] == "ERROR"
    assert "RuntimeError: boom" in str(second["exception"])
    assert "ingested m1" in capsys.readouterr().out


def test_idempotent(tmp_path: Path) -> None:
    configure_logging("web", tmp_path)
    configure_logging("web", tmp_path, level="debug")
    root = logging.getLogger()
    assert len(ours(root)) == 2
    assert root.level == logging.DEBUG
    logging.getLogger("x").warning("once")
    assert len(read_lines(tmp_path / "web.log")) == 1


def test_unknown_level_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="log level"):
        configure_logging("web", tmp_path, level="LOUD")
