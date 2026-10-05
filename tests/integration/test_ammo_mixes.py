"""Ammo mixes of the "Hits to destroy" section (FR-WEB-18): level 1, level 2, rebuild equality, reads, page."""

from dataclasses import replace

import pytest
from django.http import HttpResponse
from django.test import Client

from il2ks.core.replay.result import MissionResult, SingleAttackerKill
from il2ks.db.models import MIX_SEPARATOR, TOTAL_AMMO, AircraftAmmoMixStats, GameObject
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.persist import aircraft_ammo_mix_totals
from il2ks.queries.ammo import aircraft_ammo
from il2ks.queries.paging import MIN_EVENTS_LISTED
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


def mix_page(client: Client, pk: int, query: str = "") -> HttpResponse:
    response = client.get(f"/aircraft/{pk}/{query}")
    assert response.status_code == 200
    return response


@pytest.mark.usefixtures("list_every_row")
def test_aircraft_detail_lists_ammo_mixes_and_stays_cheap(client: Client) -> None:
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 3), (INC, 1)), ((API, 5),))))
    pk = GameObject.objects.get(log_name="MiG-15bis").pk

    response = mix_page(client, pk)

    body = response.content.decode()
    assert "Kills it hit in" not in body
    assert "Show 2 more mixes" not in body  # the fold is gone: the table pages
    assert [(m.instances, m.averages, [p.name for p in m.parts]) for m in response.context["ammo_mixes"]] == [
        ("1", "5.0", [".50 BMG API"]),  # a single ammunition is a mix of one: no separate per-ammo rows
        ("1", "3.0 + 1.0", [".50 BMG API", ".50 BMG INC"]),
    ]
    assert response.context["hits"].kills == "2"
    assert "3.0 + 1.0" in body
    assert_simple_reads(client, f"/aircraft/{pk}/", max_queries=AIRCRAFT_DETAIL_READS)


def test_ammo_mix_average_hits_read_5_4_plus_2_7_in_the_stored_order(client: Client) -> None:
    """Ten kills where the API hit 54 times and the INC 27 times: the row reads 5.4 + 2.7, in the order of the mix."""
    api_hits = (5, 5, 5, 5, 5, 5, 5, 5, 5, 9)  # 54
    inc_hits = (3, 3, 3, 3, 3, 3, 3, 2, 2, 2)  # 27
    rows = tuple(((API, a), (INC, i)) for a, i in zip(api_hits, inc_hits, strict=True))
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", *rows)))
    mig = GameObject.objects.get(log_name="MiG-15bis")

    response = mix_page(client, mig.pk)

    rows = [(m.instances, m.averages) for m in response.context["ammo_mixes"]]
    assert rows == [("10", "5.4 + 2.7")]
    assert "5.4 + 2.7" in response.content.decode()


def test_ammo_mix_parts_follow_the_order_the_mix_is_stored_in(client: Client) -> None:
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 1), (INC, 1)))))
    mig = GameObject.objects.get(log_name="MiG-15bis")
    stored = f"{SHELL}{MIX_SEPARATOR}{INC}{MIX_SEPARATOR}{API}"  # not alphabetical by name: stored order wins
    for ammo, hits in ((TOTAL_AMMO, 100), (API, 20), (INC, 30), (SHELL, 50)):
        AircraftAmmoMixStats.objects.create(aircraft=mig, mix=stored, ammo=ammo, kills=10, hits=hits)

    found = aircraft_ammo(mig, min_mix_kills=10).mixes

    assert [m.ammos for m in found] == [(SHELL, INC, API)]
    assert [(p.ammo, p.average_hits) for p in found[0].parts] == [(SHELL, 5.0), (INC, 3.0), (API, 2.0)]
    assert [m.averages for m in mix_page(client, mig.pk, "?tour=all").context["ammo_mixes"]] == ["5.0 + 3.0 + 2.0"]


def test_ammo_mixes_need_ten_kills_to_be_listed(client: Client) -> None:
    assert MIN_EVENTS_LISTED == 10
    ten = tuple(((API, 2), (INC, 1)) for _ in range(10))
    nine = tuple(((API, 1), (SHELL, 1)) for _ in range(9))
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", *ten, *nine)))
    pk = GameObject.objects.get(log_name="MiG-15bis").pk

    response = mix_page(client, pk)

    assert [(m.instances, m.averages) for m in response.context["ammo_mixes"]] == [("10", "2.0 + 1.0")]
    assert response.context["hits"].kills == "19"  # the summary row still counts every kill


def test_a_type_without_a_mix_of_ten_kills_says_so(client: Client) -> None:
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", ((API, 1),))))
    pk = GameObject.objects.get(log_name="MiG-15bis").pk

    response = mix_page(client, pk)

    assert list(response.context["ammo_mixes"]) == []
    assert "No ammunition mix with at least 10 kills yet." in response.content.decode()


@pytest.mark.usefixtures("list_every_row")
def test_ammo_mixes_page_by_twenty_with_their_own_parameter(client: Client) -> None:
    rows = tuple(((API, 1), (f"BULLET_X{i:02d}", 1)) for i in range(25))
    save(with_kills(mission((sortie(0, 1),)), *kills("MiG-15bis", *rows)))
    pk = GameObject.objects.get(log_name="MiG-15bis").pk

    first = mix_page(client, pk)
    second = mix_page(client, pk, "?page_mixes=2&page_mods=7")

    assert (len(first.context["ammo_mixes"]), len(second.context["ammo_mixes"])) == (20, 5)
    assert second.context["ammo_mixes"].number == 2
    body = second.content.decode()
    assert "Showing 21\N{EN DASH}25 of 25" in body
    assert 'aria-label="Pagination: Hits to destroy"' in body
    assert 'hx-get="?page_mixes=2' in first.content.decode()
