"""The ammo breakdown in the database (FR-WEB-18): the sortie JSON, hits to destroy, and the reads for the pages."""

from dataclasses import replace
from typing import cast

import pytest

from il2ks.core.replay.result import (
    UNATTRIBUTED_ORDNANCE,
    AmmoHits,
    MissionResult,
    OrdnanceUse,
    SingleAttackerKill,
    UnattributedDamage,
)
from il2ks.db.models import (
    TOTAL_AMMO,
    AircraftAmmoStats,
    GameObject,
    MissionAircraftAmmo,
    PlayerSortie,
)
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.persist import aircraft_ammo_totals
from il2ks.queries.ammo import aircraft_ammo, all_aircraft_ammo, ordnance_name, parse_sortie_ammo, sortie_ammo_by_pk
from tests.factories import meta, mission, save, sortie

pytestmark = pytest.mark.django_db

API = "BULLET_12-7_USA_API"
INC = "BULLET_12-7_USA_INC"
SHELL = "SHELL_23_RUS_HET"


def kills(victim: str, *rows: tuple[tuple[str, int], ...]) -> tuple[SingleAttackerKill, ...]:
    return tuple(SingleAttackerKill(victim, tuple(sorted(r))) for r in rows)


def with_kills(result: MissionResult, *items: SingleAttackerKill) -> MissionResult:
    return replace(result, single_attacker_kills=items)


def stats() -> dict[tuple[str, str], tuple[int, int]]:
    return {
        (r.aircraft.log_name, r.ammo): (r.kills, r.hits) for r in AircraftAmmoStats.objects.select_related("aircraft")
    }


def level1() -> dict[tuple[str, str, str], tuple[int, int]]:
    return {
        (r.mission.mission_uid, r.aircraft.log_name, r.ammo): (r.kills, r.hits)
        for r in MissionAircraftAmmo.objects.select_related("mission", "aircraft")
    }


# --- the sortie JSON ------------------------------------------------------------------------------------------------


def test_ammo_json_carries_damage_unattributed_and_ordnance() -> None:
    s = replace(
        sortie(0, 1),
        ammo_hits=(AmmoHits(API, 5, 2, 0.4, 0.12345678), AmmoHits("BOMB_238kg_USA_M64", 1, 0)),
        ammo_unattributed=UnattributedDamage(0.25, 0.0),
        ordnance=(
            OrdnanceUse("M64", released=2, detonations=3, targets_damaged=4, kills=1, damage_dealt=0.7, direct_hits=1),
        ),
    )
    save(mission((s,)))
    ammo: dict[str, object] = PlayerSortie.objects.get().ammo
    assert cast(list[object], ammo["hits"])[0] == {
        "ammo": API,
        "hits_given": 5,
        "hits_received": 2,
        "damage_dealt": 0.4,
        "damage_taken": 0.1235,
    }
    assert ammo["unattributed"] == {"dealt": 0.25, "taken": 0.0}
    assert ammo["ordnance"] == [
        {
            "ordnance": "M64",
            "released": 2,
            "detonations": 3,
            "targets_damaged": 4,
            "kills": 1,
            "damage_dealt": 0.7,
            "damage_taken": 0.0,
            "direct_hits": 1,
        }
    ]
    assert {"loaded", "left", "used"} <= set(ammo)  # the v1 keys stay


def test_the_page_read_splits_guns_from_named_ordnance_and_names_the_ordnance() -> None:
    s = replace(
        sortie(0, 1),
        ammo_hits=(AmmoHits(API, 5, 2, 0.4, 0.1), AmmoHits("BOMB_238kg_USA_M64", 1, 0), AmmoHits("NapalmBullet", 7, 0)),
        ammo_unattributed=UnattributedDamage(0.25, 0.5),
        ordnance=(
            OrdnanceUse("M64", released=2, detonations=3, targets_damaged=4, kills=1, direct_hits=1),
            OrdnanceUse(UNATTRIBUTED_ORDNANCE, detonations=9),
            OrdnanceUse("bombs_mixed", released=1),
        ),
    )
    save(mission((s,)))
    ammo = sortie_ammo_by_pk(PlayerSortie.objects.get().pk)
    assert ammo is not None
    assert [g.ammo for g in ammo.guns] == [API]
    assert ammo.guns[0].damage_dealt == 0.4
    assert {h.ammo for h in ammo.other_hit_lines} == {"BOMB_238kg_USA_M64", "NapalmBullet"}
    names = {o.ordnance: o.name for o in ammo.ordnance}
    assert names["M64"] == "M64 500 lb General Purpose bomb"
    assert names["bombs_mixed"] == "Bombs (mixed loadout)"
    assert "explosion" not in " ".join(names.values()).lower()
    assert (ammo.unattributed_dealt, ammo.unattributed_taken) == (0.25, 0.5)
    assert ammo.has_ordnance
    assert ordnance_name("never-heard-of-it") == "never-heard-of-it"


def test_a_row_written_before_the_attribution_still_reads() -> None:
    old: dict[str, object] = {
        "loaded": {},
        "left": None,
        "used": {},
        "hits": [{"ammo": API, "hits_given": 3, "hits_received": 1}],
    }
    ammo = parse_sortie_ammo(old)
    assert [(g.ammo, g.hits_given, g.damage_dealt) for g in ammo.guns] == [(API, 3, 0.0)]
    assert ammo.ordnance == ()
    assert not ammo.has_ordnance
    assert parse_sortie_ammo({}).guns == ()


# --- hits to destroy ------------------------------------------------------------------------------------------------


def test_a_mission_counts_a_kill_for_each_ammo_that_hit_and_for_the_total() -> None:
    totals = aircraft_ammo_totals(kills("MiG-15bis", ((API, 3), (INC, 1)), ((API, 2),)))
    assert totals == {
        ("MiG-15bis", TOTAL_AMMO): (2, 6),
        ("MiG-15bis", API): (2, 5),
        ("MiG-15bis", INC): (1, 1),
    }


def test_saving_a_mission_writes_level_1_and_level_2() -> None:
    result = with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 3), (INC, 1)), ((API, 2),)))
    save(result)
    assert level1() == {
        ("2026-09-19_22-34-13", "MiG-15bis", TOTAL_AMMO): (2, 6),
        ("2026-09-19_22-34-13", "MiG-15bis", API): (2, 5),
        ("2026-09-19_22-34-13", "MiG-15bis", INC): (1, 1),
    }
    assert stats() == {("MiG-15bis", TOTAL_AMMO): (2, 6), ("MiG-15bis", API): (2, 5), ("MiG-15bis", INC): (1, 1)}


def test_level_2_sums_missions_and_a_resave_replaces_the_missions_rows() -> None:
    one = with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 4),)))
    two = with_kills(
        mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 2), (SHELL, 2))), *kills("F-86A-5", ((SHELL, 3),))
    )
    save(one, meta("2026-09-19_22-34-13"))
    save(two, meta("2026-09-20_22-34-13"))
    assert stats() == {
        ("MiG-15bis", TOTAL_AMMO): (2, 8),
        ("MiG-15bis", API): (2, 6),
        ("MiG-15bis", SHELL): (1, 2),
        ("F-86A-5", TOTAL_AMMO): (1, 3),
        ("F-86A-5", SHELL): (1, 3),
    }
    # reprocess the second mission with different kills: its old rows go, other missions stay, the F-86 rows vanish
    again = with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((INC, 5),)))
    save(again, meta("2026-09-20_22-34-13"))
    assert stats() == {
        ("MiG-15bis", TOTAL_AMMO): (2, 9),
        ("MiG-15bis", API): (1, 4),
        ("MiG-15bis", INC): (1, 5),
    }
    save(with_kills(mission((sortie(0, 1),))), meta("2026-09-20_22-34-13"))  # no counted kill at all
    assert stats() == {("MiG-15bis", TOTAL_AMMO): (1, 4), ("MiG-15bis", API): (1, 4)}


def test_incremental_level_2_equals_a_rebuild_and_a_rebuild_repairs_drift() -> None:
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 4),))), meta("2026-09-19_22-34-13"))
    save(
        with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 1), (INC, 2))), *kills("B-29", ((SHELL, 9),))),
        meta("2026-09-20_22-34-13"),
    )
    before = stats()
    pks = sorted(AircraftAmmoStats.objects.values_list("pk", flat=True))
    rebuild_aggregates()
    assert stats() == before
    assert sorted(AircraftAmmoStats.objects.values_list("pk", flat=True)) == pks  # upserted, not recreated
    AircraftAmmoStats.objects.filter(ammo=API).update(hits=999)
    AircraftAmmoStats.objects.filter(ammo=SHELL).delete()
    rebuild_aggregates()
    assert stats() == before


def test_a_rebuild_drops_level_2_rows_without_level_1_rows() -> None:
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 4),))))
    MissionAircraftAmmo.objects.all().delete()
    rebuild_aggregates()
    assert stats() == {}


# --- the reads for the aircraft page --------------------------------------------------------------------------------


def test_aircraft_reads_give_average_hits_per_ammo_and_the_total() -> None:
    save(
        with_kills(
            mission((sortie(0, 1),)),
            *kills("MiG-15bis", ((API, 3), (INC, 1)), ((API, 5),)),
            *kills("F-86A-5", ((SHELL, 2),)),
        )
    )
    mig = aircraft_ammo(GameObject.objects.get(log_name="MiG-15bis"))
    assert mig.total is not None
    assert (mig.total.kills, mig.total.hits, mig.total.average_hits) == (2, 9, 4.5)
    assert [(a.ammo, a.kills, a.average_hits) for a in mig.by_ammo] == [(API, 2, 4.0), (INC, 1, 1.0)]
    assert [a.aircraft.log_name for a in all_aircraft_ammo()] == ["MiG-15bis", "F-86A-5"]  # the most killed first
    untouched = GameObject.objects.create(log_name="Zzz", display_name="Zzz")
    nothing = aircraft_ammo(untouched)
    assert (nothing.total, nothing.by_ammo) == (None, ())
