"""Player search and profile pages (FR-WEB-3, FR-WEB-4, FR-WEB-13, FR-ADM-3, TD-22). Synthetic data only."""

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client

from il2ks.db.models import GameObject, Mission, Player, PlayerRole, PlayerTour, Tour
from il2ks.queries import players as reads
from tests.factories import STARTED_AT, account, kill, meta, mission, save, sortie
from tests.simple_reads import PROFILE_READS_ALL_TIME, PROFILE_READS_TOUR, assert_simple_reads

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
                sortie(0, 1, name="Mav", aircraft_type="IL-10", kills_ground=4, taxi_accident=True),
                sortie(1, 1, name="Mav", aircraft_type="IL-10", strafed_on_ground=True),
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
    il10 = GameObject.objects.get(log_name="IL-10")

    response = client.get(f"/players/{pk}/?tour=all")

    rows = response.context["aircraft"]
    assert {r.aircraft.log_name for r in rows} == {"MiG-15bis", "IL-10"}
    assert f'href="/players/{pk}/sorties/?aircraft={il10.pk}&amp;tour=all"' in response.content.decode()


def test_profile_aircraft_table_sorts_and_whitelists(client: Client) -> None:
    seed()
    pk = player_pk(1)

    by_air_kills = client.get(f"/players/{pk}/?sort=-kills_air").context["aircraft"]
    default = client.get(f"/players/{pk}/?sort=drop_table").context
    by_name = client.get(f"/players/{pk}/?sort=aircraft").context["aircraft"]

    assert [r.aircraft.log_name for r in by_air_kills] == ["MiG-15bis", "IL-10"]
    assert default["sort"] == reads.DEFAULT_AIRCRAFT_SORT
    assert [r.aircraft.display_name for r in by_name] == sorted(r.aircraft.display_name for r in by_name)


def test_profile_recent_sorties_are_newest_first_and_capped(client: Client) -> None:
    save(
        mission(tuple(sortie(i, 1, spawn_tick=1000 * (i + 1)) for i in range(12))),
        meta("2026-09-21_22-34-13", STARTED_AT + timedelta(days=2)),
    )

    response = client.get(f"/players/{player_pk(1)}/")

    recent = response.context["recent"]
    assert len(recent) == reads.RECENT_SORTIES == 5
    assert [s.spawn_tick for s in recent] == [12000 - 1000 * i for i in range(5)]
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

    response = client.get(f"/players/{player_pk(2)}/?tour=all")

    assert response.context["gunner_only"] is False
    assert "No pilot sorties are counted" in response.content.decode()


def test_hidden_and_missing_players_are_404(client: Client) -> None:
    seed()

    assert client.get(f"/players/{player_pk(4)}/").status_code == 404
    assert client.get("/players/999999/").status_code == 404


def test_profile_page_budget(client: Client) -> None:
    seed()

    # see tests/simple_reads.py for what the profile budgets count
    assert_simple_reads(client, f"/players/{player_pk(1)}/", max_queries=PROFILE_READS_TOUR)
    assert_simple_reads(client, f"/players/{player_pk(1)}/?tour=all", max_queries=PROFILE_READS_ALL_TIME)
    all_time_sorted = f"/players/{player_pk(1)}/?tour=all&sort=-kills_air"
    assert_simple_reads(client, all_time_sorted, max_queries=PROFILE_READS_ALL_TIME)
    assert_simple_reads(client, f"/players/{player_pk(5)}/", max_queries=PROFILE_READS_TOUR)


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


def test_profile_sections_come_in_order_header_shame_recent_air_ground_overall(client: Client) -> None:
    """FR-WEB-4: general header, hall of shame and the latest sorties first, then air-to-air, air-to-ground, overall."""
    seed()

    body = client.get(f"/players/{player_pk(1)}/?tour=all").content.decode()

    markers = [
        "profile-head",
        "profile-nav",
        'class="shame"',
        'id="recent"',
        'id="air"',
        'id="ground"',
        'id="overall"',
    ]
    positions = [body.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert body.index("Ground kills by category") > body.index('id="ground"')
    assert body.index("Shot down most") > body.index('id="air"') if "Shot down most" in body else True
    assert body.index("By aircraft") > body.index('id="overall"')


def test_profile_view_all_sorties_button_keeps_the_tour_scope(client: Client) -> None:
    seed()
    pk = player_pk(1)

    all_time = client.get(f"/players/{pk}/?tour=all").content.decode()
    current = client.get(f"/players/{pk}/")
    tour = current.context["tour"]

    assert "View all sorties" in all_time
    assert f'href="/players/{pk}/sorties/?tour=all"' in all_time
    assert "View all sorties" in current.content.decode()
    assert f'href="/players/{pk}/sorties/?tour={tour.pk}"' in current.content.decode()


def test_profile_without_ground_activity_collapses_the_ground_part(client: Client) -> None:
    seed()

    response = client.get(f"/players/{player_pk(3)}/?tour=all")

    body = response.content.decode()
    assert response.context["ground_active"] is False
    assert 'id="ground"' in body
    assert "No air-to-ground activity yet" in body
    assert "not enough time on target yet" in body  # the star tile stays, muted (below the board minimum)
    assert "Tanks destroyed per hour" not in body
    assert "Ground kills by category" not in body


def test_star_tiles_follow_the_role_and_show_the_below_minimum_state(client: Client) -> None:
    """Maintainer 2026-10-05: Elo (jet, prop) and attack proficiency are the first tiles; All shows all three, air
    superiority the Elo tiles, attack the attack proficiency one; below the board minimum a dash and a muted line."""
    seed()
    pk = player_pk(3)
    page = client.get(f"/players/{pk}/?tour=all").content.decode()
    keys = ["elo-jet", "elo-prop", "attack"]
    assert [k for k in keys if f'data-star="{k}"' in page] == keys
    assert page.index('data-star="elo-jet"') < page.index('data-star="attack"') < page.index("Sorties</div>")
    assert "not enough encounters yet" in page
    assert "not enough time on target yet" in page
    assert "stat-tile--muted" in page


def test_star_tiles_in_the_role_views(client: Client) -> None:
    seed_roles()
    pk = player_pk(1)
    air = client.get(f"/players/{pk}/?tour=all&role=air_superiority").content.decode()
    assert 'data-star="elo-jet"' in air
    assert 'data-star="elo-prop"' in air
    assert 'data-star="attack"' not in air
    attack = client.get(f"/players/{pk}/?tour=all&role=attack").content.decode()
    assert 'data-star="attack"' in attack
    assert 'data-star="elo-jet"' not in attack


def test_profile_without_air_activity_collapses_the_air_part(client: Client) -> None:
    seed()
    Player.objects.filter(pk=player_pk(3)).update(kills_air=0, kills_air_pvp=0, kills_air_ai=0, assists=0)

    response = client.get(f"/players/{player_pk(3)}/?tour=all")

    assert response.context["air_active"] is False
    assert "No air-to-air activity yet" in response.content.decode()


# --- role toggle (maintainer 2026-10-05): ?role=air_superiority|attack on the profile -------------------------------
def seed_roles() -> None:
    """Player 1 flies air superiority (August: 3 air kills, a death; September: 1 air kill, 1 empty sortie) and attack
    (September: 2 sorties, 5 ground kills); player 2 only air superiority; player 3 only attack. Two monthly tours."""
    air, attack = "air_superiority", "attack"
    save(
        mission(
            (
                sortie(0, 1, name="Maverick", kills_air=3, is_death=True, is_plane_lost=True, combat_role=air),
                sortie(1, 2, name="Goose", combat_role=air),
                sortie(2, 3, name="Viper", aircraft_type="IL-10", kills_ground=1, combat_role=attack),
            )
        ),
        meta("2026-08-10_10-00-00", datetime(2026, 8, 10, 8, tzinfo=UTC)),
    )
    save(
        mission(
            (
                sortie(0, 1, name="Maverick", kills_air=1, combat_role=air),
                sortie(1, 1, name="Maverick", aircraft_type="IL-10", kills_ground=2, combat_role=attack),
                sortie(2, 1, name="Maverick", aircraft_type="IL-10", kills_ground=3, combat_role=attack),
                sortie(3, 3, name="Viper", aircraft_type="IL-10", kills_ground=4, combat_role=attack),
                sortie(4, 1, name="Maverick", combat_role=air),
            )
        ),
        meta("2026-09-10_10-00-00", datetime(2026, 9, 10, 8, tzinfo=UTC)),
    )


def test_role_toggle_scopes_the_tiles_tables_and_recent_sorties(client: Client) -> None:
    seed_roles()
    url = f"/players/{player_pk(1)}/?tour=all"

    everything = client.get(url).context["stats"]
    air = client.get(url + "&role=air_superiority")
    attack = client.get(url + "&role=attack")

    assert (everything.sorties, everything.kills_air, everything.kills_ground) == (5, 4, 5)
    assert (air.context["stats"].sorties, air.context["stats"].kills_air, air.context["stats"].deaths) == (3, 4, 1)
    assert (attack.context["stats"].sorties, attack.context["stats"].kills_ground) == (2, 5)
    assert attack.context["stats"].attack_sorties == 2
    assert [row.aircraft.display_name for row in attack.context["aircraft"]] == ["IL-10"]
    assert [row.aircraft.display_name for row in air.context["aircraft"]] == ["MiG-15bis"]
    assert {s.combat_role for s in attack.context["recent"]} == {"attack"}
    assert {s.combat_role for s in air.context["recent"]} == {"air_superiority"}
    body = attack.content.decode()
    assert 'aria-current="true"' in body
    assert "tour=all" in body  # the toggle keeps the tour


def test_the_role_rows_of_a_fully_tagged_pilot_add_up_to_his_tour_and_all_time_rows() -> None:
    """The per-role copies of the player rows (PlayerRole) are the same counters grouped by combat role: a pilot whose
    sorties all have a role has roles that sum to PlayerTour (per tour) and Player (all time)."""
    seed_roles()
    player = Player.objects.get(pk=player_pk(1))

    for tour_row in PlayerTour.objects.filter(player=player):
        parts = PlayerRole.objects.filter(player=player, tour=tour_row.tour)
        assert sum(p.sorties for p in parts) == tour_row.sorties
        assert sum(p.kills_air + p.kills_ground for p in parts) == tour_row.kills_air + tour_row.kills_ground
        assert sum(p.flight_time_s for p in parts) == pytest.approx(tour_row.flight_time_s)
    all_time = PlayerRole.objects.filter(player=player, tour__isnull=True)
    assert {r.role: r.sorties for r in all_time} == {"air_superiority": 3, "attack": 2}
    assert sum(r.deaths for r in all_time) == player.deaths == 1


def test_the_role_rows_survive_a_rebuild() -> None:
    """Incremental == rebuild (FR-ING-15): a rebuild writes the same PlayerRole rows."""
    from il2ks.ingest.aggregates import rebuild_aggregates
    from tests.db_canon import canonical_dump, diff_dumps

    seed_roles()
    expected = canonical_dump()
    assert PlayerRole.objects.filter(tour__isnull=True).count() == 4  # player 1 (two roles), 2 and 3 (one each)
    rebuild_aggregates()
    assert diff_dumps(expected, canonical_dump()) == []


def test_role_toggle_in_a_tour_counts_only_that_tours_role_sorties(client: Client) -> None:
    seed_roles()
    august = Tour.objects.order_by("started_at").first()
    assert august is not None

    air = client.get(f"/players/{player_pk(1)}/?tour={august.pk}&role=air_superiority").context["stats"]
    attack = client.get(f"/players/{player_pk(1)}/?tour={august.pk}&role=attack")
    september = client.get(f"/players/{player_pk(1)}/?role=attack").context["stats"]  # default = the current tour

    assert (air.sorties, air.kills_air) == (1, 3)
    assert attack.context["stats"].sorties == 0  # flew no attack in August
    assert "No sorties in this role" in attack.content.decode()
    assert (september.sorties, september.kills_ground) == (2, 5)


def test_a_pilot_who_never_flew_attack_gets_a_notice_and_a_working_toggle(client: Client) -> None:
    seed_roles()

    response = client.get(f"/players/{player_pk(2)}/?tour=all&role=attack")

    body = response.content.decode()
    assert response.status_code == 200
    assert "No sorties in this role" in body
    assert 'id="air"' not in body
    assert "Which sorties to count" in body  # the toggle stays so the visitor can go back
    assert client.get(f"/players/{player_pk(2)}/?tour=all&role=air_superiority").context["stats"].sorties == 1


def test_a_player_without_sorties_has_no_role_toggle_and_a_bad_role_means_every_role(client: Client) -> None:
    seed_roles()
    save(mission((sortie(0, 9, name="Gunnerella", aircraft_type="Turret_IL10", role="gunner"),)))

    assert "Which sorties to count" not in client.get(f"/players/{player_pk(9)}/?role=attack").content.decode()
    assert client.get(f"/players/{player_pk(1)}/?tour=all&role=bogus").context["role"] == "all"


def test_role_view_hides_what_has_no_per_role_data(client: Client) -> None:
    seed_roles()
    url = f"/players/{player_pk(1)}/?tour=all"

    everything = client.get(url).content.decode()
    attack = client.get(url + "&role=attack").content.decode()
    air = client.get(url + "&role=air_superiority").content.decode()

    assert "Activity by tour" in everything
    assert "Elo (prop)" in everything
    assert "Elo (prop)" in air
    assert "Activity by tour" not in attack
    assert "Activity by tour" not in air
    assert "Elo (prop)" not in attack
    assert "The killboards below count every role." in attack
    assert "The killboards below count every role." not in everything


def test_air_and_ground_parts_swap_places_for_the_attack_role_and_attack_pilots(client: Client) -> None:
    seed_roles()

    def order(url: str) -> tuple[bool, bool]:
        """(the air part is before the ground part, the nav link to air is before the one to ground)."""
        body = client.get(url).content.decode()
        return body.index('id="air"') < body.index('id="ground"'), body.index('href="#air"') < body.index(
            'href="#ground"'
        )

    mixed = f"/players/{player_pk(1)}/?tour=all"
    assert order(mixed) == (True, True)
    assert order(mixed + "&role=air_superiority") == (True, True)
    assert order(mixed + "&role=attack") == (False, False)
    assert order(f"/players/{player_pk(3)}/?tour=all") == (False, False)  # a pilot who flies only attack


def test_role_views_stay_within_the_profile_query_budgets(client: Client) -> None:
    seed_roles()
    pk = player_pk(1)

    # the role's per-aircraft rows are ONE read that replaces the all-roles aircraft table read
    for role in ("air_superiority", "attack"):
        assert_simple_reads(client, f"/players/{pk}/?tour=all&role={role}", max_queries=PROFILE_READS_ALL_TIME)
        assert_simple_reads(client, f"/players/{pk}/?role={role}", max_queries=PROFILE_READS_TOUR)
        assert_simple_reads(client, f"/players/{player_pk(2)}/?role={role}", max_queries=PROFILE_READS_TOUR)


def test_other_totals_are_a_grid_of_label_value_cells(client: Client) -> None:
    seed_roles()

    body = client.get(f"/players/{player_pk(1)}/?tour=all").content.decode()

    assert 'class="totals-grid"' in body
    assert body.count("<dt>Takeoffs</dt>") == 1
