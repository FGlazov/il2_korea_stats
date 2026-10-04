"""Ammo mixes of the "Hits to destroy" section (FR-WEB-18): level 1, level 2, rebuild equality, reads, page."""

from dataclasses import replace

import pytest
from django.test import Client

from il2ks.core.replay.result import MissionResult, SingleAttackerKill
from il2ks.db.models import MIX_SEPARATOR, TOTAL_AMMO, AircraftAmmoMixStats, GameObject
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.persist import aircraft_ammo_mix_totals
from il2ks.queries.ammo import aircraft_ammo
from tests.factories import meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

API = "BULLET_12-7_USA_API"
INC = "BULLET_12-7_USA_INC"
SHELL = "SHELL_23_RUS_HET"

AIRCRAFT_DETAIL_READS = 12  # + the tour tiles (TourAircraftStats), the mods table (AircraftMods)
"""Budget of /aircraft/<pk>/ with kills: the page's own reads plus the ammo rows and the ammo mixes (one query each)."""


def kills(victim: str, *rows: tuple[tuple[str, int], ...]) -> tuple[SingleAttackerKill, ...]:
    return tuple(SingleAttackerKill(victim, tuple(sorted(r))) for r in rows)


def with_kills(result: MissionResult, *items: SingleAttackerKill) -> MissionResult:
    return replace(result, single_attacker_kills=items)


def mix_stats() -> dict[tuple[str, str, str], tuple[int, int]]:
    return {
        (r.aircraft.log_name, r.mix, r.ammo): (r.kills, r.hits)
        for r in AircraftAmmoMixStats.objects.select_related("aircraft")
    }


def test_a_mission_groups_kills_by_the_set_of_ammo_that_hit() -> None:
    totals = aircraft_ammo_mix_totals(kills("MiG-15bis", ((API, 3), (INC, 1)), ((API, 2), (INC, 4)), ((API, 2),)))
    both = f"{API}{MIX_SEPARATOR}{INC}"
    assert totals == {
        ("MiG-15bis", both, TOTAL_AMMO): (2, 10),
        ("MiG-15bis", both, API): (2, 5),
        ("MiG-15bis", both, INC): (2, 5),
        ("MiG-15bis", API, TOTAL_AMMO): (1, 2),
        ("MiG-15bis", API, API): (1, 2),
    }


def test_a_single_type_mix_equals_the_single_type_numbers_for_those_kills() -> None:
    save(
        with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 3), (INC, 1)), ((API, 4),), ((API, 6),))),
        meta("2026-09-19_22-34-13"),
    )
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 2),))), meta("2026-09-20_22-34-13"))
    found = aircraft_ammo(GameObject.objects.get(log_name="MiG-15bis"))
    solo = next(m for m in found.mixes if m.ammos == (API,))
    assert (solo.instances, solo.total_hits, solo.average_hits) == (3, 12, 4.0)
    assert [(p.ammo, p.kills, p.average_hits) for p in solo.parts] == [(API, 3, 4.0)]
    assert solo.total_hits == next(p.hits for p in solo.parts)
    pair = next(m for m in found.mixes if m.ammos == (API, INC))
    assert [(p.ammo, p.average_hits) for p in pair.parts] == [(API, 3.0), (INC, 1.0)]
    assert [m.instances for m in found.mixes] == [3, 1]  # most instances first


def test_incremental_mixes_equal_a_rebuild_and_a_resave_replaces_them() -> None:
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 4),))), meta("2026-09-19_22-34-13"))
    save(
        with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 1), (INC, 2))), *kills("B-29", ((SHELL, 9),))),
        meta("2026-09-20_22-34-13"),
    )
    before = mix_stats()
    assert len(before) == 2 + 3 + 2
    rebuild_aggregates()
    assert mix_stats() == before
    AircraftAmmoMixStats.objects.filter(ammo=API).update(hits=999)
    AircraftAmmoMixStats.objects.filter(ammo=SHELL).delete()
    rebuild_aggregates()
    assert mix_stats() == before
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((INC, 5),))), meta("2026-09-20_22-34-13"))
    after = mix_stats()
    assert set(after) == {
        ("MiG-15bis", API, TOTAL_AMMO),
        ("MiG-15bis", API, API),
        ("MiG-15bis", INC, TOTAL_AMMO),
        ("MiG-15bis", INC, INC),
    }
    rebuild_aggregates()
    assert mix_stats() == after


def test_aircraft_detail_lists_ammo_mixes_and_stays_cheap(client: Client) -> None:
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 3), (INC, 1)), ((API, 5),))))
    pk = GameObject.objects.get(log_name="MiG-15bis").pk

    response = client.get(f"/aircraft/{pk}/")

    body = response.content.decode()
    assert "Instances" in body
    assert "Kills it hit in" not in body
    hits = response.context["hits"]
    assert [(m.instances, m.average, [(p.name, p.average) for p in m.parts]) for m in hits.mixes] == [
        ("1", "5.00", [(".50 BMG API", "5.00")]),
        ("1", "4.00", [(".50 BMG API", "3.00"), (".50 BMG INC", "1.00")]),
    ]
    assert hits.more_mixes == ()
    assert_simple_reads(client, f"/aircraft/{pk}/", max_queries=AIRCRAFT_DETAIL_READS)


def test_mixes_beyond_the_top_ten_sit_in_the_fold(client: Client) -> None:
    rows = tuple(((API, 1), (f"BULLET_X{i:02d}", 1)) for i in range(12))
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", *rows)))
    pk = GameObject.objects.get(log_name="MiG-15bis").pk

    hits = client.get(f"/aircraft/{pk}/").context["hits"]

    assert (len(hits.mixes), len(hits.more_mixes)) == (10, 2)
