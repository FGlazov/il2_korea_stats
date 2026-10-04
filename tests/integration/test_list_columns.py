"""Optional columns of the player, mission and aircraft lists (`?cols=`): whitelist, sorting incl. zero denominators,
URL round trip, no-JS form, hiding, query budgets (maintainer request 2026-10-04, TD-22). Synthetic data only."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from django.http import QueryDict
from django.test import Client
from django.urls import reverse

from il2ks.db.models import AircraftStats, Mission, Player, PlayerName, Tour
from il2ks.queries import aircraft as aircraft_reads
from il2ks.queries import missions as mission_reads
from il2ks.queries import players as player_reads
from il2ks.queries import sorties as sortie_reads
from il2ks.web import columns
from tests.factories import SERVER_UID, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
CONTEXT_READS = 2  # the site context processor
ALL_PLAYER_COLS = ",".join(c.key for c in columns.PLAYER_COLUMNS)
ALL_MISSION_COLS = ",".join(c.key for c in columns.MISSION_COLUMNS)
ALL_AIRCRAFT_COLS = ",".join(c.key for c in columns.AIRCRAFT_COLUMNS)


# --- the whitelist ------------------------------------------------------------------------------------------------
def test_chosen_keeps_registry_order_and_ignores_unknown_keys() -> None:
    params = QueryDict("cols=survival,bogus,,kd&cols=elo_jet&cols=" + "x" * 500)

    assert [c.key for c in columns.chosen(params, columns.PLAYER_COLUMNS)] == ["elo_jet", "kd", "survival"]
    assert columns.chosen(QueryDict(""), columns.PLAYER_COLUMNS) == []
    assert columns.chosen(QueryDict("cols=elo_jet"), columns.MISSION_COLUMNS) == []  # another page's key


def test_every_optional_column_is_sortable() -> None:
    assert {c.key for c in columns.PLAYER_COLUMNS} <= set(player_reads.PLAYER_SORTS)
    assert {c.key for c in columns.MISSION_COLUMNS} <= set(mission_reads.SORT_FIELDS)
    assert {c.key for c in columns.AIRCRAFT_COLUMNS} <= set(aircraft_reads.AIRCRAFT_SORTS)
    for registry in (columns.PLAYER_COLUMNS, columns.MISSION_COLUMNS, columns.AIRCRAFT_COLUMNS):
        assert len({c.key for c in registry}) == len(registry)


# --- players ------------------------------------------------------------------------------------------------------
def make_player(n: int, **fields: object) -> Player:
    values: dict[str, object] = {
        "account_uuid": f"00000000-0000-4000-8000-{n:012d}",
        "current_name": f"Pilot{n}",
        "name_lower": f"pilot{n}",
        "first_seen": NOW - timedelta(days=50 - n),
        "last_seen": NOW - timedelta(days=10 - n),
        "sorties": 10,
        **fields,
    }
    player = Player.objects.create(**values)
    PlayerName.objects.create(
        player=player, name=player.current_name, name_lower=player.name_lower, first_seen=NOW, last_seen=NOW
    )
    return player


def seed_players() -> dict[str, Player]:
    """a: K/D 4, b: K/D 1, c: no deaths (K/D undefined), d: nothing flown yet (every denominator 0)."""
    return {
        "a": make_player(
            1,
            kills_air=8,
            deaths=2,
            planes_lost=4,
            elo_jet=1700.0,
            elo_jet_games=5,
            time_on_target_s=3600.0,
            score_ground_attack=900.0,
            score_ground=900.0,
        ),
        "b": make_player(
            2,
            kills_air=3,
            deaths=3,
            planes_lost=3,
            elo_jet=1450.0,
            elo_jet_games=2,
            time_on_target_s=7200.0,
            score_ground_attack=3600.0,
            score_ground=4000.0,
        ),
        "c": make_player(3, kills_air=5, deaths=0, planes_lost=0, elo_jet=1500.0, elo_jet_games=0),
        "d": make_player(4, sorties=0),
    }


def player_order(client: Client, sort: str, cols: str = "") -> list[str]:
    response = client.get("/players/", {"sort": sort, "cols": cols})
    assert response.status_code == 200
    return [hit.player.current_name for hit in response.context["page_obj"].object_list]


def test_default_player_table_has_no_optional_columns(client: Client) -> None:
    seed_players()

    html = client.get("/players/").content.decode()

    assert 'name="cols"' in html  # the picker is there, nothing is checked
    assert "checked" not in html
    for column in columns.PLAYER_COLUMNS:
        assert f"sort={column.key}" not in html


def test_chosen_player_columns_show_up_with_sortable_headers(client: Client) -> None:
    seed_players()

    html = client.get("/players/?cols=kd,elo_jet,bogus").content.decode()

    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?kd[^"]*"', html)
    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?elo_jet[^"]*"', html)
    assert "sort=elo_prop" not in html
    assert "sort=bogus" not in html
    assert re.search(r'value="kd" checked', html)  # the control and the table agree
    assert 'value="kl" checked' not in html
    assert ">4.00<" in html  # Pilot1: 8 kills / 2 deaths
    assert ">1,700<" in html


def test_player_ratio_sort_puts_undefined_values_last_in_both_directions(client: Client) -> None:
    seed_players()

    descending = player_order(client, "-kd", "kd")
    ascending = player_order(client, "kd", "kd")

    assert descending[:2] == ["Pilot1", "Pilot2"]
    assert ascending[:2] == ["Pilot2", "Pilot1"]
    assert descending[2:] == ascending[2:] == ["Pilot3", "Pilot4"]  # no deaths / nothing flown: last, tiebreak on pk


def test_player_elo_sort_ignores_unrated_defaults(client: Client) -> None:
    seed_players()

    assert player_order(client, "-elo_jet", "elo_jet")[:2] == ["Pilot1", "Pilot2"]
    assert player_order(client, "elo_jet", "elo_jet")[:2] == ["Pilot2", "Pilot1"]  # 1500 with no games is last, not 1st
    html = client.get("/players/?cols=elo_jet").content.decode()
    assert "1,500" not in html  # shown as a dash


def test_player_ground_per_hour_and_survival_sorts(client: Client) -> None:
    seed_players()
    Player.objects.filter(current_name="Pilot3").update(deaths=0, sorties=10)

    assert player_order(client, "-ground_hour", "ground_hour")[:2] == ["Pilot2", "Pilot1"]  # 1800/h before 900/h
    assert player_order(client, "ground_hour", "ground_hour")[:2] == ["Pilot1", "Pilot2"]
    # survival: (sorties - deaths) / sorties; Pilot3 has no deaths -> 100%, Pilot4 no sorties -> undefined
    assert player_order(client, "-survival", "survival")[0] == "Pilot3"
    assert player_order(client, "survival", "survival")[-1] == "Pilot4"
    assert player_order(client, "-survival", "survival")[-1] == "Pilot4"


@pytest.mark.parametrize("column", [c.key for c in columns.PLAYER_COLUMNS])
def test_every_player_column_sorts_both_ways(client: Client, column: str) -> None:
    seed_players()

    ascending = player_order(client, column, column)
    descending = player_order(client, f"-{column}", column)

    assert sorted(ascending) == sorted(descending) == ["Pilot1", "Pilot2", "Pilot3", "Pilot4"]


def test_hidden_players_stay_hidden_with_columns(client: Client) -> None:
    seed_players()
    Player.objects.filter(current_name="Pilot1").update(is_hidden=True)

    html = client.get("/players/", {"cols": ALL_PLAYER_COLS, "q": "pilot"}).content.decode()
    assert "Pilot1" not in html
    assert "Pilot2" in html
    assert "Pilot1" not in client.get("/players/", {"cols": ALL_PLAYER_COLS}).content.decode()


def test_player_links_and_form_keep_the_column_choice(client: Client) -> None:
    seed_players()

    html = client.get("/players/?cols=kd,kl&sort=-kd&page=1").content.decode()

    assert re.search(r'<th[^>]*><a href="\?[^"]*cols=kd%2Ckl[^"]*sort=', html) or re.search(
        r'<th[^>]*><a href="\?[^"]*sort=[^"]*cols=kd%2Ckl', html
    )  # sort links keep the choice (and drop page)
    form = re.search(r"<form class=\"filter-bar\".*?</form>", html, re.S)
    assert form is not None
    assert 'method="get"' in form[0]  # no-JS: a plain GET form with the checkboxes inside it
    assert form[0].count('name="cols"') == len(columns.PLAYER_COLUMNS)
    assert 'name="sort" value="-kd"' in form[0]
    assert "Clear filters" not in html  # a column choice is not a filter
    repeated = client.get("/players/?cols=kd&cols=kl").content.decode()  # what the form submits without JS
    assert 'value="kd" checked' in repeated
    assert 'value="kl" checked' in repeated


def test_htmx_column_change_returns_the_results_region(client: Client) -> None:
    seed_players()

    response = client.get("/players/?cols=kd", headers={"HX-Request": "true"})

    html = response.content.decode()
    assert 'id="results"' in html
    assert 'id="columns-picker"' in html
    assert "change from:find input[name=cols]" in html


def test_player_list_budget_is_unchanged_with_every_column(client: Client) -> None:
    seed_players()

    assert_simple_reads(client, f"/players/?cols={ALL_PLAYER_COLS}", max_queries=4)
    assert_simple_reads(client, f"/players/?cols={ALL_PLAYER_COLS}&sort=-ground_hour&q=pilot", max_queries=4)


# --- missions -----------------------------------------------------------------------------------------------------
def make_mission(index: int, **fields: object) -> Mission:
    started = NOW - timedelta(days=100 - index)
    values: dict[str, object] = {
        "server_uid": SERVER_UID,
        "mission_uid": f"2026-01-{index:02d}_00-00-00",
        "mission_file": "Multiplayer/Dogfight\\Author\\The_Sinuiju_Bridges_1951\\The_Sinuiju_Bridges_1951.msnbin",
        "started_at": started,
        "ended_at": started + timedelta(hours=3),
        "duration_s": 10_800.0,
        "game_date": "1951.9.15",
        "game_time": "13:0:0",
        "game_type": 2,
        "completed_cleanly": True,
        "players_total": 3,
        "sorties_total": 6,
        "redfor_sorties": 3,
        "blufor_sorties": 3,
        **fields,
    }
    return Mission.objects.create(**values)


def mission_order(client: Client, sort: str, cols: str) -> list[int]:
    response = client.get("/missions/", {"sort": sort, "cols": cols, "empty": "1"})
    assert response.status_code == 200
    return [m.pk for m in response.context["missions"]]


def test_mission_columns_sort_including_zero_players(client: Client) -> None:
    lively = make_mission(1, sorties_total=12, players_total=3, friendly_kills=1)  # 4 sorties per player
    quiet = make_mission(2, sorties_total=3, players_total=3, friendly_kills=5)  # 1 per player
    empty = make_mission(3, sorties_total=0, players_total=0)  # undefined, last both ways

    assert mission_order(client, "-sorties_per_player", "sorties_per_player") == [lively.pk, quiet.pk, empty.pk]
    assert mission_order(client, "sorties_per_player", "sorties_per_player") == [quiet.pk, lively.pk, empty.pk]
    assert mission_order(client, "-friendly_kills", "friendly_kills")[0] == quiet.pk
    assert mission_order(client, "friendly_kills", "friendly_kills")[0] == empty.pk


@pytest.mark.parametrize("column", [c.key for c in columns.MISSION_COLUMNS])
def test_every_mission_column_sorts_both_ways(client: Client, column: str) -> None:
    pks = {make_mission(1).pk, make_mission(2, redfor_sorties=5).pk}

    assert set(mission_order(client, column, column)) == set(mission_order(client, f"-{column}", column)) == pks


def test_mission_tour_column_costs_no_extra_query_and_hidden_stay_hidden(client: Client) -> None:
    tour = Tour.objects.create(
        title="September 2026", started_at=NOW - timedelta(days=60), ended_at=NOW + timedelta(days=30), mode="monthly"
    )
    shown = make_mission(1, tour=tour)
    hidden = make_mission(2, tour=tour, is_hidden=True)

    html = client.get("/missions/", {"cols": ALL_MISSION_COLS}).content.decode()

    assert f"/missions/{shown.pk}/" in html
    assert f"/missions/{hidden.pk}/" not in html
    assert "September 2026" in html
    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?tour', html)
    plain = f"/missions/?cols={ALL_MISSION_COLS}"
    assert_simple_reads(client, plain, max_queries=CONTEXT_READS + 3)  # same as the default list
    assert_simple_reads(client, plain + "&sort=-tour&tour=all", max_queries=CONTEXT_READS + 3)


def test_mission_default_columns_unchanged(client: Client) -> None:
    make_mission(1)

    html = client.get("/missions/").content.decode()

    for column in columns.MISSION_COLUMNS:
        assert f"sort={column.key}" not in html


# --- aircraft -----------------------------------------------------------------------------------------------------
def seed_aircraft() -> None:
    save(
        mission(
            (
                sortie(0, 1, kills_air=2, kills_air_pvp=2),
                sortie(1, 2, aircraft_type="F-86A-5", coalition=2, is_death=True, is_plane_lost=True),
                sortie(2, 3, aircraft_type="Il-10", kills_ground=2, combat_role="attack"),
            )
        ),
    )


def aircraft_order(client: Client, sort: str, cols: str) -> list[str]:
    response = client.get(reverse("web:aircraft-list"), {"sort": sort, "cols": cols})
    assert response.status_code == 200
    return [row.stats.aircraft.log_name for row in response.context["rows"]]


def test_aircraft_columns_sort_with_undefined_ratios_last(client: Client) -> None:
    seed_aircraft()
    AircraftStats.objects.filter(aircraft__log_name="Il-10").update(
        time_on_target_s=3600.0, score_ground_attack=500.0, flight_time_s=0.0
    )
    AircraftStats.objects.filter(aircraft__log_name="MiG-15bis").update(flight_time_s=7200.0, kills_air=4)

    # air kills per flight hour: MiG 2/h; the Sabre has kills_air 0 -> 0/h; the Il-10 has no flight time -> undefined
    assert aircraft_order(client, "-kills_per_hour", "kills_per_hour")[0] == "MiG-15bis"
    assert aircraft_order(client, "-kills_per_hour", "kills_per_hour")[-1] == "Il-10"
    assert aircraft_order(client, "kills_per_hour", "kills_per_hour")[-1] == "Il-10"
    assert aircraft_order(client, "-ground_hour", "ground_hour")[0] == "Il-10"
    assert aircraft_order(client, "ground_hour", "ground_hour")[-1] != "Il-10"  # the only defined value is the first


def test_aircraft_ratio_columns_sort_from_the_counters(client: Client) -> None:
    """OQ-98: `AircraftStats` stores no ratio; K/D, K/L, survival and attack share are divided at read time, a zero
    denominator is undefined and sorts last in both directions."""
    seed_aircraft()
    AircraftStats.objects.filter(aircraft__log_name="MiG-15bis").update(
        kills_air=4, deaths=3, planes_lost=1, sorties=4, attack_sorties=0
    )
    AircraftStats.objects.filter(aircraft__log_name="F-86A-5").update(
        kills_air=3, deaths=1, planes_lost=0, sorties=2, attack_sorties=1
    )
    # Il-10: deaths 0 and planes_lost 0 -> K/D and K/L undefined
    assert aircraft_order(client, "-kd", "") == ["F-86A-5", "MiG-15bis", "Il-10"]  # 3.0, 2.0, undefined
    assert aircraft_order(client, "kd", "") == ["MiG-15bis", "F-86A-5", "Il-10"]
    assert aircraft_order(client, "-kl", "") == ["MiG-15bis", "F-86A-5", "Il-10"]  # 4.0, undefined, undefined (by name)
    assert aircraft_order(client, "-survival", "")[0] == "Il-10"  # no death: 100%
    assert aircraft_order(client, "survival", "")[0] == "MiG-15bis"  # (4 - 3) / 4
    assert aircraft_order(client, "-attack_share", "") == ["Il-10", "F-86A-5", "MiG-15bis"]  # 100%, 50%, 0%
    assert aircraft_order(client, "attack_share", "")[0] == "MiG-15bis"  # 0%


@pytest.mark.parametrize("column", [c.key for c in columns.AIRCRAFT_COLUMNS])
def test_every_aircraft_column_sorts_both_ways(client: Client, column: str) -> None:
    seed_aircraft()

    ascending = aircraft_order(client, column, column)
    descending = aircraft_order(client, f"-{column}", column)

    assert sorted(ascending) == sorted(descending) == ["F-86A-5", "Il-10", "MiG-15bis"]


def test_aircraft_page_columns_form_and_budget(client: Client) -> None:
    seed_aircraft()
    url = reverse("web:aircraft-list")

    html = client.get(url).content.decode()
    assert 'name="cols"' in html
    assert "sort=sortie_length" not in html  # not shown by default
    chosen = client.get(url + "?cols=sortie_length,assists").content.decode()
    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?sortie_length', chosen)
    assert 'value="assists" checked' in chosen
    assert_simple_reads(client, f"{url}?cols={ALL_AIRCRAFT_COLS}&sort=-score_air", max_queries=4)


# --- a player's sortie list (optional columns, sortable like the others) -----------------------------------
ALL_SORTIE_COLS = ",".join(c.key for c in columns.SORTIE_COLUMNS)


def test_sortie_list_shows_damage_taken_by_default_and_optional_columns_on_request(client: Client) -> None:
    save(mission((sortie(0, 1, kills_air=1, kills_air_pvp=1, damage_taken=0.42),)))
    player = Player.objects.get()
    url = reverse("web:player-sorties", args=[player.pk]) + "?tour=all"

    default = client.get(url).content.decode()
    assert "Damage taken" in default
    assert "42%" in default
    assert not re.search(r"sort=-?payload", default)
    assert 'name="cols"' in default

    chosen = client.get(f"{url}&cols=payload,air_points,bogus").content.decode()
    assert re.search(r"sort=-?payload", chosen)
    assert 'value="payload" checked' in chosen
    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?air_points', chosen)
    assert re.search(r'<th[^>]*><a href="[^"]*sort=-?damage_taken', default)


def test_sortie_list_budget_is_unchanged_with_every_column(client: Client) -> None:
    save(mission((sortie(0, 1),)))
    player = Player.objects.get()

    base = reverse("web:player-sorties", args=[player.pk]) + "?tour=all"
    assert_simple_reads(client, base, max_queries=8)
    assert_simple_reads(client, f"{base}&cols={ALL_SORTIE_COLS}", max_queries=8)


def test_every_sortie_column_is_sortable_and_sorts_both_ways(client: Client) -> None:
    assert {c.key for c in columns.SORTIE_COLUMNS} <= set(sortie_reads.SORT_FIELDS)
    save(mission((sortie(0, 1, damage_taken=0.2), sortie(1, 1, damage_taken=0.9, time_on_target_s=60.0))))
    url = reverse("web:player-sorties", args=[Player.objects.get().pk])
    for key in ("damage_taken", *(c.key for c in columns.SORTIE_COLUMNS)):
        for sort in (key, f"-{key}"):
            response = client.get(url, {"tour": "all", "sort": sort, "cols": key})
            assert response.status_code == 200, sort
            assert response.context["sort"] == sort
    rows = client.get(url, {"tour": "all", "sort": "-damage_taken"}).context["page_obj"].object_list
    assert [r.damage_taken for r in rows] == [0.9, 0.2]
    for sort in ("time_on_target", "-time_on_target"):  # a sortie without time on target is last either way
        page = client.get(url, {"tour": "all", "sort": sort, "cols": "time_on_target"}).context["page_obj"]
        assert next(r.time_on_target_s for r in page.object_list) == 60.0, sort
