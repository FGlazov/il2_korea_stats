"""Leaderboard pages (FR-WEB-7, FR-WEB-19, FR-WEB-20, FR-ADM-3, TD-22): ranking, minimum activity, hiding, tours,
aircraft filter, Elo pools, query budget. Synthetic data only."""

from datetime import timedelta

import pytest
from django.test import Client, override_settings

from il2ks.config import LeaderboardConfig
from il2ks.db.models import GameObject, Player, PlayerPool, PlayerTourPool, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.queries.leaderboards import BoardRow
from tests.factories import STARTED_AT, account, kill, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

LOW = LeaderboardConfig(min_sorties=1, min_elo_games=1, min_attack_sorties=1, min_time_on_target_minutes=1.0)
AIR = "air_superiority"


def names(client: Client, url: str) -> list[str]:
    response = client.get(url)
    assert response.status_code == 200
    rows: list[BoardRow] = response.context["page_obj"].object_list
    return [row.player.current_name for row in rows]


def seed() -> None:
    """Ace and Rookie fight in the air, Pounder and Fencer attack the ground; player 3 (Ghost) is hidden and best."""
    save(
        mission(
            (
                sortie(0, 1, name="Ace", combat_role=AIR, kills_air_pvp=3, kills_air_ai=0),
                sortie(1, 2, name="Rookie", coalition=2, combat_role=AIR, kills_air_pvp=1, kills_air_ai=0),
                sortie(2, 3, name="Ghost", combat_role=AIR, kills_air_pvp=9, kills_air_ai=0),
                sortie(
                    3,
                    4,
                    name="Pounder",
                    aircraft_type="IL-10",
                    combat_role="attack",
                    ground_by_category={"tank": 3},
                    time_on_target_s=600.0,
                ),
                sortie(
                    4,
                    5,
                    name="Fencer",
                    aircraft_type="IL-10",
                    combat_role="attack",
                    ground_by_category={"other": 10},
                    time_on_target_s=7200.0,
                ),
            ),
            (kill(100, 0, 1), kill(200, 0, 1), kill(300, 2, 1), kill(400, 2, 1), kill(500, 1, 0)),
        ),
        meta("2026-09-19_22-34-13", STARTED_AT),
    )
    Player.objects.filter(account_uuid=account(3)).update(is_hidden=True)


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_air_board_ranks_by_score_and_leaves_out_hidden_players(client: Client) -> None:
    seed()

    assert names(client, "/leaderboards/")[:2] == ["Ace", "Rookie"]  # the plain URL is the air board
    listed = names(client, "/leaderboards/air/")
    assert "Ghost" not in listed
    assert listed[:2] == ["Ace", "Rookie"]
    rows = client.get("/leaderboards/air/").context["page_obj"].object_list
    assert [r.rank for r in rows] == list(range(1, len(rows) + 1))


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_hidden_players_are_absent_from_every_board(client: Client) -> None:
    seed()
    for board in ("air", "ground", "ground-hour", "elo-prop", "elo-jet", "interception", "tank-busting"):
        assert "Ghost" not in names(client, f"/leaderboards/{board}/"), board
        assert "Ghost" not in client.get(f"/leaderboards/{board}/").content.decode(), board


def test_minimum_sorties_keep_one_lucky_sortie_off_the_board(client: Client) -> None:
    seed()  # everyone flew exactly one sortie

    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_sorties=2)):
        assert names(client, "/leaderboards/air/") == []
        assert "Nobody qualifies" in client.get("/leaderboards/air/").content.decode()
    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_sorties=1)):
        assert names(client, "/leaderboards/air/")[0] == "Ace"


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_ground_board_and_ground_per_hour(client: Client) -> None:
    seed()

    # Pounder: 3 tanks = 18 points in 10 minutes = 108 / h; Fencer: 10 fences = 2 points in 2 h = 1 / h
    assert names(client, "/leaderboards/ground/")[:2] == ["Pounder", "Fencer"]
    response = client.get("/leaderboards/ground-hour/")
    rows: list[BoardRow] = response.context["page_obj"].object_list
    assert [(r.player.current_name, r.per_hour) for r in rows] == [("Pounder", 108.0), ("Fencer", 1.0)]
    assert [r.rank for r in rows] == [1, 2]


def test_ground_per_hour_needs_enough_time_on_target_and_attack_sorties(client: Client) -> None:
    seed()

    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_attack_sorties=1, min_time_on_target_minutes=30)):
        assert names(client, "/leaderboards/ground-hour/") == ["Fencer"]
    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_attack_sorties=2, min_time_on_target_minutes=0)):
        assert names(client, "/leaderboards/ground-hour/") == []


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_ground_per_hour_ignores_ground_score_of_fighter_sorties(client: Client) -> None:
    """Regression-style check of FR-WEB-20: only attack sorties feed the rate."""
    save(
        mission(
            (
                sortie(0, 1, name="Mixed", combat_role=AIR, ground_by_category={"tank": 5}),
                sortie(
                    1,
                    1,
                    name="Mixed",
                    combat_role="attack",
                    ground_by_category={"tank": 1},
                    time_on_target_s=3600.0,
                ),
            )
        )
    )

    rows = client.get("/leaderboards/ground-hour/").context["page_obj"].object_list
    assert [r.per_hour for r in rows] == [6.0]
    assert client.get("/leaderboards/ground/").context["page_obj"].object_list[0].stats.score_ground == 36.0


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_elo_boards_have_prop_and_jet_pools(client: Client) -> None:
    save(
        mission(
            (
                sortie(0, 1, name="JetA", aircraft_type="MiG-15bis", combat_role=AIR),
                sortie(1, 2, name="JetB", aircraft_type="F-86A-5", coalition=2, combat_role=AIR),
                sortie(2, 3, name="PropA", aircraft_type="F-51D", combat_role=AIR),
                sortie(3, 4, name="PropB", aircraft_type="F-51D", coalition=2, combat_role=AIR),
            ),
            (kill(100, 0, 1), kill(200, 2, 3)),
        )
    )

    jets = client.get("/leaderboards/elo-jet/").context["page_obj"].object_list
    props = client.get("/leaderboards/elo-prop/").context["page_obj"].object_list
    assert [r.player.current_name for r in jets] == ["JetA", "JetB"]
    assert [r.player.current_name for r in props] == ["PropA", "PropB"]
    assert jets[0].rating > 1500 > jets[1].rating
    assert client.get("/leaderboards/elo-jet/?tour=1&aircraft=1").status_code == 200  # no aircraft filter: ignored


def test_elo_boards_need_the_minimum_rated_games(client: Client) -> None:
    save(
        mission(
            (sortie(0, 1, combat_role=AIR), sortie(1, 2, coalition=2, combat_role=AIR)),
            (kill(100, 0, 1),),
        )
    )

    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_sorties=1, min_elo_games=2)):
        assert names(client, "/leaderboards/elo-jet/") == []
    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_sorties=1, min_elo_games=1)):
        assert len(names(client, "/leaderboards/elo-jet/")) == 2


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_tour_selector_shows_the_tours_own_scores(client: Client) -> None:
    save(
        mission(
            (
                sortie(0, 1, name="Ace", kills_air_pvp=1, kills_air_ai=0),
                sortie(1, 2, name="Other", kills_air_pvp=2, kills_air_ai=0),
            )
        ),
        meta("2026-09-19_22-34-13", STARTED_AT),
    )
    save(
        mission((sortie(0, 1, name="Ace", kills_air_pvp=5, kills_air_ai=0),)),
        meta("2026-10-19_22-34-13", STARTED_AT + timedelta(days=30)),
    )
    september = Tour.objects.get(started_at__lt=STARTED_AT + timedelta(days=10), ended_at__gt=STARTED_AT)

    all_time = client.get("/leaderboards/air/?tour=all").context["page_obj"].object_list
    in_september = client.get(f"/leaderboards/air/?tour={september.pk}").context
    assert [(r.player.current_name, r.stats.score_air) for r in all_time] == [("Ace", 60.0), ("Other", 20.0)]
    assert [(r.player.current_name, r.stats.score_air) for r in in_september["page_obj"].object_list] == [
        ("Other", 20.0),
        ("Ace", 10.0),
    ]
    assert in_september["tour"] == september
    assert client.get("/leaderboards/air/?tour=junk").status_code == 200  # an unknown tour means the current tour


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_aircraft_filter_lists_one_row_per_player_of_that_type(client: Client) -> None:
    save(
        mission(
            (
                sortie(0, 1, name="Both", aircraft_type="MiG-15bis", kills_air_pvp=1, kills_air_ai=0),
                sortie(1, 1, name="Both", aircraft_type="F-51D", kills_air_pvp=4, kills_air_ai=0),
                sortie(2, 2, name="Mig", aircraft_type="MiG-15bis", kills_air_pvp=2, kills_air_ai=0),
            )
        )
    )
    from il2ks.db.models import GameObject

    mig = GameObject.objects.get(log_name="MiG-15bis")

    rows = client.get(f"/leaderboards/air/?aircraft={mig.pk}").context["page_obj"].object_list
    assert [(r.player.current_name, r.stats.score_air) for r in rows] == [("Mig", 20.0), ("Both", 10.0)]
    assert len(client.get("/leaderboards/air/").context["page_obj"].object_list) == 2  # all aircraft: per player
    assert client.get("/leaderboards/air/?aircraft=zzz").status_code == 200


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_sorting_is_whitelisted_and_columns_are_sortable(client: Client) -> None:
    seed()

    by_name = client.get("/leaderboards/ground/?sort=name")
    assert by_name.context["sort"] == "name"
    assert client.get("/leaderboards/ground/?sort=password").context["sort"] == "-score"
    assert client.get("/leaderboards/air/?sort=-bogus").context["sort"] == "-score"


def test_unknown_board_is_a_404(client: Client) -> None:
    assert client.get("/leaderboards/nope/").status_code == 404


def test_the_navigation_links_to_the_leaderboards(client: Client) -> None:
    html = client.get("/").content.decode()
    assert 'href="/leaderboards/"' in html


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_boards_are_simple_reads_within_budget(client: Client) -> None:
    seed()
    # context processors 2, tours, aircraft options, count, rows (Elo boards have no tour or aircraft reads)
    assert_simple_reads(client, "/leaderboards/", max_queries=6)
    assert_simple_reads(client, "/leaderboards/ground-hour/?sort=-score", max_queries=6)
    assert_simple_reads(client, "/leaderboards/ground/?sort=name", max_queries=6)
    assert_simple_reads(client, "/leaderboards/elo-prop/", max_queries=4)


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_profile_shows_scores_and_elo(client: Client) -> None:
    seed()
    pk = Player.objects.get(account_uuid=account(1)).pk

    html = client.get(f"/players/{pk}/").content.decode()

    assert "Air score" in html
    assert "30.0" in html  # Ace: 3 kills * 10


@pytest.mark.parametrize("raw", ["9" * 5000, "9" * 19, "-1", "x", ""])
def test_a_malformed_or_huge_aircraft_filter_means_all_aircraft(client: Client, raw: str) -> None:
    """A digit string beyond Python's int() limit must not raise (500)."""
    seed()

    response = client.get("/leaderboards/air/", {"aircraft": raw})

    assert response.status_code == 200


# --- prop / jet and fighter / attack split, home page ---------------------------------------------------------------


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_score_boards_split_into_prop_and_jet_pilots(client: Client) -> None:
    """Ace and Rookie fly the MiG-15bis (jet), Pounder and Fencer the Il-10 (prop)."""
    seed()

    assert names(client, "/leaderboards/air/?pool=jet")[:2] == ["Ace", "Rookie"]
    assert set(names(client, "/leaderboards/air/?pool=jet")) == {"Ace", "Rookie"}
    assert set(names(client, "/leaderboards/air/?pool=prop")) == {"Pounder", "Fencer"}
    assert names(client, "/leaderboards/ground/?pool=prop")[:2] == ["Pounder", "Fencer"]
    assert names(client, "/leaderboards/ground-hour/?pool=prop") == ["Pounder", "Fencer"]
    assert names(client, "/leaderboards/ground-hour/?pool=jet") == []
    assert "Ghost" not in names(client, "/leaderboards/air/?pool=jet")  # hidden players stay hidden
    everyone = set(names(client, "/leaderboards/air/"))
    assert everyone >= {"Ace", "Pounder"}
    assert set(names(client, "/leaderboards/air/?pool=nonsense")) == everyone  # unknown value: both pools


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_the_pool_split_works_per_tour_and_the_aircraft_filter_wins(client: Client) -> None:
    seed()
    tour = Tour.objects.get()
    pounder_type = GameObject.objects.get(log_name="IL-10")

    assert set(names(client, f"/leaderboards/air/?tour={tour.pk}&pool=prop")) == {"Pounder", "Fencer"}
    assert set(names(client, f"/leaderboards/air/?tour={tour.pk}&pool=jet")) == {"Ace", "Rookie"}
    both = names(client, f"/leaderboards/air/?pool=jet&aircraft={pounder_type.pk}")
    assert set(both) == {"Pounder", "Fencer"}  # an aircraft type implies its own pool


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_elo_boards_are_not_split_again_and_the_pool_filter_is_offered_elsewhere(client: Client) -> None:
    seed()

    assert client.get("/leaderboards/elo-jet/").context["pool_options"] == []
    assert [value for value, _label in client.get("/leaderboards/air/").context["pool_options"]] == ["prop", "jet"]
    assert names(client, "/leaderboards/elo-jet/?pool=prop") == names(client, "/leaderboards/elo-jet/")


def test_the_board_switcher_lists_the_air_boards_then_the_ground_boards(client: Client) -> None:
    """Maintainer decision 2026-10-04: this order, an air group and a ground group, no kills board."""
    groups = {title: [t[0] for t in tabs] for title, tabs in client.get("/leaderboards/").context["tab_groups"]}

    assert groups == {
        "Air": ["elo-jet", "elo-prop", "air", "interception"],
        "Ground": ["ground-hour", "tank-busting", "ground"],
        "General": ["play-time", "ironman-air", "ironman-ground"],  # OQ-79; the ironman boards: 2026-10-05
    }
    body = client.get("/leaderboards/").content.decode()
    assert "Propeller and jet" in body


def test_the_retired_kills_board_redirects_permanently_to_the_index(client: Client) -> None:
    response = client.get("/leaderboards/kills/?sort=name")

    assert response.status_code == 301
    assert response["Location"] == "/leaderboards/"


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_switching_boards_keeps_the_filters_the_target_board_has(client: Client) -> None:
    seed()
    october = Tour.objects.get()

    response = client.get(f"/leaderboards/air/?tour={october.pk}&pool=jet&sort=name")
    tabs = {key: (url, current) for group in response.context["tab_groups"] for key, _t, url, current, _i in group[1]}

    assert tabs["air"][1] is True
    assert tabs["ground"][0] == f"/leaderboards/ground/?tour={october.pk}&pool=jet"
    assert tabs["elo-jet"][0] == f"/leaderboards/elo-jet/?tour={october.pk}"  # the tour (Elo is per tour), no pool
    body = response.content.decode()
    assert 'aria-current="page"' in body
    assert (
        client.get("/leaderboards/air/?tour=all").context["tab_groups"][1][1][0][2]
        == "/leaderboards/ground-hour/?tour=all"
    )


def _without_ids(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{k: v for k, v in row.items() if k != "id"} for row in rows]


def test_pool_rows_are_rebuilt_like_the_other_level_2_rows() -> None:
    seed()
    before = {m.__name__: list(m.objects.order_by("pk").values()) for m in (PlayerPool, PlayerTourPool)}
    assert before["PlayerPool"]
    assert before["PlayerTourPool"]

    PlayerPool.objects.update(sorties=99)
    PlayerTourPool.objects.all().delete()
    rebuild_aggregates()

    after = {m.__name__: list(m.objects.order_by("pk").values()) for m in (PlayerPool, PlayerTourPool)}
    assert _without_ids(after["PlayerPool"]) == _without_ids(before["PlayerPool"])
    assert _without_ids(after["PlayerTourPool"]) == _without_ids(before["PlayerTourPool"])


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_home_highlights_elo_and_ground_proficiency(client: Client) -> None:
    """OQ-64: Elo of both pools and ground proficiency on the home page, hidden players left out."""
    seed()

    response = client.get("/")

    boards = {b.key: [r.player.current_name for r in b.rows] for b in response.context["boards"]}
    assert list(boards) == ["elo-jet", "elo-prop", "interception", "ground-hour", "tank-busting", "play-time"]
    assert boards["elo-jet"][0] == "Ace"
    assert boards["ground-hour"] == ["Pounder", "Fencer"]
    assert boards["tank-busting"] == ["Pounder", "Fencer"]
    assert "Ghost" not in response.content.decode()
    assert "/leaderboards/elo-jet/" in response.content.decode()


def test_home_boards_show_a_message_when_nobody_qualifies(client: Client) -> None:
    seed()  # the default minimums are higher than one sortie

    html = client.get("/").content.decode()

    assert "Nobody qualifies for this board yet." in html


def test_pool_and_group_pages_stay_simple_reads(client: Client) -> None:
    seed()

    assert_simple_reads(client, "/leaderboards/air/?pool=jet", max_queries=6)
    assert_simple_reads(client, "/leaderboards/ground-hour/?pool=prop&sort=name", max_queries=6)
    tour = Tour.objects.get()
    assert_simple_reads(client, f"/leaderboards/air/?tour={tour.pk}&pool=jet", max_queries=7)


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_leaderboard_selector_offers_all_time_as_all_and_filters_keep_it(client: Client) -> None:
    """No `?tour` is the current tour, so the selector's All time option must be `all` and survive other filters."""
    seed()

    default = client.get("/leaderboards/air/").content.decode()
    all_time = client.get("/leaderboards/air/?tour=all").content.decode()

    october = Tour.objects.get()
    assert '<option value="all">All time</option>' in default
    assert '<option value="" selected>Current tour</option>' in default  # no parameter = the current tour
    assert f'<option value="{october.pk}">' in default
    assert '<option value="all" selected>All time</option>' in all_time
    assert 'name="pool"' in all_time
    assert all_time.count('name="tour"') == 1  # the one form carries the tour with the pool and aircraft filters
    assert f"/players/{Player.objects.get(current_name='Ace').pk}/?tour=all" in all_time
    assert client.get("/leaderboards/air/?tour=all").context["tour"] is None


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_home_ground_board_links_keep_the_home_pages_tour(client: Client) -> None:
    """The home block lists the selected tour's top 5 (OQ-79), so its title and player links open that tour; the Elo
    boards follow the tour like the others (Elo resets every tour, OQ-128)."""
    seed()
    tour = Tour.objects.get()
    default = client.get("/").content.decode()
    assert f'href="/leaderboards/ground-hour/?tour={tour.pk}"' in default
    assert f"/players/{Player.objects.get(current_name='Pounder').pk}/?tour={tour.pk}" in default

    html = client.get("/?tour=all").content.decode()

    assert 'href="/leaderboards/ground-hour/?tour=all"' in html
    assert 'href="/leaderboards/elo-jet/?tour=all"' in html
    assert f"/players/{Player.objects.get(current_name='Pounder').pk}/?tour=all" in html
