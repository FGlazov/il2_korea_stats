"""Level-2 aggregates: incremental updates must equal a full rebuild from level 1 (TD-08, doc 08)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from il2ks.db.models import Counters, Player, PlayerAircraft, PlayerName
from il2ks.ingest.aggregates import rebuild_aggregates, subtract_mission
from il2ks.ingest.counters import COUNTER_FIELDS, FLOAT_COUNTERS
from tests.factories import STARTED_AT, account, meta, mission, reindexed, save, sortie

pytestmark = pytest.mark.django_db


def level2() -> dict[str, list[dict[str, object]]]:
    """Level-2 state keyed by natural keys (PlayerAircraft PKs may differ after a rebuild; nothing links to them)."""

    def norm(row: dict[str, object]) -> dict[str, object]:
        return {k: pytest.approx(v) if k in FLOAT_COUNTERS else v for k, v in row.items()}

    players = [norm(r) for r in Player.objects.order_by("account_uuid").values()]
    names = list(PlayerName.objects.order_by("player_id", "name").values())
    aircraft = [
        norm(r)
        for r in PlayerAircraft.objects.order_by("player_id", "aircraft_id").values(
            "player_id", "aircraft_id", *COUNTER_FIELDS
        )
    ]
    return {"players": players, "names": names, "aircraft": aircraft}


def test_registry_covers_exactly_the_counter_fields() -> None:
    """The three counter tables share `db.models.Counters`; the registry must define every one of its fields."""
    model_fields = [f.name for f in Counters._meta.get_fields()]
    assert list(COUNTER_FIELDS) == model_fields


def test_incremental_equals_rebuild_over_overlapping_missions() -> None:
    """Key aggregate test: several missions, overlapping players, renames, a re-ingest, an out-of-order mission."""
    m1 = mission(
        (
            sortie(0, 1, kills_air=2, flight_time_s=1234.56),
            sortie(1, 2, aircraft_type="F-86A-5", coalition=2, is_death=True, is_plane_lost=True, outcome="shot_down"),
            sortie(2, 3, aircraft_type="Turret_IL10", role="gunner"),
            sortie(3, 1, aircraft_type="Il-10", kills_ground=3, flight_time_s=777.7),
        )
    )
    m2 = mission(
        (
            sortie(0, 1, name="Player-1-renamed", assists=1, flight_time_s=333.3),
            sortie(1, 2, aircraft_type="F-86A-5", coalition=2, pilot_fate="bailed_out", outcome="crashed"),
            sortie(2, 4, aircraft_type="Brand-New Jet", coalition=2, kills_air=1, flight_time_s=0.02),
        )
    )
    m3 = mission(
        (
            sortie(0, 2, name="Player-2-old", aircraft_type="F-51D", coalition=2, kills_ground=1),
            sortie(1, 3, flight_time_s=98.76),
        )
    )
    save(m1, meta("2026-09-19_22-34-13", STARTED_AT))
    save(m2, meta("2026-09-20_22-00-00", STARTED_AT + timedelta(days=1)))
    # An older mission ingested last: current names must still come from the latest sortie by time.
    save(m3, meta("2026-09-18_20-00-00", STARTED_AT - timedelta(days=1)))
    # Late parts for m1: player 2's sortie gone, player 1's Il-10 sortie changed, player 5 new.
    m1b = mission(
        reindexed(
            (
                m1.sorties[0],
                m1.sorties[2],
                replace(m1.sorties[3], kills_ground=1, is_death=True),
                sortie(4, 5, aircraft_type="F-51D", coalition=2),
            )
        )
    )
    save(m1b, meta("2026-09-19_22-34-13", STARTED_AT))
    incremental = level2()

    rebuild_aggregates()

    assert level2() == incremental
    p1 = Player.objects.get(account_uuid=account(1))
    assert p1.current_name == "Player-1-renamed"
    assert (p1.sorties, p1.kills_air, p1.kills_ground, p1.assists, p1.deaths) == (3, 2, 1, 1, 1)
    p2 = Player.objects.get(account_uuid=account(2))
    assert p2.current_name == "Player-2"  # Player-2-old was the earliest mission
    assert set(PlayerName.objects.filter(player=p2).values_list("name", flat=True)) == {"Player-2", "Player-2-old"}
    assert (p2.sorties, p2.deaths, p2.bailouts, p2.kills_ground) == (2, 0, 1, 1)
    assert p2.first_seen == STARTED_AT - timedelta(days=1) + timedelta(seconds=1000 / 50)
    p3 = Player.objects.get(account_uuid=account(3))
    assert p3.sorties == 1  # gunner sortie not counted (FR-WEB-14)
    assert PlayerAircraft.objects.filter(player=p3).count() == 1


def test_rebuild_repairs_drifted_counters() -> None:
    save(mission((sortie(0, 1, kills_air=2), sortie(1, 2, aircraft_type="F-86A-5", coalition=2))))
    expected = level2()
    Player.objects.update(kills_air=99, current_name="wrong", name_lower="wrong")
    PlayerAircraft.objects.update(sorties=42)
    PlayerName.objects.all().delete()

    rebuild_aggregates()

    names = level2()["names"]
    assert [n["name"] for n in names] == ["Player-1", "Player-2"]
    for got, want in zip(names, expected["names"], strict=True):
        assert {k: v for k, v in got.items() if k != "id"} == {k: v for k, v in want.items() if k != "id"}
    assert level2()["players"] == expected["players"]
    assert level2()["aircraft"] == expected["aircraft"]


def test_subtract_then_rebuild_leaves_no_contribution() -> None:
    m = save(mission((sortie(0, 1, kills_air=2, flight_time_s=0.1), sortie(1, 1, aircraft_type="Il-10"))))
    subtract_mission(m)

    p = Player.objects.get()
    assert all(getattr(p, f) == 0 for f in COUNTER_FIELDS)
    assert not PlayerAircraft.objects.exists()
