"""Achievements / medals (FR-WEB-26, doc 17): ingest rows, incremental == rebuild, the upgrade backfill, the pages."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client

from il2ks.db.models import AchievementHolders, Player, PlayerAchievement, PlayerSortie, SiteSettings, Tour
from il2ks.ingest.achievements import recompute_holders
from il2ks.ingest.aggregates import rebuild_aggregates, recompute_players
from tests.factories import STARTED_AT, account, kill, meta, mission, save, sortie
from tests.ops_helpers import make_instance
from tests.simple_reads import PROFILE_READS_ALL_TIME, assert_simple_reads

pytestmark = pytest.mark.django_db

DAY2 = STARTED_AT + timedelta(days=1)
DAY3 = STARTED_AT + timedelta(days=2)


def pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def held(n: int, tour: Tour | None = None) -> dict[str, int]:
    """key -> highest tier held by player number `n` (all time, or in `tour`)."""
    best: dict[str, int] = {}
    for row in PlayerAchievement.objects.filter(player_id=pk(n), tour=tour):
        best[row.key] = max(best.get(row.key, 0), row.tier)
    return best


def snapshot() -> list[tuple[object, ...]]:
    rows = PlayerAchievement.objects.order_by("player_id", "tour_id", "key", "tier").values_list(
        "player_id", "tour_id", "key", "tier", "earned_at", "sortie_id", "mission_id"
    )
    counts = AchievementHolders.objects.order_by("tour_id", "key", "tier").values_list(
        "tour_id", "key", "tier", "holders", "pilots"
    )
    return [*rows, *counts]


def seed() -> None:
    """Player 1 gets kills over two missions and a death; player 2 flies one grounded and one landed sortie."""
    save(
        mission((sortie(0, 1, kills_air=3, kills_ground=60), sortie(1, 1, kills_air=2), sortie(2, 2))),
        meta("m1", STARTED_AT),
    )
    save(
        mission(
            (
                sortie(0, 1, kills_air=1, outcome="shot_down", is_death=True, is_plane_lost=True),
                sortie(1, 2, outcome="not_taken_off", flight_time_s=0.0),
                sortie(2, 3, aircraft_type="IL-10", ground_by_category={"tank": 6}),
            ),
            (kill(100, 0, 2, victim_type="IL-10"),),
        ),
        meta("m2", DAY2),
    )
    save(
        mission((sortie(0, 1, kills_air=4), sortie(1, 4, role="gunner", aircraft_type="Turret_IL10"))), meta("m3", DAY3)
    )


def test_medals_are_earned_in_the_sortie_that_reaches_them() -> None:
    seed()

    assert held(1)["life_kills"] == 1  # 3 + 2 = 5 in the first life; the death sortie adds 1
    first = PlayerAchievement.objects.get(player_id=pk(1), tour=None, key="life_kills", tier=1)
    assert first.mission.mission_uid == "m1"
    assert first.sortie.kills_air == 2  # the second sortie took the life to 5
    assert first.earned_at == first.sortie.ended_at
    assert held(1)["ground_sortie"] == 2  # 60 ground targets in one sortie
    assert held(3)["tank_buster"] == 1  # 6 tanks
    assert "life_kills" not in held(2)


def test_gunner_and_grounded_sorties_earn_nothing() -> None:
    seed()

    assert not PlayerAchievement.objects.filter(player_id=pk(4)).exists()  # a gunner only
    assert held(2).get("frequent_flyer") is None
    assert "regular" not in held(2)


def test_bomber_and_attacker_kills_count_for_the_killer() -> None:
    seed()
    assert PlayerAchievement.objects.filter(player_id=pk(1), key="strike_hunter", tier=1).exists()
    assert not PlayerAchievement.objects.filter(player_id=pk(2), key="strike_hunter").exists()


def test_friendly_fire_is_not_a_strike_kill() -> None:
    save(
        mission(
            (sortie(0, 1), sortie(1, 2, aircraft_type="IL-10")),
            (kill(100, 0, 1, victim_type="IL-10", is_friendly=True),),
        )
    )
    assert not PlayerAchievement.objects.filter(key="strike_hunter").exists()


def test_incremental_equals_rebuild_and_is_idempotent() -> None:
    seed()
    incremental = snapshot()
    assert PlayerAchievement.objects.exists()

    rebuild_aggregates()

    assert snapshot() == incremental
    recompute_players(Player.objects.values_list("pk", flat=True))
    assert snapshot() == incremental


def test_reprocessing_a_mission_keeps_or_removes_tiers() -> None:
    seed()
    before = snapshot()
    save(
        mission((sortie(0, 1, kills_air=3, kills_ground=60), sortie(1, 1, kills_air=2), sortie(2, 2))),
        meta("m1", STARTED_AT),
    )
    assert snapshot() == before

    save(mission((sortie(0, 1), sortie(1, 1), sortie(2, 2))), meta("m1", STARTED_AT))  # the kills were a mistake

    assert "ground_sortie" not in held(1)
    assert (
        held(1).get("life_kills", 0) < 1
        or PlayerAchievement.objects.filter(player_id=pk(1), tour=None, key="life_kills").count() <= 1
    )


def test_holders_count_visible_players_only() -> None:
    seed()
    assert AchievementHolders.objects.get(tour=None, key="life_kills", tier=1).holders == 1
    assert AchievementHolders.objects.get(tour=None, key="tank_buster", tier=1).holders == 1

    Player.objects.filter(pk=pk(1)).update(is_hidden=True)
    recompute_holders()

    assert AchievementHolders.objects.get(tour=None, key="tank_buster", tier=1).holders == 1
    assert not AchievementHolders.objects.filter(tour=None, key="life_kills").exists()
    assert PlayerAchievement.objects.filter(player_id=pk(1), key="ground_sortie").exists()  # only presentation hides


def test_the_upgrade_backfill_fills_an_old_database_once(tmp_path: Path) -> None:
    from il2ks.ops import migrate

    seed()
    good = snapshot()
    PlayerAchievement.objects.all().delete()
    AchievementHolders.objects.all().delete()

    migrate._backfill_achievements()  # pyright: ignore[reportPrivateUsage]

    assert snapshot() == good
    assert migrate.BACKFILL_ACHIEVEMENTS in SiteSettings.objects.get(pk=1).backfills_done
    PlayerAchievement.objects.all().delete()
    migrate._backfill_achievements()  # pyright: ignore[reportPrivateUsage]
    assert not PlayerAchievement.objects.exists()  # marked done: not repeated
    assert make_instance(tmp_path)


def test_the_backfill_marks_an_empty_database_without_work() -> None:
    from il2ks.ops import migrate

    migrate._backfill_achievements()  # pyright: ignore[reportPrivateUsage]

    assert migrate.BACKFILL_ACHIEVEMENTS in SiteSettings.objects.get(pk=1).backfills_done


# --- pages ----------------------------------------------------------------------------------------------------------
def test_profile_shows_the_best_tier_of_each_medal_and_stays_in_budget(client: Client) -> None:
    seed()

    html = client.get(f"/players/{pk(1)}/?tour=all").content.decode()

    assert "Charmed Life" in html
    assert "Bronze · 5" in html
    assert f"/players/{pk(1)}/achievements/" in html
    assert "Gold · 20" not in html
    assert_simple_reads(client, f"/players/{pk(1)}/?tour=all", max_queries=PROFILE_READS_ALL_TIME)


def test_a_profile_without_medals_has_no_medal_row(client: Client) -> None:
    seed()

    html = client.get(f"/players/{pk(2)}/?tour=all").content.decode()

    assert 'id="medals"' not in html


def test_player_achievement_list_shows_earned_and_open_tiers(client: Client) -> None:
    seed()
    url = f"/players/{pk(1)}/achievements/"

    html = client.get(url).content.decode()

    assert "Ace of the Sortie" in html
    assert "not yet" in html
    assert "Platinum" in html
    assert_simple_reads(client, url, max_queries=2 + 4)  # context 2, player, tours, rarity, medals


def test_hidden_player_has_no_achievement_page_and_is_not_listed(client: Client) -> None:
    seed()
    Player.objects.filter(pk=pk(1)).update(is_hidden=True)
    recompute_holders()

    assert client.get(f"/players/{pk(1)}/achievements/").status_code == 404
    html = client.get("/achievements/life_kills/?tour=all&tier=1").content.decode()
    assert "Player-1" not in html
    assert "Nobody holds this tier yet." in html


def test_overview_counts_holders_per_tier_and_links_to_them(client: Client) -> None:
    seed()

    html = client.get("/achievements/?tour=all").content.decode()

    assert "Charmed Life" in html
    assert "/achievements/ground_sortie/?tour=all&amp;tier=1" in html
    assert "nobody yet" in html
    assert "Held by" in html  # the rarity of every tier somebody holds
    assert_simple_reads(client, "/achievements/", max_queries=2 + 2)  # context 2, tours, holder counts


def test_holders_page_lists_the_pilots_of_a_tier(client: Client) -> None:
    seed()

    html = client.get("/achievements/ground_sortie/?tour=all&tier=2").content.decode()
    default = client.get("/achievements/ground_sortie/?tour=all").content.decode()  # the highest tier anybody holds

    assert "Player-1" in html
    assert "Player-1" in default
    assert client.get("/achievements/nothing/").status_code == 404
    assert client.get("/achievements/ground_sortie/?tier=x").status_code == 200
    assert_simple_reads(
        client, "/achievements/ground_sortie/?tour=all&tier=2", max_queries=2 + 4
    )  # tours, holder counts, count, page


def test_sortie_page_lists_what_the_sortie_earned(client: Client) -> None:
    seed()
    reaching = PlayerSortie.objects.get(player_id=pk(1), kills_ground=60)
    quiet = PlayerSortie.objects.get(player_id=pk(2), mission__mission_uid="m1")

    html = client.get(f"/sorties/{reaching.pk}/").content.decode()

    assert "Earned in this sortie" in html
    assert "Target-Rich" in html
    assert "Earned in this sortie" not in client.get(f"/sorties/{quiet.pk}/").content.decode()


def test_a_hidden_missions_sortie_is_not_linked_from_the_medal(client: Client) -> None:
    seed()
    from il2ks.db.models import Mission

    Mission.objects.filter(mission_uid="m1").update(is_hidden=True)
    sortie_pk = PlayerAchievement.objects.get(player_id=pk(1), tour=None, key="ground_sortie", tier=1).sortie_id

    html = client.get(f"/players/{pk(1)}/achievements/?tour=all").content.decode()

    assert f"/sorties/{sortie_pk}/" not in html


# --- per tour, rarity, ribbons, the feed (doc 17, OQ-105) -----------------------------------------------------------
OCTOBER = STARTED_AT + timedelta(days=30)


def seed_two_tours() -> tuple[Tour, Tour]:
    """Player 1 shoots down 3 in September and 3 in October (both survived): 6 in one life all time, 3 in each tour."""
    save(mission((sortie(0, 1, kills_air=3), sortie(1, 2, kills_air=1))), meta("t1", STARTED_AT))
    save(mission((sortie(0, 1, kills_air=3), sortie(1, 3))), meta("t2", OCTOBER))
    september, october = Tour.objects.order_by("started_at")
    return september, october


def test_tours_start_afresh() -> None:
    september, october = seed_two_tours()

    assert held(1)["life_kills"] == 1  # 3 + 3 = 6 in the one life, all time
    assert "life_kills" not in held(1, september)  # only 3 in a tour
    assert "life_kills" not in held(1, october)
    assert held(1, september)["career_kills"] == 1
    assert held(1, october)["career_kills"] == 1
    row = PlayerAchievement.objects.get(player_id=pk(1), tour=october, key="career_kills", tier=1)
    assert row.mission.mission_uid == "t2"  # the first kill of the tour, not of the career


def test_per_tour_rows_incremental_equals_rebuild() -> None:
    seed_two_tours()
    incremental = snapshot()

    rebuild_aggregates()
    assert snapshot() == incremental
    recompute_players(Player.objects.values_list("pk", flat=True))
    assert snapshot() == incremental


def test_holders_and_pilots_are_counted_per_scope() -> None:
    september, october = seed_two_tours()

    everyone = AchievementHolders.objects.get(tour=None, key="career_kills", tier=1)
    assert (everyone.holders, everyone.pilots) == (2, 3)  # players 1 and 2 shot something down; 1 to 3 flew
    in_october = AchievementHolders.objects.get(tour=october, key="career_kills", tier=1)
    assert (in_october.holders, in_october.pilots) == (1, 2)  # players 1 and 3
    assert AchievementHolders.objects.get(tour=september, key="career_kills", tier=1).pilots == 2  # players 1 and 2

    Player.objects.filter(pk=pk(3)).update(is_hidden=True)
    recompute_holders()

    assert AchievementHolders.objects.get(tour=october, key="career_kills", tier=1).pilots == 1


def test_profile_and_lists_follow_the_tour() -> None:
    _, october = seed_two_tours()
    client = Client()

    all_time = client.get(f"/players/{pk(1)}/?tour=all").content.decode()
    in_october = client.get(f"/players/{pk(1)}/?tour={october.pk}").content.decode()
    listing = client.get(f"/players/{pk(1)}/achievements/?tour={october.pk}").content.decode()

    assert "Charmed Life" in all_time
    assert "Charmed Life" not in in_october
    assert "Sky Hunter" in in_october
    assert "Charmed Life" in listing  # the list shows every achievement, earned or not
    assert "not yet" in listing
    assert f"/players/{pk(1)}/achievements/?tour={october.pk}" in in_october


def test_rarity_is_hover_text_and_screen_reader_text() -> None:
    seed_two_tours()
    client = Client()

    html = client.get(f"/players/{pk(1)}/?tour=all").content.decode()

    assert "Held by 33% of pilots" in html  # 1 of 3 pilots
    assert 'data-tooltip="' in html
    assert 'class="visually-hidden">' in html
    assert "rarity--" not in html  # a three-pilot server does not glow


def test_a_medal_row_and_a_ribbon_rack_split_by_kind() -> None:
    seed()
    html = Client().get(f"/players/{pk(1)}/?tour=all").content.decode()

    assert 'class="medal-row"' in html
    assert "Charmed Life" in html
    assert 'class="ribbon-rack"' in html
    assert "Sky Hunter" in html
    assert html.index("Charmed Life") < html.index("Sky Hunter")  # medals first, then the ribbon rack


def test_the_sortie_page_shows_both_scopes() -> None:
    seed_two_tours()
    first = PlayerSortie.objects.get(player_id=pk(1), mission__mission_uid="t1")

    html = Client().get(f"/sorties/{first.pk}/").content.decode()

    assert "Earned in this sortie" in html
    assert "All time" in html
    assert "medal-scope" in html
    assert html.count("Sky Hunter") == 2  # the all-time first kill and the tour's


def test_the_home_feed_lists_the_newest_uncommon_tiers_and_never_links_a_hidden_mission() -> None:
    seed()
    client = Client()

    html = client.get("/?tour=all").content.decode()

    assert "Recently earned" in html
    assert "Target-Rich" in html  # silver (tier 2): in the feed
    assert "Sky Hunter</a> · Bronze" not in html  # a bronze ribbon: left out
    assert "Charmed Life" not in html  # bronze medal that a quarter of the pilots hold: too common
    sortie_pk = PlayerAchievement.objects.get(player_id=pk(1), tour=None, key="ground_sortie", tier=2).sortie_id
    assert f"/sorties/{sortie_pk}/" in html

    from il2ks.db.models import Mission

    Mission.objects.filter(mission_uid="m1").update(is_hidden=True)
    hidden = client.get("/?tour=all").content.decode()
    assert "Target-Rich" in hidden
    assert f"/sorties/{sortie_pk}/" not in hidden

    Player.objects.filter(pk=pk(1)).update(is_hidden=True)
    assert "Target-Rich" not in client.get("/?tour=all").content.decode()


def test_hall_of_shame_entries_stay_out_of_the_feed_and_the_medal_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `shame` achievement is shown with the hall of shame only (doc 17)."""
    from dataclasses import replace

    from il2ks.core import achievements as core
    from il2ks.queries import achievements as reads
    from il2ks.web import medals
    from il2ks.web.templatetags import il2ks_achievements as tags

    shamed = tuple(replace(a, shame=True) if a.key == "ground_sortie" else a for a in core.ACHIEVEMENTS)
    by_key = {a.key: a for a in shamed}
    for module in (core, reads, medals):
        monkeypatch.setattr(module, "ACHIEVEMENTS", shamed)
    for module in (core, medals, tags):
        monkeypatch.setattr(module, "BY_KEY", by_key)
    seed()
    client = Client()

    feed = client.get("/?tour=all").content.decode()
    profile = client.get(f"/players/{pk(1)}/?tour=all").content.decode()

    assert "Target-Rich" not in feed
    shame_part = profile[profile.index('class="shame"') :]
    assert "Target-Rich" in shame_part
    assert profile.index("Target-Rich") > profile.index('class="shame"')  # not in the medal row above it


def test_the_upgrade_backfill_adds_the_tour_rows_once() -> None:
    from il2ks.ops import migrate

    seed_two_tours()
    good = snapshot()
    PlayerAchievement.objects.filter(tour__isnull=False).delete()
    AchievementHolders.objects.filter(tour__isnull=False).delete()
    AchievementHolders.objects.update(pilots=0)
    SiteSettings.objects.filter(pk=1).update(backfills_done=[migrate.BACKFILL_ACHIEVEMENTS])

    migrate._backfill_achievement_tours()  # pyright: ignore[reportPrivateUsage]

    assert snapshot() == good
    assert migrate.BACKFILL_ACHIEVEMENT_TOURS in SiteSettings.objects.get(pk=1).backfills_done
