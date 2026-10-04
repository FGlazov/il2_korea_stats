"""Ammo attribution (FR-WEB-18): closest-hit damage per gun ammo, explosion labelling, ordnance use, hits to destroy.

Player A (account 1, F-86A-5, aircraft 100) shoots and bombs; B (MiG-15bis, aircraft 200) is the player target, AI
aircraft 300 (MiG-15bis) and AI tank 400 are the others (builder.py).
"""

from dataclasses import replace
from pathlib import Path

import pytest

import il2ks.core.catalog
from il2ks.core.catalog.loader import PayloadInfo, parse_ordnance
from il2ks.core.logparse.events import (
    ObjectId,
    PlayerSpawnEvent,
    Pos,
    RocketFiredEvent,
    SortieEndEvent,
    StoreReleaseEvent,
)
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import UNATTRIBUTED_ORDNANCE, MissionResult, OrdnanceUse, SortieResult
from il2ks.core.replay.state import run
from tests.unit.replay.builder import FakeCatalog, Scenario, by_acct, tick

API = "BULLET_12-7_USA_API"
INC = "BULLET_12-7_USA_INC"
SHELL = "SHELL_23_RUS_HET"
HVAR_HIT = "RKT_127mm_USA_HVAR_HIT"
M64_HIT = "BOMB_238kg_USA_M64"

ORDNANCE = parse_ordnance(
    (Path(il2ks.core.catalog.__file__).parent / "data" / "ordnance.csv").read_text(encoding="utf-8")
)
PAYLOADS = [
    PayloadInfo("f-86a", 1, "HVAR-8", "8 x HVAR"),  # one damaging type
    PayloadInfo("f-86a", 2, "M64-2 + HVAR-4", "bomb and rocket"),  # two types, each one of its class
    PayloadInfo("f-86a", 3, "NAP_110GAL-2 + M64-2", "napalm and bomb"),  # the store class holds two types
    PayloadInfo("f-86a", 4, "F86_120GAL-2 + M64-2", "tanks and a bomb"),
    PayloadInfo("f-86a", 5, "HVAR-4 + TINYTIM-2", "two rocket types"),
    PayloadInfo("f-86a", 6, "Empty", "Empty"),
    PayloadInfo("f-86a", 7, "NAP_110GAL-2 + HVAR-4", "napalm and rockets"),
    PayloadInfo("f-86a", 8, "NAP_110GAL-2", "napalm only"),
]


class AmmoCatalog(FakeCatalog):
    def __init__(self) -> None:
        super().__init__(payloads=PAYLOADS, payload_aliases={"F-86A-5": "f-86a"}, ordnance=ORDNANCE)


def loadout(sc: Scenario, payload_id: int, *, bombs: int = 0, rockets: int = 0, acct: int = 1) -> None:
    """Give player `acct`'s spawn a payload (and the AType 10 counts)."""
    sc.events = [
        replace(e, payload_id=payload_id, bombs=bombs, rockets=rockets)
        if isinstance(e, PlayerSpawnEvent) and e.account_uuid == f"account-{acct}"
        else e
        for e in sc.events
    ]


def release_store(sc: Scenario, t: float, aircraft: int = 100) -> None:
    sc.add(
        StoreReleaseEvent(tick=tick(t), object_id=ObjectId(aircraft), pos=Pos(1.0, 1000.0, 1.0), store_id=ObjectId(1))
    )


def release_rocket(sc: Scenario, t: float, aircraft: int = 100) -> None:
    sc.add(
        RocketFiredEvent(tick=tick(t), object_id=ObjectId(aircraft), pos=Pos(1.0, 1000.0, 1.0), rocket_id=ObjectId(2))
    )


def explode(sc: Scenario, t: float, *targets: int, attacker: int = 100) -> None:
    for target in targets:
        sc.hit(t, attacker, target, "explosion")


def scenario() -> Scenario:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.declare(0, 400, "M46 Patton", 501)
    return sc


def finish(sc: Scenario, rules: ReplayRules | None = None) -> MissionResult:
    sc.end(300, 100, 101)
    sc.end(300, 200, 201)
    return run(sc.events, AmmoCatalog(), rules)


def ordnance(sortie: SortieResult) -> dict[str, OrdnanceUse]:
    return {o.ordnance: o for o in sortie.ordnance}


def gun_damage(sortie: SortieResult) -> dict[str, float]:
    return {h.ammo: h.damage_dealt for h in sortie.ammo_hits if h.damage_dealt}


# --- closest-hit rule for guns --------------------------------------------------------------------------------------


def test_damage_takes_the_ammo_of_the_closest_hit_either_side() -> None:
    sc = scenario()
    sc.hit(100.00, 100, 300, API)
    sc.hit(100.40, 100, 300, INC)  # 0.1 s after the damage line; the API hit is 0.3 s before it
    sc.damage(100.30, 100, 300, 0.25)
    a = by_acct(finish(sc), 1)
    assert gun_damage(a) == {INC: pytest.approx(0.25)}
    assert a.ammo_unattributed.dealt == 0.0


def test_a_tie_goes_to_the_earlier_hit_and_the_same_tick_beats_both() -> None:
    sc = scenario()
    sc.hit(100.0, 100, 300, API)
    sc.hit(100.2, 100, 300, INC)
    sc.damage(100.1, 100, 300, 0.1)  # 0.1 s from each: the earlier one
    sc.hit(110.0, 100, 300, API)
    sc.hit(110.1, 100, 300, INC)
    sc.damage(110.1, 100, 300, 0.2)  # the INC hit is on the same tick
    a = by_acct(finish(sc), 1)
    assert gun_damage(a) == {API: pytest.approx(0.1), INC: pytest.approx(0.2)}


def test_damage_with_no_hit_in_the_window_is_unattributed_and_the_window_is_a_rule() -> None:
    sc = scenario()
    sc.hit(100.0, 100, 300, API)
    sc.damage(101.5, 100, 300, 0.3)  # 1.5 s after the only hit
    result = finish(sc)
    a = by_acct(result, 1)
    assert gun_damage(a) == {}
    assert a.ammo_unattributed.dealt == pytest.approx(0.3)
    wide = by_acct(run(sc.events, AmmoCatalog(), ReplayRules(ammo_window_s=2.0)), 1)
    assert gun_damage(wide) == {API: pytest.approx(0.3)}
    assert wide.ammo_unattributed.dealt == 0.0


def test_only_the_same_attacker_and_target_count() -> None:
    sc = scenario()
    sc.hit(100.0, 200, 300, INC)  # another attacker's hit on the same target
    sc.hit(100.0, 100, 400, INC)  # the same attacker's hit on another target
    sc.damage(100.0, 100, 300, 0.2)
    a = by_acct(finish(sc), 1)
    assert gun_damage(a) == {}
    assert a.ammo_unattributed.dealt == pytest.approx(0.2)


def test_damage_taken_is_attributed_to_the_ammo_that_hit_the_victim() -> None:
    sc = scenario()
    sc.hit(100.0, 200, 100, API)
    sc.damage(100.0, 200, 100, 0.4)
    sc.hit(120.0, 300, 100, SHELL)  # an AI aircraft's shell
    sc.damage(120.0, 300, 100, 0.1)
    a = by_acct(finish(sc), 1)
    rows = {h.ammo: h for h in a.ammo_hits}
    assert rows[API].damage_taken == pytest.approx(0.4)
    assert rows[SHELL].damage_taken == pytest.approx(0.1)
    assert rows[API].hits_received == 1


def test_attributed_damage_adds_up_to_the_damage_dealt() -> None:
    sc = scenario()
    sc.hit(100, 100, 300, API)
    sc.damage(100, 100, 300, 0.3)
    sc.damage(150, 100, 300, 0.2)  # no hit near
    sc.hit(160, 100, 400, M64_HIT)
    sc.damage(160, 100, 400, 0.5)  # a named ordnance hit
    a = by_acct(finish(sc), 1)
    total = sum(d.damage_dealt for d in a.damage)
    parts = (
        sum(h.damage_dealt for h in a.ammo_hits) + sum(o.damage_dealt for o in a.ordnance) + a.ammo_unattributed.dealt
    )
    assert total == pytest.approx(1.0)
    assert parts == pytest.approx(total)


# --- labelling an explosion -----------------------------------------------------------------------------------------


def test_explosions_are_never_hits_and_collapse_to_detonations() -> None:
    sc = scenario()
    loadout(sc, 1, rockets=8)
    for _ in range(3):  # the log writes several lines per detonation and target
        explode(sc, 100, 300, 400)
    explode(sc, 105, 300)
    a = by_acct(finish(sc), 1)
    assert all("explosion" not in h.ammo for h in a.ammo_hits)
    assert not a.ammo_hits
    assert a.damage == ()
    assert ordnance(a)["HVAR"].detonations == 2


def test_rule_1_a_named_ordnance_line_within_a_second_names_the_explosion() -> None:
    sc = scenario()
    loadout(sc, 2, bombs=2, rockets=4)  # two types: rule 2 can't decide
    sc.hit(100.5, 100, 300, HVAR_HIT)
    explode(sc, 100, 400)
    sc.damage(100, 100, 400, 0.4)
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["HVAR"].detonations == 1
    assert ordnance(a)["HVAR"].damage_dealt == pytest.approx(0.4)
    assert ordnance(a)["HVAR"].direct_hits == 1


def test_rule_1_a_named_line_beyond_the_window_is_ignored() -> None:
    sc = scenario()
    loadout(sc, 2, bombs=2, rockets=4)
    sc.hit(101.5, 100, 300, HVAR_HIT)
    explode(sc, 100, 400)
    a = by_acct(finish(sc), 1)
    assert ordnance(a)[UNATTRIBUTED_ORDNANCE].detonations == 1


def test_rule_1_a_shell_line_makes_the_explosion_a_gun_hit() -> None:
    sc = scenario()
    loadout(sc, 1, rockets=8)  # rule 2 would say HVAR; the shell line comes first
    sc.hit(100.0, 100, 300, SHELL)
    explode(sc, 100.02, 300)
    sc.damage(100.02, 100, 300, 0.3)
    a = by_acct(finish(sc), 1)
    assert not a.ordnance
    assert {h.ammo: h.damage_dealt for h in a.ammo_hits}[SHELL] == pytest.approx(0.3)


def test_a_bullet_line_does_not_name_an_explosion() -> None:
    sc = scenario()
    loadout(sc, 1, rockets=8)
    sc.hit(100.0, 100, 300, API)  # bullets don't explode
    explode(sc, 100.0, 400)
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["HVAR"].detonations == 1  # rule 2


def test_rule_2_a_single_ordnance_type_in_the_loadout() -> None:
    sc = scenario()
    loadout(sc, 1, rockets=8)
    explode(sc, 100, 400)
    sc.damage(100, 100, 400, 0.5)
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["HVAR"].detonations == 1
    assert ordnance(a)["HVAR"].damage_dealt == pytest.approx(0.5)


def test_rule_3_the_type_of_a_recent_release() -> None:
    sc = scenario()
    loadout(sc, 2, bombs=2, rockets=4)
    release_store(sc, 90)  # the only bomb type in the loadout
    explode(sc, 100, 400)
    release_rocket(sc, 150)
    explode(sc, 152, 400)
    result = finish(sc)
    a = by_acct(result, 1)
    assert ordnance(a)["M64"].detonations == 1
    assert ordnance(a)["HVAR"].detonations == 1
    assert (ordnance(a)["M64"].released, ordnance(a)["HVAR"].released) == (1, 1)


def test_rule_3_a_release_more_than_a_minute_before_is_too_old() -> None:
    sc = scenario()
    loadout(sc, 2, bombs=2, rockets=4)
    release_store(sc, 30)
    explode(sc, 100, 400)  # 70 s later
    a = by_acct(finish(sc), 1)
    assert ordnance(a)[UNATTRIBUTED_ORDNANCE].detonations == 1
    assert ordnance(a)["M64"].detonations == 0


def test_rule_3_a_release_of_one_of_several_types_is_generic() -> None:
    sc = scenario()
    loadout(sc, 5, rockets=6)  # HVAR and Tiny Tim
    release_rocket(sc, 95)
    explode(sc, 100, 400)
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["rockets_mixed"].detonations == 1
    assert ordnance(a)["rockets_mixed"].released == 1


def test_rule_4_napalm_lingers_after_its_release() -> None:
    sc = scenario()
    loadout(sc, 7, bombs=2, rockets=4)  # napalm and rockets
    release_store(sc, 10)  # the napalm
    explode(sc, 100, 400)  # long after: the fire
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["NAPALM"].detonations == 1
    assert ordnance(a)["NAPALM"].released == 1


def test_rule_4_wins_over_a_release_that_could_be_napalm_or_a_bomb() -> None:
    """Napalm and a bomb share AType 25, so the release has no specific type and napalm fire is the likelier source."""
    sc = scenario()
    loadout(sc, 3, bombs=4)
    release_store(sc, 95)
    explode(sc, 100, 400)
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["NAPALM"].detonations == 1
    assert ordnance(a)["bombs_mixed"].released == 1


def test_rule_5_unattributed_never_explosion() -> None:
    sc = scenario()
    loadout(sc, 6)  # empty
    explode(sc, 100, 400)
    sc.damage(100, 100, 400, 0.4)
    a = by_acct(finish(sc), 1)
    assert set(ordnance(a)) == {UNATTRIBUTED_ORDNANCE}
    assert ordnance(a)[UNATTRIBUTED_ORDNANCE].detonations == 1
    assert a.ammo_unattributed.dealt == pytest.approx(0.4)  # the damage can't be named either


def test_an_unknown_payload_labels_by_release_class_from_the_ammo_counts() -> None:
    sc = scenario()
    loadout(sc, 99, bombs=2)  # not in the payload file
    release_store(sc, 90)
    explode(sc, 100, 400)
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["bombs_mixed"].detonations == 1


# --- ordnance counters ----------------------------------------------------------------------------------------------


def test_targets_damaged_counts_a_detonation_and_target_only_if_it_took_damage() -> None:
    sc = scenario()
    loadout(sc, 1, rockets=8)
    explode(sc, 100, 300, 400)  # one detonation, two targets
    sc.damage(100, 100, 400, 0.2)  # only the tank is hurt
    sc.damage(100.02, 100, 400, 0.1)  # a second line on the same target and detonation: still one
    explode(sc, 110, 400)
    sc.damage(110, 100, 400, 0.3)  # a second detonation on the tank
    a = by_acct(finish(sc), 1)
    use = ordnance(a)["HVAR"]
    assert (use.detonations, use.targets_damaged) == (2, 2)
    assert use.damage_dealt == pytest.approx(0.6)


def test_kills_credit_the_ordnance_of_the_last_damage_line() -> None:
    sc = scenario()
    loadout(sc, 2, bombs=2, rockets=4)
    sc.hit(100, 100, 400, API)
    sc.damage(100, 100, 400, 0.5)  # a gun hit first
    release_rocket(sc, 118)
    explode(sc, 120, 400)
    sc.damage(120, 100, 400, 0.6)
    sc.kill(120, 100, 400)
    a = by_acct(finish(sc), 1)
    assert a.kills_ground == 1
    assert ordnance(a)["HVAR"].kills == 1
    assert gun_damage(a) == {API: pytest.approx(0.5)}


def test_a_gun_kill_is_not_an_ordnance_kill() -> None:
    sc = scenario()
    loadout(sc, 1, rockets=8)
    sc.hit(100, 100, 400, API)
    sc.damage(100, 100, 400, 1.0)
    sc.kill(100, 100, 400)
    a = by_acct(finish(sc), 1)
    assert a.kills_ground == 1
    assert not a.ordnance or ordnance(a)["HVAR"].kills == 0


def test_direct_hits_count_the_named_lines_and_keep_listing_them_as_hits() -> None:
    sc = scenario()
    loadout(sc, 2, bombs=2, rockets=4)
    sc.hit(100, 100, 400, M64_HIT)
    sc.hit(100.02, 100, 400, M64_HIT)
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["M64"].direct_hits == 2
    assert {h.ammo: h.hits_given for h in a.ammo_hits}[M64_HIT] == 2


def test_releases_of_drop_tanks_are_not_bombs() -> None:
    """Tanks and bombs share AType 25: of four release lines, the two bombs the ammo counts say were used."""
    sc = scenario()
    loadout(sc, 4, bombs=2)  # two tanks and two bombs
    for t in (50, 51, 52, 53):
        release_store(sc, t)
    a = by_acct(finish(sc), 1)  # the builder's AType 4 reports 0 bombs left of 2
    assert ordnance(a)["M64"].released == 2


def test_tanks_dropped_alone_leave_the_bomb_count_at_zero() -> None:
    sc = scenario()
    loadout(sc, 4, bombs=2)
    release_store(sc, 50)
    release_store(sc, 51)
    sc.end(300, 100, 101)
    events = [replace(e, bombs=2) if isinstance(e, SortieEndEvent) and e.bot_id == 101 else e for e in sc.events]
    sc.events = events
    a = by_acct(finish(sc), 1)
    assert ordnance(a)["M64"].released == 0  # both bombs were still on board at AType 4


def test_rocket_salvos_are_counted_per_type() -> None:
    sc = scenario()
    loadout(sc, 1, rockets=8)
    for t in (90, 91, 92):
        release_rocket(sc, t)
    result = finish(sc)
    assert ordnance(by_acct(result, 1))["HVAR"].released == 3
    assert by_acct(result, 2).ordnance == ()


def test_a_player_without_explosions_or_releases_has_no_ordnance() -> None:
    sc = scenario()
    sc.hit(100, 100, 300, API)
    sc.damage(100, 100, 300, 0.2)
    b = by_acct(finish(sc), 2)
    assert b.ordnance == ()
    assert b.ammo_unattributed.dealt == 0.0


# --- hits to destroy ------------------------------------------------------------------------------------------------


def test_single_attacker_kill_lists_the_gun_hits_per_ammo() -> None:
    sc = scenario()
    for t in (100.0, 100.1, 100.2):
        sc.hit(t, 100, 300, API)
    sc.hit(100.3, 100, 300, INC)
    sc.damage(100.3, 100, 300, 1.0)
    sc.kill(100.3, 100, 300)
    result = finish(sc)
    (kill,) = result.single_attacker_kills
    assert kill.victim_type == "MiG-15bis"
    assert kill.hits == ((API, 3), (INC, 1))


def test_a_second_attacker_or_the_environment_spoils_the_count() -> None:
    sc = scenario()
    sc.hit(100.0, 100, 300, API)
    sc.damage(100.0, 100, 300, 0.5)
    sc.hit(101.0, 200, 300, API)
    sc.damage(101.0, 200, 300, 0.5)
    sc.kill(101.0, 200, 300)
    sc.declare(0, 301, "MiG-15bis", 501)
    sc.hit(110.0, 100, 301, API)
    sc.damage(110.0, 100, 301, 0.5)
    sc.damage(111.0, -1, 301, 0.5)  # AID:-1, the environment
    sc.kill(111.0, 100, 301)
    assert finish(sc).single_attacker_kills == ()


def test_only_gun_hits_before_the_kill_count_and_not_explosions_or_ordnance() -> None:
    sc = scenario()
    loadout(sc, 1, rockets=8)
    sc.hit(100.0, 100, 300, API)
    sc.hit(100.1, 100, 300, HVAR_HIT)  # a rocket line: not a gun hit
    explode(sc, 100.1, 300)
    sc.damage(100.1, 100, 300, 1.0)
    sc.kill(100.1, 100, 300)
    sc.hit(105.0, 100, 300, API)  # after the destruction
    (kill,) = finish(sc).single_attacker_kills
    assert kill.hits == ((API, 1),)


def test_a_kill_without_gun_hits_and_ground_victims_are_not_listed() -> None:
    sc = scenario()
    sc.damage(100.0, 100, 300, 1.0)
    sc.kill(100.0, 100, 300)
    sc.hit(110.0, 100, 400, API)
    sc.damage(110.0, 100, 400, 1.0)
    sc.kill(110.0, 100, 400)  # a tank
    assert finish(sc).single_attacker_kills == ()


def test_a_player_victim_counts_too() -> None:
    sc = scenario()
    sc.hit(100.0, 100, 200, API)
    sc.damage(100.0, 100, 200, 1.0)
    sc.kill(100.0, 100, 200)
    (kill,) = finish(sc).single_attacker_kills
    assert kill.victim_type == "MiG-15bis"
    assert kill.hits == ((API, 1),)


def test_single_attacker_kill_names_the_victims_sortie_only_for_a_player() -> None:
    """The stats scopes of the aircraft page follow the destroyed aircraft's own role and mods, so a kill of a player's
    aircraft carries that sortie's index; an AI aircraft has none."""
    sc = scenario()
    sc.hit(100.0, 100, 200, API)  # B (a player) is the victim ...
    sc.damage(100.0, 100, 200, 1.0)
    sc.kill(100.0, 100, 200)
    sc.hit(110.0, 100, 300, API)  # ... then the AI MiG
    sc.damage(110.0, 100, 300, 1.0)
    sc.kill(110.0, 100, 300)
    result = finish(sc)
    victim = next(s for s in result.sorties if s.account_uuid.endswith("2"))
    assert [k.victim_sortie_index for k in result.single_attacker_kills] == [victim.index, None]
