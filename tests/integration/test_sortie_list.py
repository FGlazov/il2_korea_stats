"""The site-wide sortie list `/sorties/` (maintainer request 2026-10-05, FR-WEB-29, FR-WEB-24, FR-ADM-3, TD-22, TD-26).

Two monthly tours: September (Maverick MiG, Goose F-86, Bob a gunner, Dan hidden), October (Maverick Il-10, Cora MiG).
Synthetic data only."""

import re
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse

from il2ks.db.models import Mission, Player, PlayerSortie
from il2ks.queries import sorties as reads
from il2ks.queries.paging import ROW_PAGE_SIZE
from il2ks.web import columns
from tests.factories import STARTED_AT, account, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

LIST_BUDGET = 2 + 5  # site context + tours (selector), aircraft choices, count, page; + the tour of an empty page
ALL_COLS = ",".join(c.key for c in columns.SITE_SORTIE_COLUMNS)


def seed() -> None:
    save(
        mission(
            (
                sortie(0, 1, name="Maverick", kills_air=2, kills_air_pvp=2, combat_role="air_superiority"),
                sortie(1, 2, name="Goose", coalition=2, aircraft_type="F-86A-5", kills_ground=3, combat_role="attack"),
                sortie(2, 3, name="Bob", role="gunner", flight_time_s=60.0),
                sortie(3, 4, name="Dan", kills_ground=9),
            )
        ),
        meta("2026-09-19_22-34-13", STARTED_AT),
    )
    Player.objects.filter(account_uuid=account(4)).update(is_hidden=True)
    save(
        mission(
            (
                sortie(
                    0, 1, name="Maverick", aircraft_type="IL-10", kills_ground=4, outcome="shot_down", is_death=True
                ),
                sortie(1, 5, name="Cora", kills_air=1),
            )
        ),
        meta("2026-10-02_10-00-00", STARTED_AT + timedelta(days=13)),
    )


def names(client: Client, query: str = "") -> list[str]:
    response = client.get(reverse("web:sortie-list") + query)
    assert response.status_code == 200
    return [row.name_at_time for row in response.context["page_obj"]]


def test_the_url_is_stable() -> None:
    assert reverse("web:sortie-list") == "/sorties/"


def test_default_is_the_current_tour_pilot_sorties_newest_first(client: Client) -> None:
    seed()

    response = client.get("/sorties/")

    assert response.status_code == 200
    assert response.context["tour"].title == "October 2026"
    assert [row.name_at_time for row in response.context["page_obj"]] == [
        "Cora",
        "Maverick",
    ]  # same spawn tick: newest pk first
    body = response.content.decode()
    assert "Sorties in October 2026" in body
    assert 'name="tour"' in body
    assert '<option value="" selected>Current tour</option>' in body


def test_tour_all_lists_every_counted_pilot_sortie_but_no_gunner_and_no_hidden_player(client: Client) -> None:
    seed()

    listed = names(client, "?tour=all")

    assert sorted(listed) == ["Cora", "Goose", "Maverick", "Maverick"]
    assert "Dan" not in listed  # hidden player: his sortie page is a 404, so the row would be a dead link
    assert "Bob" not in listed  # gunners are not counted by the statistics (see ?seat=)


def test_seat_filter_default_pilots_gunner_and_any(client: Client) -> None:
    seed()

    assert names(client, "?tour=all&seat=gunner") == ["Bob"]
    assert sorted(names(client, "?tour=all&seat=any")) == ["Bob", "Cora", "Goose", "Maverick", "Maverick"]
    assert sorted(names(client, "?tour=all&seat=bogus")) == sorted(names(client, "?tour=all"))
    body = client.get("/sorties/?tour=all&seat=any").content.decode()
    assert 'name="seat"' in body
    assert "Gunner" in body  # the gunner row carries a badge next to the pilot


def test_hidden_missions_are_left_out(client: Client) -> None:
    seed()
    Mission.objects.filter(mission_uid="2026-10-02_10-00-00").update(is_hidden=True)

    assert names(client, "?tour=all") == ["Goose", "Maverick"]


def test_filters(client: Client) -> None:
    seed()
    il10 = PlayerSortie.objects.get(name_at_time="Maverick", aircraft__log_name="IL-10").aircraft_id

    assert names(client, "?tour=all&q=mave") == ["Maverick", "Maverick"]
    assert names(client, "?tour=all&q=ose") == ["Goose"]
    assert names(client, f"?tour=all&aircraft={il10}") == ["Maverick"]
    assert names(client, "?tour=all&outcome=shot_down") == ["Maverick"]
    assert names(client, "?tour=all&combat_role=attack") == ["Goose"]
    assert names(client, "?tour=all&q=nobody") == []
    assert (
        len(names(client, "?tour=all&outcome=bogus&aircraft=999999&combat_role=x")) == 4
    )  # unknown values are ignored


def test_sorts(client: Client) -> None:
    seed()

    assert names(client, "?tour=all&sort=-kills_ground")[0] == "Maverick"
    assert names(client, "?tour=all&sort=kills_air")[-1] == "Maverick"
    assert names(client, "?tour=all&sort=-kills_air")[0] == "Maverick"
    assert names(client, "?tour=all&sort=aircraft")[0] in {"Goose", "Maverick"}
    assert names(client, "?tour=all&sort=bogus") == names(client, "?tour=all&sort=-date")


def test_every_sort_key_and_every_extra_column_answers_200_both_ways(client: Client) -> None:
    seed()
    for key in reads.SORT_FIELDS:
        for sort in (key, f"-{key}"):
            assert len(names(client, f"?tour=all&seat=any&sort={sort}&cols={ALL_COLS}")) == 5, sort


def test_every_extra_column_is_sortable_or_declared_plain() -> None:
    keys = [c.key for c in columns.SITE_SORTIE_COLUMNS]
    assert len(keys) == len(set(keys))
    assert "mission" not in keys  # a default column of this list
    assert {c.key for c in columns.SITE_SORTIE_COLUMNS if c.sortable} <= set(reads.SORT_FIELDS)


def test_default_headers_and_extra_columns(client: Client) -> None:
    seed()

    html = client.get("/sorties/?tour=all").content.decode()
    for key in ("date", "aircraft", "mission", "outcome", "kills_air", "kills_ground", "flight_time"):
        assert re.search(rf'<th[^>]*><a href="[^"]*sort=-?{key}\b', html), key
    assert 'name="cols"' in html
    assert "Air score" not in html.split("<thead>")[1].split("</thead>")[0]  # the extras are not shown until chosen
    chosen = client.get("/sorties/?tour=all&cols=air_points,combat_role").content.decode()
    head = chosen.split("<thead>")[1].split("</thead>")[0]
    assert "Air score" in head
    assert "Role" in head


def test_rows_link_to_the_sortie_and_the_mission_and_the_pilot(client: Client) -> None:
    seed()
    row = PlayerSortie.objects.get(name_at_time="Cora")

    html = client.get("/sorties/").content.decode()

    assert f'class="stretched-link" href="/sorties/{row.pk}/"' in html  # whole-row link (FR-WEB-24)
    assert f'href="/missions/{row.mission_id}/"' in html
    assert f'href="/players/{row.player_id}/?tour={row.mission.tour_id}"' in html


def test_a_running_mission_carries_a_live_badge(client: Client) -> None:
    seed()
    assert "Live" not in client.get("/sorties/").content.decode().split("<tbody>")[1]
    Mission.objects.filter(mission_uid="2026-10-02_10-00-00").update(is_live=True)

    assert "Live" in client.get("/sorties/").content.decode().split("<tbody>")[1]


def test_pagination_is_twenty_a_page_and_keeps_the_filters(client: Client) -> None:
    save(
        mission(tuple(sortie(i, i + 1, name=f"P{i:02d}", kills_air=i) for i in range(ROW_PAGE_SIZE + 5))),
        meta("2026-10-02_10-00-00", STARTED_AT),
    )

    first = client.get("/sorties/?sort=-kills_air")
    second = client.get("/sorties/?sort=-kills_air&page=2")

    assert len(first.context["page_obj"]) == ROW_PAGE_SIZE
    assert [r.name_at_time for r in second.context["page_obj"]] == [f"P{i:02d}" for i in range(4, -1, -1)]
    assert (
        "sort=-kills_air&amp;page=2" in first.content.decode() or "page=2&amp;sort=-kills_air" in first.content.decode()
    )


def test_empty_site_and_empty_tour(client: Client) -> None:
    assert client.get("/sorties/").status_code == 200  # no tours, no sorties: no error
    seed()
    from il2ks.db.models import Tour

    Tour.objects.create(title="November 2026", started_at=STARTED_AT + timedelta(days=60))
    response = client.get("/sorties/")
    assert response.status_code == 200
    assert response.context["quiet_tour"] is True
    assert response.context["page_obj"].paginator.count == 0
    assert "No sorties match" not in response.content.decode()  # the tour's flavor line explains the emptiness
    assert "No sorties match" in client.get("/sorties/?tour=all&q=nobody").content.decode()


def test_budgets(client: Client) -> None:
    seed()
    assert_simple_reads(client, "/sorties/", LIST_BUDGET)
    assert_simple_reads(client, "/sorties/?tour=all&q=a&seat=any&cols=" + ALL_COLS, LIST_BUDGET)
    assert_simple_reads(client, "/sorties/?tour=all&sort=-accuracy&aircraft=1&outcome=landed", LIST_BUDGET)
