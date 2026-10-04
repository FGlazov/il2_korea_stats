"""Hits on the sortie timeline (FR-WEB-6, hits.py): significant bursts, ammo of the closest hit, caps.

Same cast as test_ammo.py: player A (aircraft 100) and B (200), AI aircraft 300, AI tank 400, plus a static 450.
"""

import pytest

from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.hits import HIT_GIVEN, HIT_TAKEN, TIMELINE_MAX_HITS, DamageEvent, hit_entries
from il2ks.core.replay.result import Counterpart, MissionResult, SortieResult, TimelineEntry
from il2ks.core.replay.state import run
from tests.unit.replay.builder import Scenario, by_acct, tick
from tests.unit.replay.test_ammo import AmmoCatalog

API = "BULLET_12-7_USA_API"
INC = "BULLET_12-7_USA_INC"


def scenario() -> Scenario:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.declare(0, 400, "M46 Patton", 501)
    sc.declare(0, 450, "Military tent A2", 501)
    return sc


def finish(sc: Scenario, rules: ReplayRules | None = None) -> MissionResult:
    sc.end(300, 100, 101)
    sc.end(300, 200, 201)
    return run(sc.events, AmmoCatalog(), rules)


def hits(sortie: SortieResult) -> list[TimelineEntry]:
    return [e for e in sortie.timeline if e.kind in (HIT_GIVEN, HIT_TAKEN)]


def burst(sc: Scenario, start: float, attacker: int, target: int, lines: int, each: float, ammo: str = API) -> None:
    """`lines` damage lines 0.2 s apart, each preceded by a hit line of `ammo`."""
    for i in range(lines):
        t = start + 0.2 * i
        sc.hit(t, attacker, target, ammo)
        sc.damage(t + 0.02, attacker, target, each)


# --- significant hits ----------


def test_a_burst_is_one_row_with_the_summed_damage_for_both_sides() -> None:
    sc = scenario()
    burst(sc, 100, 100, 200, 10, 0.0004)  # 10 lines of 0.04% = 0.4%
    result = finish(sc)
    (given,) = hits(by_acct(result, 1))
    (taken,) = hits(by_acct(result, 2))
    assert (given.kind, taken.kind) == (HIT_GIVEN, HIT_TAKEN)
    assert given.damage == pytest.approx(0.004)
    assert given.lines == 10
    assert given.tick == tick(100.02)
    assert given.counterpart == Counterpart("MiG-15bis", 1, 1)  # B's sortie index, coalition 1
    assert taken.counterpart == Counterpart("F-86A-5", 0, 2)


def test_a_burst_under_the_threshold_is_left_out_and_the_threshold_is_a_rule() -> None:
    sc = scenario()
    burst(sc, 100, 100, 300, 3, 0.0003)  # 0.09%
    assert hits(by_acct(finish(sc), 1)) == []
    low = by_acct(finish(sc, ReplayRules(hit_min_damage=0.0005)), 1)
    assert len(hits(low)) == 1


def test_a_gap_splits_bursts_and_different_targets_never_share_one() -> None:
    sc = scenario()
    burst(sc, 100, 100, 300, 5, 0.002)
    burst(sc, 110, 100, 300, 5, 0.002)  # 9 s later: a second burst
    burst(sc, 120, 100, 400, 5, 0.002)  # another target
    rows = hits(by_acct(finish(sc), 1))
    assert [(r.counterpart.object_type, r.lines) for r in rows if r.counterpart] == [
        ("MiG-15bis", 5),
        ("MiG-15bis", 5),
        ("M46 Patton", 5),
    ]


def test_a_long_burst_is_cut_after_the_longest_span() -> None:
    sc = scenario()
    burst(sc, 100, 100, 300, 100, 0.001)  # 20 s of fire, a line every 0.2 s
    rows = hits(by_acct(finish(sc), 1))
    assert len(rows) == 2  # 15 s + the rest
    assert sum(r.lines for r in rows) == 100


def test_damage_to_scenery_and_to_oneself_has_no_row() -> None:
    sc = scenario()
    burst(sc, 100, 100, 450, 5, 0.05)
    sc.damage(120, 100, 100, 0.5)
    assert hits(by_acct(finish(sc), 1)) == []


def test_only_the_heaviest_rows_are_kept_in_time_order() -> None:
    events = [
        DamageEvent(tick(t), True, t, Counterpart("MiG-15bis"), 0.01 + (t % 7) * 0.001, None)
        for t in range(0, 20 * (TIMELINE_MAX_HITS + 5), 20)
    ]
    rows = hit_entries(events, ReplayRules())
    assert len(rows) == TIMELINE_MAX_HITS
    assert [r.tick for r in rows] == sorted(r.tick for r in rows)
    lightest = [e for e in events if e.amount == 0.01]
    assert sum(1 for r in rows if r.damage == 0.01) == len(lightest) - 5  # the five dropped are the lightest


# --- ammo ----------


def test_the_row_takes_the_ammo_of_the_closest_hit_like_the_breakdown() -> None:
    sc = scenario()
    burst(sc, 100, 100, 300, 8, 0.001, INC)
    (row,) = hits(by_acct(finish(sc), 1))
    assert (row.ammo, row.ammo_kind) == (INC, "gun")


def test_the_ammo_with_the_most_damage_in_the_burst_wins_and_a_tie_the_earlier() -> None:
    sc = scenario()
    burst(sc, 100, 100, 300, 3, 0.002, API)
    burst(sc, 101, 100, 300, 3, 0.002, INC)  # same burst (1 s later), equal damage: the earlier API
    burst(sc, 101.7, 100, 300, 1, 0.002, INC)  # now INC has more
    (row,) = hits(by_acct(finish(sc), 1))
    assert row.ammo == INC
    sc = scenario()
    burst(sc, 100, 100, 300, 3, 0.002, API)
    burst(sc, 101, 100, 300, 3, 0.002, INC)
    (tie,) = hits(by_acct(finish(sc), 1))
    assert tie.ammo == API


def test_ammo_comes_from_the_same_attacker_and_target_pair_only() -> None:
    sc = scenario()
    sc.hit(100.0, 200, 300, INC)  # someone else's hit on the target
    sc.hit(100.0, 100, 400, INC)  # a hit by A on another target
    sc.damage(100.0, 100, 300, 0.05)
    (row,) = hits(by_acct(finish(sc), 1))
    assert (row.ammo, row.ammo_kind) == ("", "")  # shown, but with no ammo


def test_taken_hits_carry_the_attackers_ammo() -> None:
    sc = scenario()
    burst(sc, 100, 200, 100, 4, 0.002, INC)
    (row,) = hits(by_acct(finish(sc), 1))
    assert (row.kind, row.ammo) == (HIT_TAKEN, INC)


def test_ordnance_is_named_by_its_key() -> None:
    sc = scenario()
    sc.hit(100.0, 100, 400, "BOMB_238kg_USA_M64")
    sc.damage(100.0, 100, 400, 0.6)
    (row,) = hits(by_acct(finish(sc), 1))
    assert (row.ammo_kind, row.ammo) == ("ordnance", "M64")


# --- order and size ----------


def test_a_hit_sorts_before_the_kill_it_led_to_and_the_row_count_is_small() -> None:
    sc = scenario()
    burst(sc, 100, 100, 300, 5, 0.1)
    sc.kill(100.9, 100, 300)
    kinds = [e.kind for e in by_acct(finish(sc), 1).timeline]
    assert kinds.index(HIT_GIVEN) < kinds.index("kill")
