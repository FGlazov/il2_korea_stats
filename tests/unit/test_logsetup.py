"""Process logging setup (TD-27, NFR-OBS-1): daily JSON-lines files, readable stdout, idempotent."""

import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from il2ks.logsetup import JsonFormatter, configure_logging


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


def today_file(log_dir: Path, process: str) -> Path:
    return log_dir / f"{process}-{datetime.now(UTC).date().isoformat()}.log"


def emit_at(day: date, message: str = "m", hour: int = 12) -> None:
    """Log one record that claims to have been created on `day` (UTC)."""
    record = logging.LogRecord("il2ks.test", logging.INFO, __file__, 1, message, None, None)
    record.created = datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp()
    logging.getLogger().handle(record)


def day_files(log_dir: Path, process: str) -> list[str]:
    return sorted(p.name for p in log_dir.glob(f"{process}-*.log"))


def test_writes_json_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("watch", tmp_path / "logs")
    log = logging.getLogger("il2ks.test")
    log.info("ingested %s", "m1", extra={"mission_uid": "2026-09-19_22-34-13", "lines": 42})
    log.debug("not shown at INFO")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed")

    entries = read_lines(today_file(tmp_path / "logs", "watch"))
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
    assert len(read_lines(today_file(tmp_path, "web"))) == 1


def test_unknown_level_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="log level"):
        configure_logging("web", tmp_path, level="LOUD")


def test_server_uid_is_on_every_line_when_known(tmp_path: Path) -> None:
    uid = uuid.UUID("00000000-0000-4000-8000-000000000001")
    configure_logging("watch", tmp_path, server_uid=uid)
    logging.getLogger("a").info("one")
    logging.getLogger("b").warning("two", extra={"server_uid": "spoofed"})
    assert [e["server_uid"] for e in read_lines(today_file(tmp_path, "watch"))] == [str(uid)] * 2


def test_no_server_uid_field_when_unknown(tmp_path: Path) -> None:
    configure_logging("watch", tmp_path)
    logging.getLogger("a").info("one")
    assert "server_uid" not in read_lines(today_file(tmp_path, "watch"))[0]


def test_formatter_adds_server_uid() -> None:
    uid = uuid.UUID("00000000-0000-4000-8000-000000000002")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hi", None, None)
    assert json.loads(JsonFormatter("web", uid).format(record))["server_uid"] == str(uid)
    assert "server_uid" not in json.loads(JsonFormatter("web").format(record))


def test_a_new_file_per_utc_day_without_renaming(tmp_path: Path) -> None:
    configure_logging("watch", tmp_path)
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    emit_at(d1, "first")
    emit_at(d1, "second")
    emit_at(d2, "third")
    assert day_files(tmp_path, "watch") == ["watch-2026-10-01.log", "watch-2026-10-02.log"]
    assert [e["message"] for e in read_lines(tmp_path / "watch-2026-10-01.log")] == ["first", "second"]
    assert [e["message"] for e in read_lines(tmp_path / "watch-2026-10-02.log")] == ["third"]


def test_utc_midnight_is_the_boundary(tmp_path: Path) -> None:
    configure_logging("watch", tmp_path)
    emit_at(date(2026, 10, 1), "late", hour=23)
    emit_at(date(2026, 10, 2), "early", hour=0)
    assert day_files(tmp_path, "watch") == ["watch-2026-10-01.log", "watch-2026-10-02.log"]


def test_files_older_than_keep_days_are_deleted(tmp_path: Path) -> None:
    for offset in range(1, 8):
        (tmp_path / f"watch-{date(2026, 10, 10) - timedelta(days=offset)}.log").write_text("old\n", encoding="utf-8")
    configure_logging("watch", tmp_path, keep_days=3)
    emit_at(date(2026, 10, 10))
    # keep_days counts the current day: the 10th plus the 9th and 8th
    assert day_files(tmp_path, "watch") == ["watch-2026-10-08.log", "watch-2026-10-09.log", "watch-2026-10-10.log"]


def test_pruning_leaves_other_processes_and_other_files_alone(tmp_path: Path) -> None:
    ancient = date(2020, 1, 1)
    other = [f"web-{ancient}.log", f"watch-{ancient}.log.bak", "watch-notes.log", "watch.log", f"watcher-{ancient}.log"]
    for name in other:
        (tmp_path / name).write_text("keep\n", encoding="utf-8")
    configure_logging("watch", tmp_path, keep_days=1)
    emit_at(date(2026, 10, 10))
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*other, "watch-2026-10-10.log"])


def test_each_process_has_its_own_files(tmp_path: Path) -> None:
    """Several processes (web, watch, ...) log into one folder without touching each other's files (Windows)."""
    configure_logging("web", tmp_path)
    emit_at(date(2026, 10, 1), "from web")
    web = tmp_path / "web-2026-10-01.log"
    configure_logging("watch", tmp_path)  # replaces the handler of this test process; the web file stays as it is
    emit_at(date(2026, 10, 1), "from watch")
    assert [e["message"] for e in read_lines(web)] == ["from web"]
    assert [e["message"] for e in read_lines(tmp_path / "watch-2026-10-01.log")] == ["from watch"]


def test_an_undeletable_old_file_does_not_break_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "watch-2020-01-01.log"
    old.write_text("x\n", encoding="utf-8")

    def locked(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "unlink", locked)
    configure_logging("watch", tmp_path, keep_days=1)
    emit_at(date(2026, 10, 10), "still works")
    monkeypatch.undo()
    assert old.exists()
    assert [e["message"] for e in read_lines(tmp_path / "watch-2026-10-10.log")] == ["still works"]


def test_keep_days_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="keep_days"):
        configure_logging("web", tmp_path, keep_days=0)
