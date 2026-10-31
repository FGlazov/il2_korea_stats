"""`LiveReplay`: the online-now view over a streaming replay (FR-ING-12, TD-07)."""

import re
from itertools import takewhile

import pytest

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.core.logparse.events import PlayerConnectEvent, PlayerDisconnectEvent
from il2ks.core.logparse.parser import ParseStats, parse_lines
from il2ks.core.replay.live import LiveReplay
from il2ks.core.replay.state import Replay
from tests.live_helpers import fixture_lines, mission_end_index

A = "00000000-0000-4000-8000-000000000001"
B = "00000000-0000-4000-8000-000000000002"
NICK = "10000000-0000-4000-8000-000000000001"
DELAY = 10 * 60 * 50  # ten minutes of log ticks


def connect(tick: int, account: str) -> str:
    return f"T:{tick} AType:20 USERID:{account} USERNICKID:{NICK}"


def disconnect(tick: int, account: str) -> str:
    return f"T:{tick} AType:21 USERID:{account} USERNICKID:{NICK}"


def test_connected_players_are_online_until_they_disconnect() -> None:
    live = LiveReplay(load_default_catalog())
    live.feed_lines([connect(10, A), connect(20, B)])
    view = live.snapshot()
    assert {p.account_uuid for p in view.players} == {A, B}
    assert all(p.state == "connected" and p.coalition == 0 and p.aircraft_type == "" for p in view.players)

    live.feed_lines([disconnect(30, A)])
    assert {p.account_uuid for p in live.snapshot().players} == {B}


def test_the_servers_own_connect_at_tick_0_is_not_a_player() -> None:
    """DServer logs its own account connecting at T:0 of every mission; it never spawns or disconnects."""
    live = LiveReplay(load_default_catalog())
    live.feed_lines([connect(0, A), connect(10, B)])
    assert {p.account_uuid for p in live.snapshot().players} == {B}


def test_feeding_in_pieces_equals_feeding_at_once() -> None:
    lines = fixture_lines()[: mission_end_index(fixture_lines())]
    once = LiveReplay(load_default_catalog())
    once.feed_lines(lines)
    pieces = LiveReplay(load_default_catalog())
    for start in range(0, len(lines), 997):
        pieces.feed_lines(lines[start : start + 997])
    assert pieces.snapshot() == once.snapshot()
    assert pieces.lines == once.lines == len([line for line in lines if line.strip()])


@pytest.mark.parametrize("name", ["typical", "most_bailouts", "two_mission_ends"])
def test_online_players_are_those_connected_or_spawned_and_not_disconnected(name: str) -> None:
    lines = fixture_lines(name)
    cut = mission_end_index(lines)
    live = LiveReplay(load_default_catalog())
    live.feed_lines(lines[:cut])
    view = live.snapshot()

    online: set[str] = set()
    for event in parse_lines(lines[:cut], ParseStats()):
        if isinstance(event, PlayerConnectEvent) and event.tick > 0:  # T:0 is the server's own login
            online.add(event.account_uuid)
        elif isinstance(event, PlayerDisconnectEvent):
            online.discard(event.account_uuid)
    assert online <= {p.account_uuid for p in view.players}  # spawned-only accounts may be online beyond those
    assert not view.ended
    assert view.mission is not None
    assert view.tick > 0
    for player in view.players:
        assert (player.state == "connected") == (player.aircraft_type == "")
        assert (player.state == "connected") == (player.sortie_spawn_tick is None)


@pytest.mark.parametrize("name", ["typical", "most_bailouts"])
def test_kill_totals_match_the_final_result_before_the_mission_ends(name: str) -> None:
    """The snapshot uses the final rules: what it counts so far is what `finish()` counts for the same lines."""
    lines = fixture_lines(name)
    cut = mission_end_index(lines)
    live = LiveReplay(load_default_catalog())
    live.feed_lines(lines[:cut])
    replay = Replay(load_default_catalog())
    for event in parse_lines(lines[:cut], ParseStats()):
        replay.feed(event)
    final = replay.finish()

    for player in live.snapshot().players:
        sorties = [s for s in final.sorties if s.account_uuid == player.account_uuid]
        assert player.kills_air == sum(s.kills_air for s in sorties)
        assert player.kills_ground == sum(s.kills_ground for s in sorties)


def test_the_mission_end_marks_the_view_ended() -> None:
    lines = fixture_lines()
    live = LiveReplay(load_default_catalog())
    live.feed_lines(lines)
    assert live.snapshot().ended


def test_open_sorties_get_a_flight_state_and_flight_time() -> None:
    lines = fixture_lines()
    live = LiveReplay(load_default_catalog())
    seen: set[str] = set()
    step = max(1, len(lines) // 40)
    for start in range(0, mission_end_index(lines), step):
        live.feed_lines(lines[start : start + step])
        for player in live.snapshot().players:
            seen.add(player.state)
            if player.state == "in_flight":
                assert player.flight_time_s >= 0
                assert player.coalition in (1, 2)
    assert {"in_flight", "connected"} <= seen


def log_tick(line: str) -> int:
    found = re.match(r"T:(\d+) ", line)
    return int(found.group(1)) if found else 0


def log_until(lines: list[str], tick: int) -> list[str]:
    """The log up to the first line past `tick`: the log's own order, whose ticks can step back a little."""
    return list(takewhile(lambda line: log_tick(line) <= tick, lines))


def running_mission() -> list[str]:
    lines = fixture_lines()
    return lines[: mission_end_index(lines)]


def replay_of(lines: list[str], delay_ticks: int = 0) -> LiveReplay:
    live = LiveReplay(load_default_catalog(), delay_ticks=delay_ticks)
    live.feed_lines(lines)
    return live


def test_a_delayed_snapshot_is_the_mission_as_it_stood_that_long_ago() -> None:
    """FR-ING-25: nothing newer than the delay reaches the sorties or the kills."""
    lines = running_mission()
    cutoff = replay_of(lines).snapshot().tick - DELAY
    held = replay_of(lines, DELAY).snapshot()
    past = replay_of(log_until(lines, cutoff)).snapshot()
    assert held.result == past.result
    assert held.result != replay_of(lines).snapshot().result


def test_who_is_online_stays_current_while_the_details_are_held_back() -> None:
    lines = running_mission()
    cutoff = replay_of(lines).snapshot().tick - DELAY
    now = replay_of(lines).snapshot()
    held = replay_of(lines, DELAY).snapshot()
    past = {p.account_uuid: p for p in replay_of(log_until(lines, cutoff)).snapshot().players}

    assert [(p.account_uuid, p.name) for p in held.players] == [(p.account_uuid, p.name) for p in now.players]
    assert held.tick == now.tick  # "running for" is no secret
    for player in held.players:
        before = past.get(player.account_uuid)
        details = (player.coalition, player.country, player.aircraft_type, player.state, player.flight_time_s)
        if before is None:  # joined within the delay: nothing about them shows yet
            assert details == (0, 0, "", "connected", 0.0)
            assert (player.kills_air, player.kills_ground) == (0, 0)
        else:
            assert details == (
                before.coalition,
                before.country,
                before.aircraft_type,
                before.state,
                before.flight_time_s,
            )
            assert (player.kills_air, player.kills_ground) == (before.kills_air, before.kills_ground)


def test_the_mission_header_is_not_held_back() -> None:
    """The first snapshots of a mission still know which mission it is."""
    assert replay_of(running_mission()[:50], DELAY).snapshot().mission is not None


def test_a_rereading_watch_lands_on_the_same_delayed_state() -> None:
    lines = running_mission()
    pieces = LiveReplay(load_default_catalog(), delay_ticks=DELAY)
    for start in range(0, len(lines), 997):
        pieces.feed_lines(lines[start : start + 997])
    assert pieces.snapshot() == replay_of(lines, DELAY).snapshot()


def test_nothing_is_held_back_once_the_mission_ended() -> None:
    lines = fixture_lines()
    held = replay_of(lines, DELAY)
    assert held.over
    assert held.snapshot() == replay_of(lines).snapshot()
    assert held.snapshot().ended


def test_only_a_released_event_counts_as_new() -> None:
    live = replay_of([connect(10, A)], DELAY)
    assert live.applied == 0
    live.feed_lines([connect(10 + DELAY, B)])
    assert live.applied == 1
