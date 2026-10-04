"""Accuracy (doc 13 "Accuracy"): rounds fired = gun rounds loaded - left, only where "left" can be trusted; gun hits
split by target (aircraft vs everything else), counting bullets and shells but not ordnance."""

from il2ks.core.replay.ammo import rounds_fired
from il2ks.core.replay.result import AmmoCounts, SortieResult
from tests.unit.replay.builder import NO, Scenario, by_acct

SHELL = "SHELL_23_RUS_HET"
BOMB_HIT = "BOMB_100kg_RUS_FAB100"  # a named ordnance hit line: not a gun hit


def sortie_with_hits(*, resupply: bool = False, hits: int = 10) -> Scenario:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.declare(0, 300, "M46 Patton", 501)
    for i in range(hits):
        sc.hit(20 + i, 100, 200, "BULLET_12-7_USA_API")  # on the other player's aircraft
    for i in range(4):
        sc.hit(50 + i, 100, 300, SHELL)  # shells on a tank
    sc.hit(60, 100, 300, BOMB_HIT)
    sc.hit(61, 100, 300, "explosion")
    sc.hit(62, 100, 100, "BULLET_12-7_USA_API")  # on itself: never counted
    if resupply:
        sc.land(70, 100)
        sc.takeoff(80, 100)
    sc.end(120, 100, 101)
    return sc


def pilot(sc: Scenario) -> SortieResult:
    return by_acct(sc.result(), 1)


def test_rounds_are_loaded_minus_left_and_hits_split_by_target() -> None:
    a = pilot(sortie_with_hits())
    assert a.rounds_fired == (100 + 50) - (40 + 20)  # the builder loads 100 + 50 and leaves 40 + 20
    # shells count as gun hits; a bomb line, an explosion and a hit on itself do not
    assert (a.gun_hits_air, a.gun_hits_ground) == (10, 4)


def test_a_resupplied_sortie_has_no_rounds_but_keeps_its_hits() -> None:
    a = pilot(sortie_with_hits(resupply=True))
    assert a.resupplied
    assert a.rounds_fired is None
    assert (a.gun_hits_air, a.gun_hits_ground) == (10, 4)


def test_a_sortie_ended_long_after_the_loss_has_no_rounds() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.hit(20, 100, 200)
    sc.damage(100, NO, 100, 1.0)
    sc.kill(100, NO, 100)
    sc.end(125, 100, 101)
    a = pilot(sc)
    assert a.ammo_left_after_loss
    assert a.rounds_fired is None
    assert a.gun_hits_air == 1


def counts(bullets: int, shells: int = 0) -> AmmoCounts:
    return AmmoCounts(bullets=bullets, shells=shells)


def fired(
    loaded: AmmoCounts,
    left: AmmoCounts | None,
    *,
    resupplied: bool = False,
    left_after_loss: bool = False,
    gun_hits: int = 0,
) -> int | None:
    return rounds_fired(loaded, left, resupplied=resupplied, left_after_loss=left_after_loss, gun_hits=gun_hits)


def test_rounds_fired_rules() -> None:
    assert fired(counts(200), counts(150)) == 50
    assert fired(counts(200, 30), counts(150, 10)) == 70  # a log that fills `SH` too
    assert fired(counts(200), counts(200)) == 0  # fired nothing: a known zero, not unknown
    assert fired(counts(200), None) is None  # no AType 4
    assert fired(counts(200), counts(150), resupplied=True) is None
    assert fired(counts(200), counts(150), left_after_loss=True) is None
    assert fired(counts(200), counts(250)) is None  # more left than loaded
    assert fired(counts(200), counts(150), gun_hits=50) == 50
    assert fired(counts(200), counts(150), gun_hits=51) is None  # more hits than rounds
