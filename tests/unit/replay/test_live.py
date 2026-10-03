"""`LiveReplay`: the online-now view over a streaming replay (FR-ING-12, TD-07)."""

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
        if isinstance(event, PlayerConnectEvent):
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
