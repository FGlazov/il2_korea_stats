"""`il2ks reprocess --since/--until`: pick missions by their (server-local) date (FR-ING-9, FR-ING-14)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from il2ks.db.models import IngestRun, Mission
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
    env: Env, uids: list[str] | None = None, *, since: date | None = None, until: date | None = None
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
