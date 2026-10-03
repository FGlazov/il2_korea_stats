"""The pilot's final position (AType 16): garbage positions count as missing, a live snapshot between AType 4 and
AType 16 waits for it, and `finish()` falls back to the bot's latest AType 12 position (bailout rule v2, doc 13)."""

import pytest

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.model import POS_MAX_ABS_XZ_M, POS_MAX_Y_M, POS_MIN_Y_M, is_plausible_pos
from il2ks.core.replay.state import Replay
from tests.unit.replay.builder import FAR, FakeCatalog, Scenario, by_acct

CHUTE = Pos(FAR.x + 500, FAR.y - 1500, FAR.z)  # a real position far from the aircraft
GARBAGE = Pos(3.4e38, 12.0, -3.4e38)


def _leave(
    sc: Scenario, *, removal_pos: Pos | None, redeclared_at: Pos | None = None, redeclared_t: float = 109.9
) -> None:
    """AType 4 `PLID:0` at 110 s, an optional bot AType 12, an optional AType 16."""
    if redeclared_at is not None:
        sc.declare(redeclared_t, 101, "BotPlanePilot_Test", 601, parent=100, pos=redeclared_at)
    sc.end(110, 0, 101)
    if removal_pos is not None:
        sc.remove_bot(110.3, 101, removal_pos)


@pytest.mark.parametrize(
    ("pos", "plausible"),
    [
        (Pos(100_000.0, 500.0, 200_000.0), True),
        (Pos(-POS_MAX_ABS_XZ_M, POS_MIN_Y_M, POS_MAX_ABS_XZ_M), True),  # the bounds are inclusive
        (Pos(0.0, POS_MAX_Y_M, 0.0), True),
        (Pos(POS_MAX_ABS_XZ_M + 1, 100.0, 0.0), False),
        (Pos(0.0, 100.0, -POS_MAX_ABS_XZ_M - 1), False),
        (Pos(0.0, POS_MAX_Y_M + 1, 0.0), False),
        (Pos(0.0, POS_MIN_Y_M - 1, 0.0), False),
        (GARBAGE, False),
    ],
)
def test_position_bounds(pos: Pos, *, plausible: bool) -> None:
    assert is_plausible_pos(pos) is plausible


def test_living_pilot_with_a_garbage_removal_position_is_not_a_bailout() -> None:
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=GARBAGE)
    a = by_acct(sc.result(), 1)
    assert a.pilot_fate != "bailed_out"
    assert (a.pilot_fate, a.pilot_fate_source) == ("unknown", "unknown")
    assert (a.is_plane_lost, a.is_death, a.suspected_early_bailout) == (False, False, False)


def test_real_removal_position_still_makes_a_bailout() -> None:
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=CHUTE)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.is_plane_lost) == ("bailed_out", True)


def test_garbage_removal_position_falls_back_to_the_bots_redeclared_position() -> None:
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=GARBAGE, redeclared_at=CHUTE)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "inferred")


def test_finish_without_atype16_uses_the_bot_atype12_near_the_sortie_end() -> None:
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=None, redeclared_at=CHUTE)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("bailed_out", "inferred")
    assert a.is_plane_lost


def test_finish_without_atype16_and_without_a_nearby_atype12_stays_unknown() -> None:
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=None)
    a = by_acct(sc.result(), 1)
    assert (a.pilot_fate, a.pilot_fate_source) == ("unknown", "unknown")
    assert not a.is_plane_lost


def test_an_old_atype12_is_not_near_the_sortie_end() -> None:
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=None, redeclared_at=CHUTE, redeclared_t=60)
    assert by_acct(sc.result(), 1).pilot_fate == "unknown"
    near = ReplayRules(pilot_pos_fallback_window_s=60.0)
    assert by_acct(sc.result(near), 1).pilot_fate == "bailed_out"


def test_garbage_atype12_position_is_not_a_fallback() -> None:
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=None, redeclared_at=GARBAGE)
    assert by_acct(sc.result(), 1).pilot_fate == "unknown"


def test_snapshot_between_atype4_and_atype16_waits_for_the_position() -> None:
    """The gap is at most 0.34 s but a live snapshot can land in it: the fate is provisional, not a final `unknown`."""
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=CHUTE)
    *before, removal = sc.events  # the last event is the AType 16
    replay = Replay(FakeCatalog())
    for event in before:
        replay.feed(event)
    (pending,) = replay.snapshot().sorties
    assert (pending.pilot_fate, pending.pilot_fate_source) == ("in_aircraft", "inferred")  # like an open sortie
    assert (pending.is_plane_lost, pending.is_death, pending.suspected_early_bailout) == (False, False, False)
    replay.feed(removal)
    (done,) = replay.snapshot().sorties
    assert (done.pilot_fate, done.pilot_fate_source) == ("bailed_out", "inferred")
    assert replay.finish().sorties == (done,)


def test_final_result_is_not_provisional_without_atype16() -> None:
    """Only a snapshot waits; `finish()` has the whole log and gives the real `unknown`."""
    sc = Scenario()
    sc.fly_a()
    _leave(sc, removal_pos=None)
    replay = Replay(FakeCatalog())
    for event in sc.events:
        replay.feed(event)
    (snap,) = replay.snapshot().sorties
    assert snap.pilot_fate == "in_aircraft"
    assert replay.finish().sorties[0].pilot_fate == "unknown"
