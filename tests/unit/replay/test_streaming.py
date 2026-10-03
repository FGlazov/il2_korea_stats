"""Streaming equivalence and live snapshots (TD-07): feed + finish == run, and snapshot() never raises."""

from pathlib import Path

import pytest

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.core.logparse.events import LogEvent
from il2ks.core.logparse.files import MissionLog, parse_mission
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.state import Replay, run
from tests.conftest import FIXTURE_LOGS
from tests.unit.replay.builder import FAR, GROUND, NO, FakeCatalog, Scenario

FIXTURES = sorted(FIXTURE_LOGS.glob("*.txt.zip"))


def busy_scenario() -> Scenario:
    """A bit of everything: kill with assist, bailout, disconnect, re-declaration, mission end."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.fly(500, 501, 3, aircraft_type="MiG-15bis", country=501)
    sc.fly(600, 601, 4)
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.damage(50, 500, 100, 0.3)
    sc.damage(51, 200, 100, 0.7)
    sc.kill(52, 200, 100)
    sc.end(52.1, 100, 101)
    sc.remove_bot(52.1, 101, FAR)
    sc.damage(60, 200, 600, 0.2)
    sc.end(70, 0, 601)
    sc.remove_bot(70, 601, FAR)
    sc.kill(70.5, NO, 600)
    sc.disconnect(80, 3)
    sc.remove_bot(81, 501, FAR)
    sc.land(90, 200)
    sc.declare(91, 200, "MiG-15bis", 501, pos=GROUND)
    sc.mission_end(100)
    sc.end(100.1, 200, 201)
    return sc


def test_feed_one_by_one_equals_run() -> None:
    sc = busy_scenario()
    replay = Replay(FakeCatalog())
    for event in sc.events:
        replay.feed(event)
    assert replay.finish() == run(sc.events, FakeCatalog())


def test_snapshot_never_raises_and_does_not_change_the_result() -> None:
    sc = busy_scenario()
    replay = Replay(FakeCatalog())
    for event in sc.events:
        replay.feed(event)
        snapshot = replay.snapshot()
        assert snapshot.tick == event.tick or snapshot.tick > event.tick
    assert replay.finish() == run(sc.events, FakeCatalog())


def test_snapshot_before_any_event() -> None:
    assert Replay(FakeCatalog()).snapshot().sorties == ()
    assert Replay(FakeCatalog()).finish().sorties == ()


def test_run_is_deterministic() -> None:
    sc = busy_scenario()
    assert run(sc.events, FakeCatalog()) == run(sc.events, FakeCatalog())


def fixture_events(path: Path) -> list[LogEvent]:
    return list(parse_mission(MissionLog("fixture", "archive", (path,)), ParseStats()))


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_fixture_streaming_equivalence(path: Path) -> None:
    events = fixture_events(path)
    catalog = load_default_catalog()
    replay = Replay(catalog)
    for index, event in enumerate(events):
        replay.feed(event)
        if index % 5000 == 0:
            replay.snapshot()
    assert replay.finish() == run(events, catalog)


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_fixture_result_is_sane(path: Path) -> None:
    result = run(fixture_events(path), load_default_catalog())
    assert result.mission.mission_file
    assert not result.unknown_object_types
    for sortie in result.sorties:
        assert sortie.end_tick >= sortie.spawn_tick
        assert 0.0 <= sortie.damage_taken <= 1.0
        assert sortie.flight_time_s >= 0.0
        assert [e.tick for e in sortie.timeline] == sorted(e.tick for e in sortie.timeline)
        if sortie.is_death:
            assert sortie.pilot_status == "dead"
        if sortie.loss_cause != "none":
            assert sortie.is_plane_lost
