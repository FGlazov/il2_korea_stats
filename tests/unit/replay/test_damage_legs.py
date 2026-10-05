"""Aircraft damage per flight leg (maintainer 2026-10-05, doc 13 "Damage per flight leg").

Damage TAKEN is tracked per leg: it accumulates up to 100% and resets at a landing that is followed by another takeoff
(an assumed repair; no log event says so, so it only happens with `replay.resupply_allowed`). The exchange table's
dealt / taken numbers use the capped amounts; damage to the pilot or crew is not aircraft damage.

Cast (builder.py): A = acct 1, aircraft 100, bot 101; B = acct 2, aircraft 200; C = acct 3, aircraft 500; AI 300.
"""

import pytest

from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import DamageExchange, MissionResult, SortieResult
from tests.unit.replay.builder import Scenario, by_acct

NO_REPAIR = ReplayRules(resupply_allowed=False)


def _scenario(*, third: bool = False) -> Scenario:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    if third:
        sc.fly(500, 501, 3, aircraft_type="MiG-15bis", country=501)
    return sc


def _resupply(sc: Scenario, *, hits: tuple[tuple[float, float], ...], second: tuple[float, ...] = ()) -> None:
    """A is hit by B at the given (time, amount) pairs, lands at 100 s, takes off again at 200 s, is hit again by B at
    the `second` amounts (240 s on, 5 s apart)."""
    for t, amount in hits:
        sc.damage(t, 200, 100, amount)
    sc.land(100, 100)
    sc.takeoff(200, 100)
    for i, amount in enumerate(second):
        sc.damage(240 + 5 * i, 200, 100, amount)


def _finish(sc: Scenario, rules: ReplayRules | None = None) -> MissionResult:
    sc.end(300, 100, 101)
    sc.end(300, 200, 201)
    return sc.result(rules)


def _exchange(sortie: SortieResult, counterpart_type: str, index: int | None) -> DamageExchange:
    (found,) = [
        e
        for e in sortie.damage
        if e.counterpart.object_type == counterpart_type and e.counterpart.sortie_index == index
    ]
    return found


def _b_vs_a(result: MissionResult) -> tuple[DamageExchange, DamageExchange]:
    """(what A took from B, what B dealt to A)."""
    a, b = by_acct(result, 1), by_acct(result, 2)
    return _exchange(a, "MiG-15bis", b.index), _exchange(b, "F-86A-5", a.index)


# --- the cap ----------


def test_hits_summing_over_100_percent_cap_at_100_for_both_sides() -> None:
    sc = _scenario()
    sc.damage(50, 200, 100, 0.6)
    sc.damage(60, 200, 100, 0.6)
    sc.damage(70, 200, 100, 0.6)
    taken, dealt = _b_vs_a(_finish(sc))
    assert taken.damage_taken == pytest.approx(1.0)
    assert dealt.damage_dealt == pytest.approx(1.0)


def test_damage_to_the_pilot_is_not_aircraft_damage() -> None:
    sc = _scenario()
    sc.damage(50, 200, 100, 0.5)
    sc.damage(51, 200, 101, 0.4)  # the pilot bot
    taken, dealt = _b_vs_a(_finish(sc))
    assert taken.damage_taken == pytest.approx(0.5)
    assert dealt.damage_dealt == pytest.approx(0.5)


def test_hits_on_an_aircraft_that_is_already_at_100_percent_count_nothing() -> None:
    sc = _scenario()
    sc.damage(50, 200, 100, 0.8)
    sc.damage(51, 200, 100, 0.8)  # 0.2 of room left
    sc.kill(52, 200, 100)
    sc.damage(53, 200, 100, 0.5)  # the wreck: the replay already drops lines after AType 3
    result = _finish(sc)
    taken, dealt = _b_vs_a(result)
    assert (taken.damage_taken, dealt.damage_dealt) == (pytest.approx(1.0), pytest.approx(1.0))
    assert by_acct(result, 1).damage_taken == 1.0


def test_the_counterparts_split_one_aircraft_so_the_shares_sum_to_at_most_100_percent() -> None:
    sc = _scenario(third=True)
    sc.damage(50, 200, 100, 0.7)
    sc.damage(60, 500, 100, 0.7)  # only 0.3 of room left
    sc.end(300, 500, 501)
    result = _finish(sc)
    a = by_acct(result, 1)
    shares = {e.counterpart.sortie_index: e.damage_taken for e in a.damage}
    assert shares[by_acct(result, 2).index] == pytest.approx(0.7)
    assert shares[by_acct(result, 3).index] == pytest.approx(0.3)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_an_ai_aircraft_takes_at_most_one_whole_health() -> None:
    sc = _scenario()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.damage(50, 100, 300, 0.7)
    sc.damage(60, 100, 300, 0.7)
    a = by_acct(_finish(sc), 1)
    (entry,) = [e for e in a.damage if e.counterpart.sortie_index is None]
    assert entry.damage_dealt == pytest.approx(1.0)


# --- a landing repairs ----------


def test_a_landing_followed_by_a_takeoff_resets_the_damage() -> None:
    sc = _scenario()
    _resupply(sc, hits=((50, 0.7),), second=(0.7,))
    result = _finish(sc)
    taken, dealt = _b_vs_a(result)
    # 70% of the first leg and 70% of the second: the row sums over both legs, each leg is capped at 100% by itself
    assert taken.damage_taken == pytest.approx(1.4)
    assert dealt.damage_dealt == pytest.approx(1.4)
    a = by_acct(result, 1)
    assert a.damage_taken == pytest.approx(0.7)  # the state at the end: the last leg only


def test_each_leg_caps_at_100_percent_by_itself() -> None:
    sc = _scenario()
    _resupply(sc, hits=((50, 0.8), (51, 0.8)), second=(0.9, 0.9))
    taken, dealt = _b_vs_a(_finish(sc))
    assert taken.damage_taken == pytest.approx(2.0)
    assert dealt.damage_dealt == pytest.approx(2.0)


def test_a_repaired_aircraft_is_unharmed_at_the_end() -> None:
    sc = _scenario()
    _resupply(sc, hits=((50, 0.5),))
    a = by_acct(_finish(sc), 1)
    assert (a.damage_taken, a.aircraft_status) == (0.0, "unharmed")


def test_a_landing_without_a_second_takeoff_is_no_repair() -> None:
    sc = _scenario()
    sc.damage(50, 200, 100, 0.5)
    sc.land(100, 100)
    sc.damage(150, 200, 100, 0.4)  # strafed on the ground
    taken, _ = _b_vs_a(_finish(sc))
    assert taken.damage_taken == pytest.approx(0.9)


def test_without_resupply_the_damage_accumulates_over_the_whole_sortie_up_to_100_percent() -> None:
    sc = _scenario()
    _resupply(sc, hits=((50, 0.7),), second=(0.7,))
    result = _finish(sc, NO_REPAIR)
    taken, dealt = _b_vs_a(result)
    assert taken.damage_taken == pytest.approx(1.0)
    assert dealt.damage_dealt == pytest.approx(1.0)
    assert by_acct(result, 1).damage_taken == pytest.approx(1.0)


# --- timeline ----------


def test_the_timeline_shows_the_repair_at_the_landing_of_a_damaged_aircraft() -> None:
    sc = _scenario()
    _resupply(sc, hits=((50, 0.5),))
    a = by_acct(_finish(sc), 1)
    kinds = [e.kind for e in a.timeline if e.kind in ("landing", "repaired", "takeoff")]
    assert kinds == ["takeoff", "landing", "repaired", "takeoff"]
    landing = next(e for e in a.timeline if e.kind == "landing")
    repaired = next(e for e in a.timeline if e.kind == "repaired")
    assert repaired.tick == landing.tick


def test_no_repair_row_for_an_undamaged_aircraft() -> None:
    sc = _scenario()
    _resupply(sc, hits=())
    a = by_acct(_finish(sc), 1)
    assert all(e.kind != "repaired" for e in a.timeline)


def test_no_repair_row_when_resupply_is_off() -> None:
    sc = _scenario()
    _resupply(sc, hits=((50, 0.5),))
    a = by_acct(_finish(sc, NO_REPAIR), 1)
    assert all(e.kind != "repaired" for e in a.timeline)


def test_no_repair_row_for_a_final_landing() -> None:
    sc = _scenario()
    sc.damage(50, 200, 100, 0.5)
    sc.land(100, 100)
    a = by_acct(_finish(sc), 1)
    assert all(e.kind != "repaired" for e in a.timeline)
