"""The mission page's sortie tables: sortable columns, optional columns (`?sort=`, `?cols=`), hidden players, budgets
(maintainer request 2026-10-04, FR-WEB-2, FR-ADM-3, TD-22). Synthetic data only."""

import re

import pytest
from django.http import QueryDict
from django.test import Client
from django.urls import reverse

from il2ks.db.models import Mission, Player, PlayerSortie
from il2ks.queries import missions as reads
from il2ks.web import columns
from tests.factories import account, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

ALL_COLS = ",".join(c.key for c in columns.MISSION_SORTIE_COLUMNS)
DEFAULT_HEADERS = ("time", "pilot", "aircraft", "role", "outcome", "fate", "kills_air", "kills_ground", "assists")


def seed() -> Mission:
    """Four sorties in spawn order: Cora (dead, 5 air kills), Abe (captured-less survivor, attack role), Bob (gunner:
    no combat role), Dan (hidden player, the most ground kills)."""
    result = save(
        mission(
            (
                sortie(0, 1, name="Cora", kills_air=5, kills_air_pvp=5, is_death=True, damage_taken=0.9),
                sortie(1, 2, name="abe", kills_ground=3, combat_role="attack", time_on_target_s=90.0, assists=2),
                sortie(2, 3, name="Bob", role="gunner", flight_time_s=60.0, damage_taken=0.1),
                sortie(3, 4, name="Dan", kills_ground=9, combat_role="attack", time_on_target_s=30.0),
            )
        )
    )
    Player.objects.filter(account_uuid=account(4)).update(is_hidden=True)
    PlayerSortie.objects.filter(name_at_time__in=["Bob", "Dan"]).update(payload_name="")
    PlayerSortie.objects.filter(name_at_time="Cora").update(payload_name="Rockets")
    PlayerSortie.objects.filter(name_at_time="abe").update(payload_name="Bombs")
    return result


def order(client: Client, pk: int, sort: str = "", cols: str = "") -> list[str]:
    response = client.get(reverse("web:mission-detail", args=[pk]), {"sort": sort, "cols": cols})
    assert response.status_code == 200
    return [row.name_at_time for group in response.context["groups"] for row in group.rows]


def test_default_order_is_spawn_order_and_the_default_columns_are_unchanged(client: Client) -> None:
    pk = seed().pk

    assert order(client, pk) == ["Cora", "abe", "Bob", "Dan"]
    assert order(client, pk, "bogus") == order(client, pk, "-bogus") == ["Cora", "abe", "Bob", "Dan"]
    html = client.get(reverse("web:mission-detail", args=[pk])).content.decode()
    for key in (*DEFAULT_HEADERS, "flight_time"):
        assert re.search(rf'<th[^>]*><a href="[^"]*sort=-?{key}\b', html), key
    assert 'name="cols"' in html
    assert "checked" not in html
    for column in columns.MISSION_SORTIE_COLUMNS:
        assert not re.search(rf"sort=-?{column.key}\b", html), column.key


def test_every_default_and_optional_column_is_sortable() -> None:
    assert {c.key for c in columns.MISSION_SORTIE_COLUMNS} <= set(reads.SORTIE_SORT_FIELDS)
    assert len({c.key for c in columns.MISSION_SORTIE_COLUMNS}) == len(columns.MISSION_SORTIE_COLUMNS)
    assert set(DEFAULT_HEADERS) | {"flight_time"} <= set(reads.SORTIE_SORT_FIELDS)


def test_every_sort_key_answers_200_both_ways(client: Client) -> None:
    pk = seed().pk
    for key in reads.SORTIE_SORT_FIELDS:
        for sort in (key, f"-{key}"):
            assert len(order(client, pk, sort, ALL_COLS)) == 4, sort


def test_sorting_by_numbers_and_times(client: Client) -> None:
    pk = seed().pk

    assert order(client, pk, "-kills_air") == ["Cora", "abe", "Bob", "Dan"]  # ties keep spawn order
    assert order(client, pk, "kills_air")[-1] == "Cora"
    assert order(client, pk, "-kills_ground")[:2] == ["Dan", "abe"]
    assert order(client, pk, "-assists")[0] == "abe"
    assert order(client, pk, "flight_time")[0] == "Bob"
    assert order(client, pk, "-damage_taken", "damage_taken")[:2] == ["Cora", "Bob"]
    assert order(client, pk, "-time") == ["Dan", "Bob", "abe", "Cora"]


def test_sorting_by_pilot_ignores_case_and_puts_hidden_players_last_both_ways(client: Client) -> None:
    pk = seed().pk

    assert order(client, pk, "pilot") == ["abe", "Bob", "Cora", "Dan"]
    assert order(client, pk, "-pilot") == ["Cora", "Bob", "abe", "Dan"]  # Dan is hidden: no position to leak


def test_sorting_by_fate_role_and_payload_puts_missing_values_last(client: Client) -> None:
    pk = seed().pk

    assert order(client, pk, "-fate")[0] == "Cora"  # dead first
    assert order(client, pk, "fate")[-1] == "Cora"
    assert order(client, pk, "role")[-1] == "Bob"  # a gunner has no combat role
    assert order(client, pk, "-role")[-1] == "Bob"
    assert order(client, pk, "payload", "payload")[:2] == ["abe", "Cora"]
    assert order(client, pk, "-payload", "payload")[:2] == ["Cora", "abe"]
    assert set(order(client, pk, "payload", "payload")[2:]) == {"Bob", "Dan"}  # no loadout recorded: last


def test_time_on_target_without_one_sorts_last_both_ways(client: Client) -> None:
    pk = seed().pk

    for sort in ("time_on_target", "-time_on_target"):
        assert set(order(client, pk, sort, "time_on_target")[2:]) == {"Cora", "Bob"}, sort
    assert order(client, pk, "time_on_target")[:2] == ["Dan", "abe"]
    assert order(client, pk, "-time_on_target")[:2] == ["abe", "Dan"]


def test_sort_applies_to_every_side_table(client: Client) -> None:
    result = save(
        mission(
            (
                sortie(0, 1, name="R1", kills_air=1),
                sortie(1, 2, name="R2", kills_air=4),
                sortie(2, 3, name="B1", coalition=2, kills_air=2),
                sortie(3, 4, name="B2", coalition=2, kills_air=7),
            )
        )
    )
    response = client.get(reverse("web:mission-detail", args=[result.pk]), {"sort": "-kills_air"})

    assert [[r.name_at_time for r in g.rows] for g in response.context["groups"]] == [["R2", "R1"], ["B2", "B1"]]
    html = response.content.decode()
    assert html.count('aria-sort="descending"') == 2  # one header per side table
    assert len(re.findall(r'<th[^>]*><a href="[^"]*sort=kills_air', html)) == 2  # the next click toggles to ascending


def test_cols_whitelist_and_url_round_trip(client: Client) -> None:
    pk = seed().pk
    url = reverse("web:mission-detail", args=[pk])

    html = client.get(url, {"cols": "air_points,bogus,payload", "sort": "-payload"}).content.decode()

    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?air_points', html)
    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?payload', html)
    assert "sort=ground_points" not in html
    assert "sort=bogus" not in html
    assert 'value="air_points" checked' in html
    assert 'value="payload" checked' in html
    assert 'value="landings" checked' not in html
    # the header links keep the chosen columns and flip the direction of the active one
    link = re.search(r'<a href="([^"]*sort=-kills_air[^"]*)"', html)
    assert link is not None
    params = QueryDict(link.group(1).removeprefix("?").replace("&amp;", "&"))
    assert params["cols"] == "air_points,bogus,payload"  # the choice travels with the sort link
    assert params["sort"] == "-kills_air"
    assert "Rockets" in html
    assert "Bombs" in html
    for column in columns.MISSION_SORTIE_COLUMNS:
        assert f'value="{column.key}"' in html


def test_cols_repeated_parameters_work_like_the_no_js_form(client: Client) -> None:
    pk = seed().pk

    html = client.get(reverse("web:mission-detail", args=[pk]) + "?cols=takeoffs&cols=landings").content.decode()

    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?takeoffs', html)
    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?landings', html)
    assert "<form" in html
    assert 'method="get"' in html  # the Apply button submits it without JS


def test_optional_cells_render_the_values(client: Client) -> None:
    pk = seed().pk

    html = client.get(reverse("web:mission-detail", args=[pk]), {"cols": ALL_COLS}).content.decode()

    assert ">90%<" in html  # Cora's damage taken
    assert "Rockets" in html


def test_hidden_player_stays_anonymous_and_unlinked_in_every_sort(client: Client) -> None:
    pk = seed().pk
    hidden = Player.objects.get(account_uuid=account(4))

    for sort in ("pilot", "-pilot", "-kills_ground", "fate"):
        html = client.get(reverse("web:mission-detail", args=[pk]), {"sort": sort, "cols": ALL_COLS}).content.decode()
        assert ">Dan<" not in html, sort
        assert f"/players/{hidden.pk}/" not in html, sort
        assert "Hidden player" in html, sort
    rows = PlayerSortie.objects.filter(player=hidden)
    assert f"/sorties/{rows.get().pk}/" not in html  # no row link for a hidden player


def test_whole_row_links_survive_sorting(client: Client) -> None:
    pk = seed().pk

    html = client.get(
        reverse("web:mission-detail", args=[pk]), {"sort": "-flight_time", "cols": "landings"}
    ).content.decode()

    assert len(re.findall(r'class="row-link stretched-link" href="/sorties/\d+/"', html)) == 3  # all but the hidden one


def test_results_region_is_there_for_htmx_and_kills_table_is_outside(client: Client) -> None:
    pk = seed().pk

    html = client.get(reverse("web:mission-detail", args=[pk]), {"sort": "pilot"}).content.decode()

    region = html[html.index('id="results"') : html.index('id="kills"')]
    assert "sortie-table" in region
    assert 'hx-select="#results"' in html
    assert "sort=" not in html[html.index('id="kills"') :].split("</table>")[0]  # the kills table has no sort links


def test_budget_is_unchanged_with_every_column_and_sort(client: Client) -> None:
    pk = seed().pk
    url = reverse("web:mission-detail", args=[pk])

    assert_simple_reads(client, url, max_queries=6)  # the same budget as before (tests/perf/pages.py)
    assert_simple_reads(client, f"{url}?cols={ALL_COLS}&sort=-pilot", max_queries=6)
    assert_simple_reads(client, f"{url}?cols={ALL_COLS}&sort=-fate", max_queries=6)


def test_heavy_columns_stay_deferred(client: Client) -> None:
    pk = seed().pk
    mission_row = Mission.objects.get(pk=pk)

    rows = reads.mission_sorties(mission_row, "-pilot")

    assert all("timeline" in row.get_deferred_fields() for row in rows)
    assert all("ammo" in row.get_deferred_fields() for row in rows)
