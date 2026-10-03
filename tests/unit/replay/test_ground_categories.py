"""Ground kills by category, and how many were static (OQ-33, doc 13)."""

from il2ks.core.replay.result import SortieResult
from tests.unit.replay.builder import Scenario, by_acct


def kill_ground(sc: Scenario, t: float, oid: int, object_type: str) -> None:
    sc.declare(0, oid, object_type, 501)
    sc.damage(t, 100, oid, 1.0)
    sc.kill(t + 1, 100, oid)


def test_ground_kills_split_by_category_and_static() -> None:
    sc = Scenario()
    sc.fly_a()
    kill_ground(sc, 10, 401, "M46 Patton")  # dynamic tank
    kill_ground(sc, 20, 402, "GAZ_63")  # a static truck: a vehicle, and static
    kill_ground(sc, 30, 403, "GAZ_63")
    kill_ground(sc, 40, 404, "Military tent A2")
    kill_ground(sc, 50, 405, "Cargo ship 1")  # a dynamic ship
    kill_ground(sc, 60, 406, "Uncategorised static")  # no catalog category: other
    kill_ground(sc, 70, 407, "Brand new thing")  # not in the catalog at all: ground and other, not static
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert a.kills_ground == 7
    assert dict(a.kills_ground_by_category) == {
        "tank": 1,
        "vehicle": 2,
        "building": 1,
        "ship": 1,
        "other": 2,
    }
    assert a.kills_ground_static == 4  # two trucks, the tent, the uncategorised static


def test_categories_sum_to_ground_kills_and_air_kills_are_not_ground() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.damage(5, 100, 300, 1.0)
    sc.kill(6, 100, 300)
    kill_ground(sc, 10, 401, "M46 Patton")
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert (a.kills_air, a.kills_ground) == (1, 1)
    assert_consistent(a)


def test_assists_and_friendly_kills_are_not_in_the_ground_breakdown() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 402, "GAZ_63", 601)  # own side's truck
    sc.damage(10, 100, 402, 1.0)
    sc.kill(11, 100, 402)
    sc.declare(0, 401, "M46 Patton", 501)
    sc.damage(20, 100, 401, 0.5)  # assist only: someone else finishes it
    sc.damage(21, 300, 401, 1.0)
    sc.kill(22, 300, 401)
    sc.end(200, 100, 101)
    a = by_acct(sc.result(), 1)
    assert a.kills_ground == 0
    assert dict(a.kills_ground_by_category) == {}
    assert a.kills_ground_static == 0
    assert_consistent(a)


def assert_consistent(sortie: SortieResult) -> None:
    assert sum(sortie.kills_ground_by_category.values()) == sortie.kills_ground
    assert sortie.kills_ground_static <= sortie.kills_ground
