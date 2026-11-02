"""`il2ks reprocess --since/--until`: pick missions by their (server-local) date (FR-ING-9, FR-ING-14)."""

import logging
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from il2ks.core.logparse.files import SpanBound, span_is_reversed
from il2ks.db.models import IngestRun, Mission
from il2ks.ingest import reprocess as reprocess_module
from il2ks.ingest.reprocess import ReprocessSummary, archived_targets, mission_in_span, reprocess
from tests.integration.test_ingest_runner import Env, fake_work, threads

pytestmark = pytest.mark.django_db

EDGE_BEFORE = "2026-03-31_23-59-59"
FIRST = "2026-04-01_00-00-00"
MIDDLE = "2026-04-15_12-00-00"
LAST = "2026-04-30_23-59-59"
EDGE_AFTER = "2026-05-01_00-00-00"
ALL = [EDGE_BEFORE, FIRST, MIDDLE, LAST, EDGE_AFTER]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    env = Env(tmp_path)
    for uid in ALL:
        env.add(uid)
    env.ingest()
    return env


def run(
    env: Env, uids: list[str] | None = None, *, since: SpanBound | None = None, until: SpanBound | None = None
) -> ReprocessSummary:
    return reprocess(
        env.cfg,
        env.pipeline,
        uids,
        since=since,
        until=until,
        workers=2,
        work=fake_work,
        executor_factory=threads,
        rebuild=lambda: None,
        now=env.now,
    )


def test_the_live_line_follows_the_missions(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closed before the last rebuild, which logs what it does to the console again."""
    seen: list[object] = []

    @contextmanager
    def recording(total: int) -> Generator[Callable[[int], None]]:
        seen.append(("start", total))
        yield seen.append
        seen.append("end")

    monkeypatch.setattr(reprocess_module, "live_progress", recording)

    run(env, since=date(2026, 4, 1), until=date(2026, 4, 30))

    assert seen == [("start", 3), 1, 2, 3, "end"]


def test_the_run_says_how_many_missions_are_left(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    """Maintainer 2026-11-02: `ingest` reports every mission, a reprocess in the terminal said nothing until the end."""
    with caplog.at_level(logging.INFO, logger="il2ks.ingest.reprocess"):
        run(env, since=date(2026, 4, 1), until=date(2026, 4, 30))

    lines = [r.getMessage() for r in caplog.records]
    assert "reprocessing 3 missions" in lines
    counts = [m.groups() for m in (re.search(r"(\d) of 3 missions done, (\d) left", line) for line in lines) if m]
    assert counts == [("1", "2"), ("2", "1"), ("3", "0")]


@pytest.mark.parametrize(
    ("uid", "since", "until", "expected"),
    [
        (FIRST, date(2026, 4, 1), date(2026, 4, 30), True),
        (LAST, date(2026, 4, 1), date(2026, 4, 30), True),  # both ends are inclusive, whatever the time of day
        (EDGE_BEFORE, date(2026, 4, 1), None, False),
        (EDGE_AFTER, None, date(2026, 4, 30), False),
        (EDGE_BEFORE, None, date(2026, 3, 31), True),
        (MIDDLE, date(2026, 4, 15), date(2026, 4, 15), True),  # a single day
        (MIDDLE, None, None, True),
        (MIDDLE, date(2026, 4, 16), None, False),
        (MIDDLE, None, date(2026, 4, 14), False),
        ("2025-12-31_23-59-59", date(2026, 1, 1), None, False),  # year boundary
    ],
)
def test_mission_in_span(uid: str, since: date | None, until: date | None, expected: bool) -> None:
    assert mission_in_span(uid, since, until) is expected


@pytest.mark.parametrize(
    ("uid", "since", "until", "expected"),
    [
        (MIDDLE, datetime(2026, 4, 15, 12, 0), None, True),  # the start counts by its minute
        (MIDDLE, datetime(2026, 4, 15, 12, 1), None, False),
        (MIDDLE, None, datetime(2026, 4, 15, 12, 0), True),
        (MIDDLE, None, datetime(2026, 4, 15, 11, 59), False),
        ("2026-04-15_12-00-59", None, datetime(2026, 4, 15, 12, 0), True),  # the whole last minute
        (MIDDLE, date(2026, 4, 15), datetime(2026, 4, 15, 12, 0), True),  # one end with a time, one without
        (MIDDLE, datetime(2026, 4, 15, 12, 0), date(2026, 4, 15), True),
        (MIDDLE, datetime(2026, 4, 14, 23, 0), datetime(2026, 4, 15, 1, 0), False),
    ],
)
def test_a_time_narrows_the_span_to_the_minute(
    uid: str, since: SpanBound | None, until: SpanBound | None, expected: bool
) -> None:
    assert mission_in_span(uid, since, until) is expected


@pytest.mark.parametrize(
    ("since", "until", "expected"),
    [
        (date(2026, 4, 2), date(2026, 4, 1), True),
        (date(2026, 4, 1), date(2026, 4, 1), False),
        (datetime(2026, 4, 1, 18, 0), datetime(2026, 4, 1, 17, 59), True),
        (datetime(2026, 4, 1, 18, 0), datetime(2026, 4, 1, 18, 0), False),
        (datetime(2026, 4, 1, 18, 0), date(2026, 4, 1), False),  # a date is the whole day, so it holds that time
        (date(2026, 4, 1), datetime(2026, 4, 1, 0, 0), False),
        (date(2026, 4, 2), datetime(2026, 4, 1, 23, 59), True),
        (None, date(2026, 4, 1), False),
        (date(2026, 4, 1), None, False),
    ],
)
def test_a_reversed_span(since: SpanBound | None, until: SpanBound | None, expected: bool) -> None:
    assert span_is_reversed(since, until) is expected


def test_a_span_with_times_picks_the_missions_of_those_minutes(env: Env) -> None:
    summary = run(env, since=datetime(2026, 4, 15, 12, 0), until=datetime(2026, 4, 30, 23, 59))
    assert sorted(summary.ok) == [MIDDLE, LAST]


def test_since_and_until_are_inclusive(env: Env) -> None:
    summary = run(env, since=date(2026, 4, 1), until=date(2026, 4, 30))
    assert sorted(summary.ok) == [FIRST, MIDDLE, LAST]
    assert summary.missing == []
    assert len(env.runs(EDGE_BEFORE)) == 1  # untouched: no new IngestRun
    assert len(env.runs(EDGE_AFTER)) == 1
    assert len(env.runs(MIDDLE)) == 2


def test_since_alone_and_until_alone(env: Env) -> None:
    assert sorted(run(env, since=date(2026, 4, 30)).ok) == [LAST, EDGE_AFTER]
    assert sorted(run(env, until=date(2026, 4, 1)).ok) == [EDGE_BEFORE, FIRST]


def test_a_span_without_missions_does_nothing(env: Env) -> None:
    summary = run(env, since=date(2027, 1, 1))
    assert sorted(summary.ok) == []
    assert summary.failed == []
    assert IngestRun.objects.count() == len(ALL)


def test_no_span_means_everything(env: Env) -> None:
    assert sorted(run(env).ok) == ALL


def test_span_combines_with_mission_as_an_intersection(env: Env) -> None:
    summary = run(env, [FIRST, EDGE_AFTER, MIDDLE], since=date(2026, 4, 1), until=date(2026, 4, 30))
    assert sorted(summary.ok) == [FIRST, MIDDLE]
    assert summary.missing == []  # EDGE_AFTER has an archive but lies outside the span: just not selected


def test_a_requested_mission_inside_the_span_without_an_archive_is_missing(env: Env) -> None:
    summary = run(env, [MIDDLE, "2026-04-20_00-00-00"], since=date(2026, 4, 1), until=date(2026, 4, 30))
    assert sorted(summary.ok) == [MIDDLE]
    assert summary.missing == ["2026-04-20_00-00-00"]


def test_the_date_is_the_server_local_one_from_the_uid_not_the_utc_start(env: Env) -> None:
    """00:30 local on 1 April in Seoul (UTC+9) is 15:30 UTC on 31 March: it still counts as 1 April."""
    seoul = replace(env.cfg, timezone_name="Asia/Seoul")
    summary = reprocess(
        seoul,
        env.pipeline,
        None,
        since=date(2026, 4, 1),
        until=date(2026, 4, 1),
        workers=1,
        work=fake_work,
        executor_factory=threads,
        rebuild=lambda: None,
        now=env.now,
    )
    assert sorted(summary.ok) == [FIRST]
    mission = Mission.objects.get(mission_uid=FIRST)
    assert mission.started_at == datetime(2026, 3, 31, 15, 0, tzinfo=UTC)  # the UID is 00:00:00 local
    assert mission.started_at.date() != date(2026, 4, 1)  # the UTC date would have excluded it


def test_archives_without_run_history_are_adopted_only_inside_the_span(env: Env) -> None:
    IngestRun.objects.all().delete()
    Mission.objects.all().delete()
    targets = archived_targets(env.cfg, None, date(2026, 4, 1), date(2026, 4, 15))
    assert sorted(targets) == [FIRST, MIDDLE]
    summary = run(env, since=date(2026, 4, 1), until=date(2026, 4, 15))
    assert sorted(summary.ok) == [FIRST, MIDDLE]
    assert Mission.objects.count() == 2
