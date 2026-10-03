"""Online now, ingest side: `LiveTracker` and `watch` (FR-ING-12, TD-28).

Missions are anonymized fixtures cut into raw parts that grow tick by tick in a temp log folder."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.db import OperationalError

from il2ks.config import Config, LiveConfig
from il2ks.db.models import LiveMission, LivePlayer, Player
from il2ks.db.site import current_data_version
from il2ks.ingest.live import LiveTracker, find_in_progress
from il2ks.ingest.watch import watch
from tests.ingest_fakes import T0, FakeSteps, make_config, make_pipeline
from tests.live_helpers import LIVE_UID, fixture_lines, mission_end_index, split_into_parts, write_part

pytestmark = pytest.mark.django_db

type Row = tuple[str, str, str, int, int, int, float]


class Live:
    """A temp log folder holding the growing fixture mission, a tracker and a controllable clock."""

    def __init__(self, tmp_path: Path, *, live: LiveConfig | None = None) -> None:
        self.logs = tmp_path / "logs"
        self.logs.mkdir(parents=True)
        base = make_config(tmp_path / "data", self.logs)
        self.cfg: Config = replace(base, live=live or LiveConfig(enabled=True, interval_s=30.0))
        self.tracker = LiveTracker(self.cfg)
        self.clock = T0
        lines = fixture_lines()
        self.lines = lines
        self.before_end = lines[: mission_end_index(lines)]
        self.parts = split_into_parts(self.before_end, 4)

    def write(self, count: int, uid: str = LIVE_UID, *, whole: bool = True) -> None:
        """Write parts 0..count-1 completely (the last one half-written unless `whole`)."""
        for index in range(count):
            lines = self.parts[index]
            if index == count - 1 and not whole:
                write_part(self.logs, uid, index, lines[: len(lines) // 2], self.clock, newline_at_end=False)
            else:
                write_part(self.logs, uid, index, lines, self.clock)

    def tick(self, seconds: float = 30.0) -> None:
        self.clock += timedelta(seconds=seconds)
        self.tracker.tick(self.clock)


def rows() -> list[Row]:
    """The saved player rows, without ids and times, in a stable order."""
    return sorted(
        (p.account_uuid, p.state, p.aircraft_type, p.kills_air, p.kills_ground, p.coalition, round(p.flight_time_s, 3))
        for p in LivePlayer.objects.all()
    )


@pytest.fixture
def live(tmp_path: Path) -> Live:
    return Live(tmp_path)


def test_a_tick_saves_the_running_mission_and_who_is_on(live: Live) -> None:
    live.write(2)
    live.tick()

    mission = LiveMission.objects.get()
    assert mission.is_running
    assert mission.mission_uid == LIVE_UID
    assert mission.server_uid == live.cfg.server_uid
    assert mission.started_at == datetime(2026, 9, 19, 21, 0, tzinfo=UTC)
    assert mission.mission_file
    assert mission.elapsed_s > 0
    assert mission.updated_at == live.clock
    assert mission.interval_s == 30.0
    assert LivePlayer.objects.count() > 0
    assert live.tracker.following == LIVE_UID


def test_growing_across_ticks_and_parts_equals_reading_everything_at_once(live: Live, tmp_path: Path) -> None:
    live.write(1, whole=False)
    live.tick()
    live.write(1)
    live.tick()
    live.write(2, whole=False)
    live.tick()
    live.write(3)
    live.tick()
    live.write(4)
    live.tick()
    incremental = rows()
    assert incremental

    fresh = Live(tmp_path / "fresh")
    fresh.clock = live.clock
    fresh.write(4)
    LivePlayer.objects.all().delete()
    LiveMission.objects.all().delete()
    fresh.tick(0)
    assert rows() == incremental


def test_a_restarted_watch_rereads_the_parts_and_lands_on_the_same_snapshot(live: Live) -> None:
    live.write(3)
    live.tick()
    before = rows()

    live.tracker = LiveTracker(live.cfg)  # the watch process restarted: no memory of offsets or the replay
    live.tick()
    assert rows() == before
    assert live.tracker.following == LIVE_UID


def test_a_part_that_shrinks_restarts_the_reading_cleanly(live: Live) -> None:
    live.write(3)
    live.tick()
    live.write(2)  # a part vanished (replaced by a shorter set): the offsets no longer fit
    (live.logs / f"missionReport({LIVE_UID})[2].txt").unlink()
    live.tick()
    after_shrink = rows()

    fresh = LiveTracker(live.cfg)
    LivePlayer.objects.all().delete()
    fresh.tick(live.clock)
    assert rows() == after_shrink


def test_snapshots_are_taken_every_interval_not_every_tick(live: Live) -> None:
    live.write(1)
    live.tick()
    first = live.clock
    live.write(2)
    live.tick(5)  # new lines are read, but a snapshot isn't due yet
    assert LiveMission.objects.get().updated_at == first
    live.tick(20)  # 25 s after the first: due (a tick a little early still counts)
    assert LiveMission.objects.get().updated_at == live.clock


def test_live_writes_never_bump_the_data_version(live: Live) -> None:
    before = current_data_version()
    live.write(3)
    live.tick()
    live.write(4)
    live.tick()
    assert LivePlayer.objects.count() > 0
    assert current_data_version() == before


def test_the_mission_end_clears_the_players_and_stops_reading(live: Live) -> None:
    live.write(4)
    live.tick()
    assert LivePlayer.objects.exists()

    last = len(live.parts) - 1
    write_part(live.logs, LIVE_UID, last, [*live.parts[last], *live.lines[mission_end_index(live.lines) :]], live.clock)
    live.tick()

    mission = LiveMission.objects.get()
    assert not mission.is_running
    assert mission.mission_uid == LIVE_UID  # kept: "last seen"
    assert mission.updated_at == live.clock
    assert not LivePlayer.objects.exists()
    assert live.tracker.following is None

    stamp = mission.updated_at
    live.tick()
    assert LiveMission.objects.get().updated_at == stamp  # not re-read, not rewritten


def test_ingest_taking_the_files_away_clears_the_live_state(live: Live) -> None:
    live.write(3)
    live.tick()
    assert LivePlayer.objects.exists()

    for path in live.logs.iterdir():  # the normal ingest moved the originals
        path.unlink()
    live.tick()

    assert not LiveMission.objects.get().is_running
    assert not LivePlayer.objects.exists()
    assert live.tracker.following is None


def test_a_completed_mission_by_idle_time_is_not_followed(live: Live) -> None:
    live.write(2)
    live.clock += timedelta(minutes=11)  # idle_minutes is 10: ingest will take it
    assert find_in_progress(live.cfg, live.clock) is None
    live.tracker.tick(live.clock)
    assert not LiveMission.objects.filter(is_running=True).exists()


def test_a_newer_mission_replaces_the_followed_one(live: Live) -> None:
    live.write(3)
    live.tick()
    first = rows()

    other = "2026-09-19_22-30-00"
    write_part(live.logs, other, 0, live.parts[0], live.clock)
    live.tick()

    assert LiveMission.objects.get().mission_uid == other
    assert live.tracker.following == other
    assert first
    assert rows() != first


def test_no_log_folder_or_an_empty_one_writes_nothing_but_the_flag(live: Live) -> None:
    live.tick()
    assert not LiveMission.objects.exists()
    assert find_in_progress(replace(live.cfg, logs=replace(live.cfg.logs, dir=None)), live.clock) is None


def test_known_players_are_linked_and_names_come_from_the_log(live: Live) -> None:
    live.write(3)
    live.tick()
    someone = LivePlayer.objects.exclude(name="").first()
    assert someone is not None
    assert someone.player_id is None
    Player.objects.create(
        account_uuid=someone.account_uuid,
        current_name="Known",
        name_lower="known",
        first_seen=live.clock,
        last_seen=live.clock,
    )
    live.tick()
    linked = LivePlayer.objects.get(account_uuid=someone.account_uuid)
    assert linked.player is not None
    assert linked.name == someone.name


def test_aircraft_names_and_propulsion_come_from_the_catalog(live: Live) -> None:
    live.write(4)
    live.tick()
    flying = LivePlayer.objects.exclude(aircraft_type="")
    assert flying.exists()
    assert all(p.aircraft_name for p in flying)
    assert {p.propulsion for p in flying} <= {"prop", "jet", ""}


# --- watch -------------------------------------------------------------------------------------------------------


def watch_cfg(live: Live, *, interval_s: float = 30.0, enabled: bool = True) -> Config:
    return replace(
        live.cfg,
        ingest=replace(live.cfg.ingest, watch_interval_s=0.01),
        live=LiveConfig(enabled=enabled, interval_s=interval_s),
    )


def test_watch_takes_a_live_snapshot_after_each_ingest_tick(live: Live) -> None:
    live.write(3)
    pipeline = make_pipeline(FakeSteps())
    assert watch(watch_cfg(live), pipeline, max_ticks=1, now=lambda: live.clock) == 1
    assert LiveMission.objects.get().is_running
    assert LivePlayer.objects.exists()


def test_watch_with_live_disabled_writes_nothing(live: Live) -> None:
    live.write(3)
    pipeline = make_pipeline(FakeSteps())
    watch(watch_cfg(live, enabled=False), pipeline, max_ticks=1, now=lambda: live.clock)
    assert not LiveMission.objects.exists()


def test_watch_keeps_going_when_a_live_tick_fails(live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: LiveTracker, now: datetime) -> None:
        raise RuntimeError("live tick crashed")

    def busy(self: LiveTracker, now: datetime) -> None:
        raise OperationalError("database is locked")

    pipeline = make_pipeline(FakeSteps())
    monkeypatch.setattr(LiveTracker, "tick", boom)
    assert watch(watch_cfg(live), pipeline, max_ticks=2, now=lambda: live.clock) == 2
    monkeypatch.setattr(LiveTracker, "tick", busy)
    assert watch(watch_cfg(live), pipeline, max_ticks=2, now=lambda: live.clock) == 2


def test_watch_looks_at_the_mission_every_live_interval_while_waiting(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[datetime] = []

    def record(self: LiveTracker, now: datetime) -> None:
        calls.append(now)

    monkeypatch.setattr(LiveTracker, "tick", record)
    cfg = watch_cfg(live, interval_s=0.02)
    cfg = replace(cfg, ingest=replace(cfg.ingest, watch_interval_s=0.2))
    pipeline = make_pipeline(FakeSteps())

    assert watch(cfg, pipeline, max_ticks=2, now=lambda: live.clock) == 2

    assert len(calls) >= 5  # one after each of the 2 ingest ticks, and several in the wait between them
