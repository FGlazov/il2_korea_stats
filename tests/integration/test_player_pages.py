"""Player search and profile pages (FR-WEB-3, FR-WEB-4, FR-WEB-13, FR-ADM-3, TD-22). Synthetic data only."""

from datetime import timedelta

import pytest
from django.test import Client

from il2ks.db.models import GameObject, Mission, Player
from il2ks.queries import players as reads
from tests.factories import STARTED_AT, account, kill, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db


def player_pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def seed() -> None:
    """Players 1-3 fly as pilots (1 renamed between the missions), 4 is hidden, 5 flies as gunner only."""
    save(
        mission(
            (
                sortie(0, 1, name="Maverick", kills_air=2, ground_by_category={"tank": 2, "other": 3}),
                sortie(1, 2, name="Goose", coalition=2, aircraft_type="F-86A-5", kills_air=1),
                sortie(2, 3, name="Iceman", outcome="shot_down", is_death=True, is_plane_lost=True),
                sortie(3, 4, name="Ghost"),
                sortie(4, 5, name="Gunnerella", aircraft_type="Turret_IL10", role="gunner"),
            ),
            (kill(3000, 0, 1),),
        ),
        meta("2026-09-19_22-34-13", STARTED_AT),
    )
    save(
        mission(
            (
                sortie(0, 1, name="Mav", aircraft_type="Il-10", kills_ground=4, taxi_accident=True),
                sortie(1, 1, name="Mav", aircraft_type="Il-10", strafed_on_ground=True),
            )
        ),
        meta("2026-09-20_22-34-13", STARTED_AT + timedelta(days=1)),
    )
    Player.objects.filter(account_uuid=account(4)).update(is_hidden=True)


# --- search -------------------------------------------------------------------------------------------------------
def test_search_without_query_lists_recent_visible_players(client: Client) -> None:
    seed()

    response = client.get("/players/")

    assert response.status_code == 200
    names = [hit.player.current_name for hit in response.context["page_obj"].object_list]
    assert names[0] == "Mav"  # the most recent sortie
    assert "Ghost" not in names
    assert set(names) == {"Mav", "Goose", "Iceman", "Gunnerella"}


def test_search_is_partial_and_case_insensitive(client: Client) -> None:
    seed()

    response = client.get("/players/?q=ICE")

    assert [h.player.current_name for h in response.context["page_obj"].object_list] == ["Iceman"]
    assert "Iceman" in response.content.decode()


def test_search_finds_past_names_and_says_also_known_as(client: Client) -> None:
    seed()

    response = client.get("/players/?q=maver")

    hits = response.context["page_obj"].object_list
    assert [(h.player.current_name, h.matched_name) for h in hits] == [("Mav", "Maverick")]
    assert "also known as Maverick" in response.content.decode()


def test_search_match_on_current_name_has_no_alias(client: Client) -> None:
    seed()

    hits = client.get("/players/?q=mav").context["page_obj"].object_list

    assert [(h.player.current_name, h.matched_name) for h in hits] == [("Mav", "")]  # both names match, one row


def test_hidden_player_is_not_found_by_search(client: Client) -> None:
    seed()

    response = client.get("/players/?q=ghost")

    assert response.status_code == 200
    assert response.context["page_obj"].object_list == []
    assert "No player found" in response.content.decode()


def test_search_without_match_says_so_and_escapes_the_query(client: Client) -> None:
    seed()

    body = client.get("/players/?q=%3Cb%3Ex").content.decode()

    assert "<b>x" not in body
    assert "No player found" in body


def test_search_sort_is_whitelisted(client: Client) -> None:
    seed()

    ordered = client.get("/players/?sort=-kills_air")
    bogus = client.get("/players/?sort=password")

    assert ordered.context["sort"] == "-kills_air"
    assert bogus.status_code == 200
    assert bogus.context["sort"] == reads.DEFAULT_PLAYER_SORT
    kills = [h.player.kills_air for h in ordered.context["page_obj"].object_list]
    assert kills == sorted(kills, reverse=True)


def test_htmx_live_search_returns_the_results_region(client: Client) -> None:
    seed()

    response = client.get("/players/?q=gun", headers={"HX-Request": "true"})

    body = response.content.decode()
    assert 'id="results"' in body
    assert "data-live" in body  # the live input is part of the region's filter bar
    assert "Gunnerella" in body


def test_search_paginates(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    seed()
    monkeypatch.setattr(reads, "PAGE_SIZE", 2)

    first = client.get("/players/").context["page_obj"]
    second = client.get("/players/?page=2").context["page_obj"]

    assert first.paginator.count == 4
    assert len(first.object_list) == len(second.object_list) == 2
    assert client.get("/players/?page=99").status_code == 200  # out of range: the last page


def test_search_page_budget(client: Client) -> None:
    seed()

    assert_simple_reads(client, "/players/", max_queries=4)  # context processor 2, count, rows
    assert_simple_reads(client, "/players/?q=mav", max_queries=4)


# --- profile ------------------------------------------------------------------------------------------------------
def test_profile_shows_totals_ratios_and_ground_breakdown(client: Client) -> None:
    seed()

    response = client.get(f"/players/{player_pk(1)}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert ">2026-09-19</time>" in body
    assert "Also known as" in body  # past names
    assert "Maverick" in body
    assert "Ground kills by category" in body
    assert "Tanks" in body
    assert "Other objects" in body
    assert "Hall of shame" in body
    player = response.context["player"]
    assert (player.kills_air, player.kills_ground, player.taxi_accidents, player.strafed_on_ground) == (2, 9, 1, 1)
    assert [(g.key, g.count) for g in response.context["ground"] if g.count] == [("tank", 2), ("other", 7)]


def test_profile_ratios_have_a_dash_for_zero_denominators(client: Client) -> None:
    seed()

    # Player 2 flew one sortie, killed once and never died or lost the plane.
    body = client.get(f"/players/{player_pk(2)}/").content.decode()

    assert "K/D —" in body
    assert "K/L —" in body
    assert "100%" in body  # survival rate: no death in the only sortie


def test_profile_of_a_player_with_zero_kills_has_no_ground_accordion(client: Client) -> None:
    seed()

    body = client.get(f"/players/{player_pk(3)}/").content.decode()

    assert "Ground kills by category" not in body
    assert "0%" in body  # survival rate of a player who died in the only sortie


def test_profile_per_aircraft_rows_link_to_the_filtered_sortie_list(client: Client) -> None:
    seed()
    pk = player_pk(1)
    il10 = GameObject.objects.get(log_name="Il-10")

    response = client.get(f"/players/{pk}/")

    rows = response.context["aircraft"]
    assert {r.aircraft.log_name for r in rows} == {"MiG-15bis", "Il-10"}
    assert f'href="/players/{pk}/sorties/?aircraft={il10.pk}"' in response.content.decode()


def test_profile_aircraft_table_sorts_and_whitelists(client: Client) -> None:
    seed()
    pk = player_pk(1)

    by_air_kills = client.get(f"/players/{pk}/?sort=-kills_air").context["aircraft"]
    default = client.get(f"/players/{pk}/?sort=drop_table").context
    by_name = client.get(f"/players/{pk}/?sort=aircraft").context["aircraft"]

    assert [r.aircraft.log_name for r in by_air_kills] == ["MiG-15bis", "Il-10"]
    assert default["sort"] == reads.DEFAULT_AIRCRAFT_SORT
    assert [r.aircraft.display_name for r in by_name] == sorted(r.aircraft.display_name for r in by_name)


def test_profile_recent_sorties_are_newest_first_and_capped(client: Client) -> None:
    save(
        mission(tuple(sortie(i, 1, spawn_tick=1000 * (i + 1)) for i in range(12))),
        meta("2026-09-21_22-34-13", STARTED_AT + timedelta(days=2)),
    )

    response = client.get(f"/players/{player_pk(1)}/")

    recent = response.context["recent"]
    assert len(recent) == reads.RECENT_SORTIES == 10
    assert [s.spawn_tick for s in recent] == [12000 - 1000 * i for i in range(10)]
    assert f"/players/{player_pk(1)}/sorties/" in response.content.decode()  # link to the full list
    assert "/missions/" in response.content.decode()


def test_hidden_mission_sorties_are_not_on_the_profile(client: Client) -> None:
    """FR-ADM-3: a hidden mission's sorties are left out of the recent sorties (no leaked mission link)."""
    seed()
    hidden = Mission.objects.get(mission_uid="2026-09-20_22-34-13")
    Mission.objects.filter(pk=hidden.pk).update(is_hidden=True)

    response = client.get(f"/players/{player_pk(1)}/")

    assert all(s.mission_id != hidden.pk for s in response.context["recent"])
    assert f"/missions/{hidden.pk}/" not in response.content.decode()
    assert response.context["recent"]


def test_gunner_only_profile_says_so_instead_of_empty_tables(client: Client) -> None:
    seed()

    response = client.get(f"/players/{player_pk(5)}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert response.context["gunner_only"] is True
    assert "flies as a gunner only" in body
    assert "By aircraft" not in body
    assert "Hall of shame" not in body
    assert "Turret" in body or "turret" in body  # the gunner sortie still shows under recent sorties


def test_profile_without_any_sortie_is_not_called_gunner_only(client: Client) -> None:
    seed()
    Player.objects.filter(account_uuid=account(2)).update(sorties=0)

    response = client.get(f"/players/{player_pk(2)}/")

    assert response.context["gunner_only"] is False
    assert "No pilot sorties are counted" in response.content.decode()


def test_hidden_and_missing_players_are_404(client: Client) -> None:
    seed()

    assert client.get(f"/players/{player_pk(4)}/").status_code == 404
    assert client.get("/players/999999/").status_code == 404


def test_profile_page_budget(client: Client) -> None:
    seed()

    # context processor 2, player, names, tours (selector), stat thresholds, aircraft rows, recent sorties, streak,
    # top victims, top nemeses, tour history (charts)
    assert_simple_reads(client, f"/players/{player_pk(1)}/", max_queries=12)
    assert_simple_reads(client, f"/players/{player_pk(1)}/?sort=-kills_air", max_queries=12)
    # a player with zero counted sorties adds one read to tell gunner-only from empty
    assert_simple_reads(client, f"/players/{player_pk(5)}/", max_queries=9)


def test_profile_name_is_escaped(client: Client) -> None:
    save(mission((sortie(0, 1, name="<script>alert(1)</script>"),)))

    body = client.get(f"/players/{player_pk(1)}/").content.decode()

    assert "<script>alert(1)" not in body


# --- reads --------------------------------------------------------------------------------------------------------
def test_resolve_sort_whitelist() -> None:
    allowed = {"kills_air": "kills_air"}

    assert reads.resolve_sort("kills_air", allowed, "-x") == "kills_air"
    assert reads.resolve_sort("-kills_air", allowed, "-x") == "-kills_air"
    assert reads.resolve_sort("--kills_air", allowed, "-x") == "-x"
    assert reads.resolve_sort("", allowed, "-x") == "-x"
    assert reads.resolve_sort("pk", allowed, "-x") == "-x"
