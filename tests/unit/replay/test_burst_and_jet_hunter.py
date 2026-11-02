"""The best burst of ground kills (`multi_kill_ground`) and the jets shot down from a propeller aircraft
(`kills_jet_from_prop`, the Jet Hunter achievement).

A (acct 1, aircraft 100, coalition 2) shoots; B (acct 2, aircraft 200, MiG-15bis) is the enemy player. A flies the
prop P-51D unless a test says otherwise; the catalog here knows which aircraft are jets."""

from dataclasses import replace

from il2ks.core.catalog.loader import Catalog, ObjectInfo, Propulsion
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult
from il2ks.core.replay.state import run
from tests.unit.replay.builder import FakeCatalog, Scenario, by_acct

PROPULSION: dict[str, Propulsion] = {
    "P-51D": "prop",
    "Yak-9": "prop",
    "IL-10": "prop",
    "F-86A-5": "jet",
    "MiG-15bis": "jet",
}
OTHER_AIRCRAFT = {"P-51D": "fighter", "Yak-9": "fighter"}


class _Catalog(FakeCatalog):
    def lookup(self, object_type: str) -> ObjectInfo:
        info = super().lookup(object_type)
        if object_type in OTHER_AIRCRAFT:
            info = ObjectInfo(object_type, object_type, "fighter", is_playable=True, is_known=True)
        return replace(info, propulsion=PROPULSION.get(object_type))


class _Scenario(Scenario):
    def result(self, rules: ReplayRules | None = None) -> MissionResult:
        catalog: Catalog = _Catalog()
        return run(self.events, catalog, rules)


def _shoot(sc: _Scenario, t: float, shooter: int, victim: int) -> None:
    sc.damage(t, shooter, victim, 1.0)
    sc.kill(t + 1, shooter, victim)


def _kill_ground(sc: _Scenario, t: float, oid: int, object_type: str = "M46 Patton", country: int = 501) -> None:
    sc.declare(0, oid, object_type, country)
    _shoot(sc, t, 100, oid)


# --- multi_kill_ground ---


def test_the_ground_burst_is_the_best_run_within_the_window() -> None:
    sc = _Scenario()
    sc.fly_a()
    for i, t in enumerate((100, 110, 125, 150, 400, 520)):  # four kills in 50 s, then two lone kills
        _kill_ground(sc, t, 400 + i)
    sc.end(600, 100, 101)

    a = by_acct(sc.result(), 1)
    assert (a.kills_ground, a.multi_kill_ground, a.multi_kill) == (6, 4, 0)


def test_fences_and_friendly_kills_are_no_part_of_the_ground_burst() -> None:
    sc = _Scenario()
    sc.fly_a()
    _kill_ground(sc, 100, 400)
    _kill_ground(sc, 105, 401, "Fence wire 100m")
    _kill_ground(sc, 110, 402, "GAZ_63", country=601)  # an own-side truck
    sc.end(600, 100, 101)

    a = by_acct(sc.result(), 1)
    assert (a.kills_ground, a.friendly_kills_ground, a.multi_kill_ground) == (1, 1, 1)


def test_the_ground_burst_is_zero_without_ground_kills() -> None:
    sc = _Scenario()
    sc.fly_a()
    sc.declare(0, 300, "MiG-15bis", 501)
    _shoot(sc, 100, 100, 300)
    sc.end(200, 100, 101)

    a = by_acct(sc.result(), 1)
    assert (a.kills_air, a.multi_kill, a.multi_kill_ground) == (1, 1, 0)


# --- kills_jet_from_prop ---


def _duel(shooter_type: str, victim_type: str, *, victim_country: int = 501) -> MissionResult:
    sc = _Scenario()
    sc.fly(100, 101, 1, aircraft_type=shooter_type)
    sc.fly(200, 201, 2, aircraft_type=victim_type, country=victim_country)
    _shoot(sc, 100, 100, 200)
    sc.end(200, 100, 101)
    sc.end(200, 200, 201)
    return sc.result()


def test_a_jet_shot_down_from_a_prop_counts() -> None:
    a = by_acct(_duel("P-51D", "MiG-15bis"), 1)
    assert (a.kills_air, a.kills_jet_from_prop) == (1, 1)


def test_a_jet_shot_down_from_a_jet_does_not_count() -> None:
    a = by_acct(_duel("F-86A-5", "MiG-15bis"), 1)
    assert (a.kills_air, a.kills_jet_from_prop) == (1, 0)


def test_a_prop_shot_down_from_a_prop_does_not_count() -> None:
    a = by_acct(_duel("P-51D", "Yak-9"), 1)
    assert (a.kills_air, a.kills_jet_from_prop) == (1, 0)


def test_an_ai_jet_counts_too() -> None:
    sc = _Scenario()
    sc.fly(100, 101, 1, aircraft_type="P-51D")
    sc.declare(0, 300, "MiG-15bis", 501)
    _shoot(sc, 100, 100, 300)
    sc.end(200, 100, 101)

    a = by_acct(sc.result(), 1)
    assert (a.kills_air_ai, a.kills_jet_from_prop) == (1, 1)


def test_a_friendly_jet_does_not_count() -> None:
    sc = _Scenario()
    sc.fly(100, 101, 1, aircraft_type="P-51D")
    sc.declare(0, 300, "F-86A-5", 601)  # an AI jet of A's own side
    _shoot(sc, 100, 100, 300)
    sc.end(200, 100, 101)

    a = by_acct(sc.result(), 1)
    assert (a.friendly_kills_air, a.kills_air, a.kills_jet_from_prop) == (1, 0, 0)


def test_a_jet_of_the_pilots_own_account_does_not_count() -> None:
    """The pilot, on the other side, still has their earlier jet around when the prop shoots it down."""
    sc = _Scenario()
    sc.fly(200, 201, 1, aircraft_type="MiG-15bis", country=501)
    sc.fly(100, 101, 1, aircraft_type="P-51D", spawn=30, up=35)
    _shoot(sc, 100, 100, 200)
    sc.end(300, 100, 101)

    result = sc.result()
    (prop,) = [s for s in result.sorties if s.aircraft_type == "P-51D"]
    (own,) = [k for k in result.kills if k.victim_object_id == 200]
    assert (own.victim_account_uuid, own.killer_sortie_index, own.is_friendly) == ("account-1", prop.index, False)
    assert (prop.kills_air, prop.kills_jet_from_prop) == (1, 0)


def test_a_gunner_sortie_gets_no_jet_from_prop() -> None:
    sc = _Scenario()
    sc.fly(200, 201, 2, aircraft_type="IL-10", country=501)
    sc.player(1, 500, 501, 3, aircraft_type="Turret_IL10", country=501, parent=200)
    sc.declare(0, 301, "F-86A-5", 601)
    _shoot(sc, 100, 500, 301)
    sc.end(200, 500, 501)
    sc.end(200, 200, 201)

    result = sc.result()
    pilot, gunner = by_acct(result, 2), by_acct(result, 3)
    assert gunner.kills_air == 1
    assert (gunner.kills_jet_from_prop, pilot.kills_jet_from_prop) == (0, 0)
