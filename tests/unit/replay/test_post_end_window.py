"""`ReplayRules.post_end_destroy_window_s` (300 s, OQ-30) and `post_end_destroy_window_ground_s` (5 s): what a late
destruction of a normally ended aircraft does (FR-ING-22, doc 12 shot-down shape).

The long window is for sorties that ended in the air. A sortie that ended on the ground gets the short one, so a pilot
who landed, despawned and left isn't marked dead when the parked aircraft (or an object reusing its log ID) is
destroyed minutes later.
"""

import pytest

from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult
from tests.unit.replay.builder import FAR, GROUND, Scenario, by_acct


def _late_destruction(rules: ReplayRules | None = None, *, landed: bool, gap_s: float = 60.0) -> MissionResult:
    """Player A ends the sortie normally (AType 4 with the aircraft id), landed or still in the air, and enemy B
    destroys the aircraft `gap_s` seconds later."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    pos = GROUND if landed else FAR
    if landed:
        sc.land(100, 100)
    sc.end(110, 100, 101, pos)
    sc.damage(110 + gap_s - 0.5, 200, 100, 0.5, pos=pos)
    sc.kill(110 + gap_s, 200, 100, pos=pos)
    return sc.result(rules)


def test_default_windows_are_300_s_in_the_air_and_5_s_on_the_ground() -> None:
    rules = ReplayRules()
    assert (rules.post_end_destroy_window_s, rules.post_end_destroy_window_ground_s) == (300.0, 5.0)


def test_parked_aircraft_destroyed_60_s_after_a_landed_end_is_ignored() -> None:
    """The ground guard: the pilot landed and left, so the later destruction is not their loss or death."""
    result = _late_destruction(landed=True)
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.outcome, a.pilot_fate) == ("landed", "in_aircraft")
    assert (a.is_plane_lost, a.is_death, a.loss_cause, a.aircraft_status) == (False, False, "none", "unharmed")
    assert (b.kills_air, result.kills) == (0, ())


def test_parked_aircraft_destroyed_after_a_landed_end_is_a_loss_with_a_300_s_ground_window() -> None:
    """The guard is a setting: widening the ground window brings back the loss and the death."""
    result = _late_destruction(ReplayRules(post_end_destroy_window_ground_s=300.0), landed=True)
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.outcome, a.is_plane_lost, a.is_death, a.loss_cause) == ("shot_down", True, True, "attacker")
    assert b.kills_air == 1


def test_aircraft_destroyed_60_s_after_an_airborne_end_is_a_loss() -> None:
    """The long window applies to a sortie that ended in the air (lag between AType 4 and AType 3)."""
    result = _late_destruction(landed=False)
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.outcome, a.pilot_fate) == ("shot_down", "in_aircraft")
    assert (a.is_plane_lost, a.is_death, a.loss_cause, a.aircraft_status) == (True, True, "attacker", "destroyed")
    assert b.kills_air == 1
    assert [(k.killer_sortie_index, k.victim_sortie_index) for k in result.kills] == [(b.index, a.index)]


def test_aircraft_destroyed_60_s_after_an_airborne_end_is_ignored_with_a_5_s_window() -> None:
    a = by_acct(_late_destruction(ReplayRules(post_end_destroy_window_s=5.0), landed=False), 1)
    assert (a.outcome, a.is_plane_lost, a.is_death, a.loss_cause) == ("unknown", False, False, "none")


def test_aircraft_destroyed_after_the_window_is_ignored_even_when_airborne() -> None:
    a = by_acct(_late_destruction(landed=False, gap_s=400.0), 1)
    assert (a.is_plane_lost, a.is_death) == (False, False)


@pytest.mark.parametrize("landed", [True, False])
@pytest.mark.parametrize("window", [5.0, 300.0])
def test_destruction_inside_a_5_s_window_always_counts(window: float, landed: bool) -> None:
    """The shot-down shape (doc 12): AType 4 first, the aircraft's AType 3 a second later."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    if landed:
        sc.land(90, 100)
    sc.damage(99.5, 200, 100, 0.5)
    sc.end(100, 100, 101)
    sc.kill(101, 200, 100)
    rules = ReplayRules(post_end_destroy_window_s=window, post_end_destroy_window_ground_s=window)
    a = by_acct(sc.result(rules), 1)
    assert (a.outcome, a.is_plane_lost, a.loss_cause) == ("shot_down", True, "attacker")


def test_the_pilots_own_death_follows_the_same_choice() -> None:
    """Pilot bot killed 60 s after a normal end: a death if the aircraft was airborne, not if it was parked."""
    for landed, died in ((False, True), (True, False)):
        sc = Scenario()
        sc.fly_a()
        sc.fly_b()
        if landed:
            sc.land(100, 100)
        sc.end(110, 100, 101)
        sc.kill(170, 200, 101)
        a = by_acct(sc.result(), 1)
        assert (a.is_death, a.is_plane_lost) == (died, died), f"landed={landed}"


def _recycled_ids(*, redeclare_bot: bool) -> MissionResult:
    """A's airborne sortie ends normally at 110 s. 40 s later the game declares a new object under A's aircraft ID (or
    A's pilot ID), and enemy B destroys that new object at 170 s. It has nothing to do with A's sortie."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.end(110, 100, 101, FAR)
    if redeclare_bot:
        sc.declare(150, 101, "BotPlanePilot_Test", 601, parent=-1, pos=FAR)
        sc.kill(170, 200, 101, pos=FAR)
    else:
        sc.declare(150, 100, "F-86A-5", 601, pos=FAR)
        sc.damage(169.5, 200, 100, 0.5, pos=FAR)
        sc.kill(170, 200, 100, pos=FAR)
    return sc.result()


@pytest.mark.parametrize("redeclare_bot", [False, True])
def test_a_recycled_log_id_after_the_sortie_end_is_a_new_object(redeclare_bot: bool) -> None:
    """Even with the 300 s window, an AType 3 on an object that reuses the ended sortie's aircraft or pilot ID is not
    that sortie's loss or death (doc 13 Objects; seen with recycled pilot IDs in the sample missions)."""
    result = _recycled_ids(redeclare_bot=redeclare_bot)
    a = by_acct(result, 1)
    assert (a.is_plane_lost, a.is_death, a.loss_cause, a.aircraft_status) == (False, False, "none", "unharmed")
    assert all(k.victim_sortie_index != a.index for k in result.kills)  # B's kill (if any) is the new object, not A


def test_the_shot_down_re_declaration_right_after_the_sortie_end_is_still_an_update() -> None:
    """Real shape: AType 4, then a same-type AType 12 for the aircraft a few ticks later, then its AType 3."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.damage(99.5, 200, 100, 0.5)
    sc.end(100, 100, 101)
    sc.declare(100.04, 100, "F-86A-5", 601, pos=FAR)
    sc.kill(100.04, 200, 100)
    result = sc.result()
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.outcome, a.is_plane_lost, a.loss_cause) == ("shot_down", True, "attacker")
    assert b.kills_air == 1
