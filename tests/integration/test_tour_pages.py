"""Tour selection on the pages (TD-26, FR-WEB-4, FR-WEB-5, FR-WEB-10): profile, sortie list, mission list.

Two monthly tours: September (player 1 flies a MiG, player 2 an F-86) and October (player 1 flies an Il-10)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import translation

from il2ks.db.models import Mission, Player, Tour
from il2ks.queries.tours import tour_options, tour_title
from tests.factories import STARTED_AT, account, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db


def seed() -> None:
    save(
        mission(
            (
                sortie(0, 1, name="Maverick", kills_air=2, ground_by_category={"tank": 2}),
                sortie(1, 2, name="Goose", coalition=2, aircraft_type="F-86A-5", kills_air=1),
            )
        ),
        meta("2026-09-19_22-34-13", STARTED_AT),
    )
    save(
        mission(
            (sortie(0, 1, name="Maverick", aircraft_type="Il-10", ground_by_category={"vehicle": 4}, kills_ground=4),)
        ),
        meta("2026-10-02_10-00-00", STARTED_AT + timedelta(days=13)),
    )


def pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def tour(title: str) -> Tour:
    return Tour.objects.get(title=title)


# --- title localisation --------------------------------------------------------------------------------------------
def test_monthly_titles_are_localised_at_display_time() -> None:
    assert tour_title("October 2026") == "October 2026"
    with translation.override("de"):
        assert tour_title("October 2026") == "Oktober 2026"
    with translation.override("ru"):
        assert "2026" in tour_title("October 2026")
        assert tour_title("October 2026") != "October 2026"


def test_renamed_and_lookalike_titles_are_left_alone() -> None:
    assert tour_title("Tour 7") == "Tour 7"
    with translation.override("de"):
        assert tour_title("Operation Autumn Wind") == "Operation Autumn Wind"
        assert tour_title("October 20266") == "October 20266"  # only exact automatic titles are localised


def test_selector_lists_localised_titles_and_keeps_renames(client: Client) -> None:
    seed()
    Tour.objects.filter(title="September 2026").update(title="The Bridge Campaign")

    page = client.get(f"/players/{pk(1)}/", headers={"Accept-Language": "de"}).content.decode()

    assert ">Oktober 2026</option>" in page
    assert ">The Bridge Campaign</option>" in page
    with translation.override("en"):
        assert [title for _pk, title in tour_options(list(Tour.objects.order_by("-started_at")))] == [
            "October 2026",
            "The Bridge Campaign",
        ]


# --- profile -------------------------------------------------------------------------------------------------------
def test_profile_without_tour_is_all_time_and_offers_the_selector(client: Client) -> None:
    seed()

    response = client.get(f"/players/{pk(1)}/")

    assert response.status_code == 200
    assert response.context["tour"] is None
    assert response.context["stats"] == response.context["player"]
    assert (response.context["stats"].sorties, response.context["stats"].kills_ground) == (2, 6)
    assert len(response.context["aircraft"]) == 2
    body = response.content.decode()
    assert 'name="tour"' in body
    assert f'<option value="{tour("September 2026").pk}">September 2026</option>' in body


def test_profile_with_a_tour_shows_that_tour_only(client: Client) -> None:
    seed()
    september = tour("September 2026")

    response = client.get(f"/players/{pk(1)}/?tour={september.pk}")

    assert response.status_code == 200
    stats = response.context["stats"]
    assert (stats.sorties, stats.kills_air, stats.kills_ground) == (1, 2, 2)
    assert [row.aircraft.log_name for row in response.context["aircraft"]] == ["MiG-15bis"]
    assert [s.mission.mission_uid for s in response.context["recent"]] == ["2026-09-19_22-34-13"]
    assert [(g.key, g.count) for g in response.context["ground"] if g.count] == [("tank", 2)]
    body = response.content.decode()
    assert f'<option value="{september.pk}" selected>September 2026</option>' in body
    assert f"/players/{pk(1)}/sorties/?tour={september.pk}" in body  # "All sorties" keeps the tour
    assert f"&amp;tour={september.pk}" in body  # so do the per-aircraft links


def test_profile_tour_where_the_player_did_not_fly_says_so(client: Client) -> None:
    seed()

    response = client.get(f"/players/{pk(2)}/?tour={tour('October 2026').pk}")

    assert response.status_code == 200
    assert response.context["stats"] is None
    assert response.context["recent"] == []
    assert response.context["aircraft"] == []
    assert "No pilot sorties are counted for this player in the selected tour." in response.content.decode()


@pytest.mark.parametrize("raw", ["", "abc", "-1", "999999", "1.5", "1;2", "%00"])
def test_profile_unknown_tour_falls_back_to_all_time(client: Client, raw: str) -> None:
    seed()

    response = client.get(f"/players/{pk(1)}/", {"tour": raw})

    assert response.status_code == 200
    assert response.context["tour"] is None
    assert response.context["stats"].sorties == 2


def test_profile_sort_and_tour_combine(client: Client) -> None:
    seed()

    response = client.get(f"/players/{pk(1)}/?tour={tour('October 2026').pk}&sort=-kills_ground")

    assert response.context["sort"] == "-kills_ground"
    assert response.context["stats"].kills_ground == 4
    assert 'name="sort" value="-kills_ground"' in response.content.decode()  # the selector keeps the sort


def test_profile_tour_budget(client: Client) -> None:
    seed()

    # context processor 2, player, names, tours, thresholds, aircraft, recent sorties, streak, killboard top 2 = 11;
    # a tour adds the PlayerTour row
    assert_simple_reads(client, f"/players/{pk(1)}/", max_queries=11)
    assert_simple_reads(client, f"/players/{pk(1)}/?tour={tour('September 2026').pk}", max_queries=12)
    assert_simple_reads(client, f"/players/{pk(2)}/?tour={tour('October 2026').pk}", max_queries=12)


# --- sortie list ---------------------------------------------------------------------------------------------------
def test_sortie_list_filters_by_tour(client: Client) -> None:
    seed()
    base = f"/players/{pk(1)}/sorties/"

    everything = client.get(base)
    september = client.get(f"{base}?tour={tour('September 2026').pk}")
    october = client.get(f"{base}?tour={tour('October 2026').pk}")
    unknown = client.get(f"{base}?tour=424242")

    assert [s.mission.mission_uid for s in everything.context["page_obj"]] == [
        "2026-10-02_10-00-00",
        "2026-09-19_22-34-13",
    ]
    assert [s.mission.mission_uid for s in september.context["page_obj"]] == ["2026-09-19_22-34-13"]
    assert [s.aircraft.log_name for s in october.context["page_obj"]] == ["Il-10"]
    assert unknown.status_code == 200
    assert len(unknown.context["page_obj"]) == 2
    assert f'<option value="{tour("October 2026").pk}" selected>October 2026</option>' in october.content.decode()


def test_sortie_list_tour_budget(client: Client) -> None:
    seed()
    base = f"/players/{pk(1)}/sorties/"

    # context processor 2, player, aircraft options, tours, COUNT, page
    assert_simple_reads(client, base, max_queries=7)
    assert_simple_reads(client, f"{base}?tour={tour('September 2026').pk}", max_queries=7)


# --- mission list --------------------------------------------------------------------------------------------------
def test_mission_list_filters_by_tour(client: Client) -> None:
    seed()

    everything = client.get("/missions/")
    september = client.get(f"/missions/?tour={tour('September 2026').pk}")
    unknown = client.get("/missions/?tour=abc")

    assert [m.mission_uid for m in everything.context["page_obj"]] == ["2026-10-02_10-00-00", "2026-09-19_22-34-13"]
    assert [m.mission_uid for m in september.context["page_obj"]] == ["2026-09-19_22-34-13"]
    assert len(unknown.context["page_obj"]) == 2
    assert Mission.objects.count() == 2
    assert f'<option value="{tour("September 2026").pk}" selected>September 2026</option>' in september.content.decode()


def test_mission_list_tour_budget(client: Client) -> None:
    seed()

    # context processor 2, tours, COUNT, page
    assert_simple_reads(client, "/missions/", max_queries=5)
    assert_simple_reads(client, f"/missions/?tour={tour('October 2026').pk}", max_queries=5)
