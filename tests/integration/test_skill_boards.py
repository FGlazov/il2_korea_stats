"""The interception and tank-busting skill boards (FR-WEB-7, FR-WEB-20, doc 13, maintainer request 2026-10-04): the
counters behind them, ranking and minimums, the pool filter, the home block, the profile rows and marks, rebuild ==
incremental, the backfill for upgraded databases. Synthetic data only."""

import uuid
from pathlib import Path

import pytest
from django.test import Client, override_settings

from il2ks.config import Config, LeaderboardConfig
from il2ks.core.stat_marks import MarkRules
from il2ks.db.models import GameObject, Player, PlayerPool, PlayerSortie, PlayerTour, SiteSettings, StatThreshold
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.stat_marks import recompute_thresholds
from il2ks.ops import migrate
from il2ks.queries.leaderboards import BoardRow
from tests.factories import account, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

AIR = "air_superiority"
LOW = LeaderboardConfig(
    min_sorties=1,
    min_elo_games=1,
    min_attack_sorties=1,
    min_time_on_target_minutes=1.0,
    min_air_superiority_sorties=1,
    min_air_superiority_minutes=1.0,
)


def rows(client: Client, url: str) -> list[tuple[str, float | None]]:
    response = client.get(url)
    assert response.status_code == 200
    page: list[BoardRow] = response.context["page_obj"].object_list
    return [(r.player.current_name, r.per_hour) for r in page]


def seed() -> None:
    """Hunter: two air superiority sorties of half an hour, 4 bombers/attackers (4 per hour). Sniper: 20 minutes, 2
    (6 per hour). Rider: an attack sortie with 3 such kills (not an air superiority sortie: not on the board).
    Plain: an hour, a fighter kill only. Pounder: 3 tanks in 10 minutes on target (18 per hour). Mixed: 5 tanks
    in a guns-only sortie (no time on target) and 1 in an hour on target in an attack sortie (1 per hour)."""
    save(
        mission(
            (
                sortie(
                    0, 1, name="Hunter", combat_role=AIR, flight_time_s=1800.0, kills_air_ai=3, kills_air_intercept=3
                ),
                sortie(
                    1, 1, name="Hunter", combat_role=AIR, flight_time_s=1800.0, kills_air_ai=2, kills_air_intercept=1
                ),
                sortie(
                    2, 2, name="Sniper", combat_role=AIR, flight_time_s=1200.0, kills_air_ai=2, kills_air_intercept=2
                ),
                sortie(
                    3,
                    3,
                    name="Rider",
                    combat_role="attack",
                    flight_time_s=3600.0,
                    kills_air_ai=3,
                    kills_air_intercept=3,
                ),
                sortie(4, 4, name="Plain", combat_role=AIR, flight_time_s=3600.0, kills_air_ai=1),
                sortie(
                    5,
                    5,
                    name="Pounder",
                    aircraft_type="Il-10",
                    combat_role="attack",
                    ground_by_category={"tank": 3},
                    time_on_target_s=600.0,
                ),
                sortie(6, 6, name="Mixed", combat_role=AIR, ground_by_category={"tank": 5}),
                sortie(
                    7,
                    6,
                    name="Mixed",
                    combat_role="attack",
                    ground_by_category={"tank": 1},
                    time_on_target_s=3600.0,
                ),
            )
        )
    )


def test_counters_split_the_roles() -> None:
    seed()

    hunter = Player.objects.get(account_uuid=account(1))
    assert (hunter.air_superiority_sorties, hunter.flight_time_air_s, hunter.kills_intercept) == (2, 3600.0, 4)
    rider = Player.objects.get(account_uuid=account(3))
    assert (rider.air_superiority_sorties, rider.flight_time_air_s, rider.kills_intercept) == (0, 0.0, 0)
    mixed = Player.objects.get(account_uuid=account(6))
    assert (mixed.kills_ground_tank, mixed.kills_tank_attack) == (6, 1)  # tanks of the guns-only sortie don't count
    assert PlayerSortie.objects.get(name_at_time="Rider").kills_air_intercept == 3  # stored for every sortie


def test_player_tour_and_pool_rows_carry_them_and_rebuild_equals_incremental() -> None:
    seed()
    fields = ("air_superiority_sorties", "flight_time_air_s", "kills_intercept", "kills_tank_attack")
    before = [list(m.objects.order_by("pk").values(*fields)) for m in (Player, PlayerTour, PlayerPool)]
    assert before[1][0]["kills_intercept"] + before[1][1]["kills_intercept"] > 0

    rebuild_aggregates()

    assert [list(m.objects.order_by("pk").values(*fields)) for m in (Player, PlayerTour, PlayerPool)] == before


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_interception_ranks_kills_per_air_superiority_hour(client: Client) -> None:
    seed()

    assert rows(client, "/leaderboards/interception/") == [
        ("Sniper", pytest.approx(6.0)),
        ("Hunter", pytest.approx(4.0)),
        ("Mixed", 0.0),  # ties: by name
        ("Plain", 0.0),
    ]  # Rider flew no air superiority sortie, Pounder neither
    page = client.get("/leaderboards/interception/").content.decode()
    assert "Bombers and attackers shot down" in page
    assert [n for n, _ in rows(client, "/leaderboards/interception/?pool=jet")][:2] == ["Sniper", "Hunter"]
    assert [n for n, _ in rows(client, "/leaderboards/interception/?pool=prop")] == []


def test_interception_minimums(client: Client) -> None:
    seed()

    def listed(**kwargs: float) -> list[str]:
        with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_sorties=1, **kwargs)):  # type: ignore[arg-type]
            return [n for n, _ in rows(client, "/leaderboards/interception/")]

    assert listed(min_air_superiority_sorties=1, min_air_superiority_minutes=30) == ["Hunter", "Plain"]
    assert set(listed(min_air_superiority_sorties=2, min_air_superiority_minutes=0)) == {"Hunter"}
    assert listed(min_air_superiority_sorties=1, min_air_superiority_minutes=90) == []
    body = client.get("/leaderboards/interception/").content.decode()
    assert "air superiority sorties and" in body


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_tank_busting_counts_tanks_of_attack_sorties_per_hour_on_target(client: Client) -> None:
    seed()

    assert rows(client, "/leaderboards/tank-busting/") == [
        ("Pounder", pytest.approx(18.0)),
        ("Mixed", pytest.approx(1.0)),
    ]
    assert [n for n, _ in rows(client, "/leaderboards/tank-busting/?pool=prop")] == ["Pounder"]
    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_attack_sorties=1, min_time_on_target_minutes=30)):
        assert [n for n, _ in rows(client, "/leaderboards/tank-busting/")] == ["Mixed"]
    assert "minutes on target" in client.get("/leaderboards/tank-busting/").content.decode()


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_sorting_whitelist_and_tours(client: Client) -> None:
    seed()

    assert client.get("/leaderboards/interception/?sort=kills").context["sort"] == "kills"
    assert client.get("/leaderboards/interception/?sort=bogus").context["sort"] == "-per_hour"
    assert client.get("/leaderboards/tank-busting/?sort=tanks").context["sort"] == "tanks"
    assert rows(client, "/leaderboards/interception/?tour=all")[0][0] == "Sniper"  # the all-time table
    assert rows(client, "/leaderboards/interception/")[0][0] == "Sniper"  # the current tour


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_home_block_lists_both_skill_boards_with_all_time_links(client: Client) -> None:
    seed()

    response = client.get("/?tour=all")

    boards = {b.key: [(r.player.current_name, r.per_hour) for r in b.rows] for b in response.context["boards"]}
    assert boards["interception"][0] == ("Sniper", pytest.approx(6.0))
    assert boards["tank-busting"][0] == ("Pounder", pytest.approx(18.0))
    html = response.content.decode()
    assert 'href="/leaderboards/interception/?tour=all"' in html
    assert 'href="/leaderboards/tank-busting/?tour=all"' in html
    assert "6.00" in html


def test_the_switcher_has_the_new_boards_and_no_clipped_markup(client: Client) -> None:
    body = client.get("/leaderboards/").content.decode()

    assert 'class="board-tabs" role="navigation"' in body
    assert body.index("Interception") < body.index("Tank busting")
    context = client.get("/leaderboards/").context
    air = [t[1] for title, tabs in context["tab_groups"] if title == "Air" for t in tabs]
    ground = [t[1] for title, tabs in context["tab_groups"] if title == "Ground" for t in tabs]
    assert [str(t) for t in air] == ["Elo, jet", "Elo, prop", "Air score", "Interception"]
    assert [str(t) for t in ground] == ["Ground score per hour", "Tank busting", "Ground score"]
    icons = {t[0]: t[4] for _title, tabs in context["tab_groups"] for t in tabs}
    assert icons["elo-jet"] != icons["elo-prop"]
    assert icons["interception"] == "stat/interception"


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_budgets(client: Client) -> None:
    seed()

    assert_simple_reads(client, "/leaderboards/interception/", max_queries=6)
    assert_simple_reads(client, "/leaderboards/tank-busting/?pool=prop&sort=name", max_queries=6)
    assert_simple_reads(client, "/leaderboards/interception/?tour=all", max_queries=6)


# --- stat marks ------------------------------------------------------------------------------------------------------

RULES = MarkRules(min_sorties=1, min_elo_games=1, min_time_on_target_s=600.0, min_air_superiority_s=3600.0)


def seed_marks() -> None:
    """Pilot n (1..25): n hours of air superiority flight with n * n bomber kills (n per hour) and n hours on target
    with 2 n * n tanks (2 n per hour) in attack sorties."""
    save(mission(tuple(sortie(i, i + 1, kills_air=1, kills_ground=1) for i in range(25))))
    for n in range(1, 26):
        Player.objects.filter(account_uuid=account(n)).update(
            flight_time_air_s=n * 3600.0,
            kills_intercept=n * n,
            time_on_target_s=n * 3600.0,
            kills_tank_attack=2 * n * n,
        )
    recompute_thresholds(RULES)


def test_mark_populations_follow_the_boards_minimums() -> None:
    seed_marks()

    marks = {r.metric: r for r in StatThreshold.objects.filter(tour=None)}
    assert (marks["interception_hour"].population, marks["interception_hour"].min_sorties) == (25, 3600)
    assert marks["interception_hour"].p50 == pytest.approx(13.0)
    assert (marks["tank_hour"].population, marks["tank_hour"].min_sorties) == (25, 600)
    assert marks["tank_hour"].p50 == pytest.approx(26.0)
    Player.objects.filter(account_uuid=account(1)).update(flight_time_air_s=3599.0)
    recompute_thresholds(RULES)
    assert StatThreshold.objects.get(tour=None, metric="interception_hour").population == 24  # under the minimum


def test_marks_render_next_to_the_numbers(client: Client) -> None:
    seed_marks()
    best = Player.objects.get(account_uuid=account(25)).pk

    page = client.get(f"/players/{best}/?tour=all").content.decode()

    assert "Interception per hour" in page
    assert "Tanks destroyed per hour on target" in page
    assert "Better than 9 in 10 pilots with at least 60 minutes of air superiority flight" in page
    assert "Better than 9 in 10 pilots with at least 10 minutes on target" in page
    weak = Player.objects.get(account_uuid=account(2)).pk
    assert (
        "pilots with at least 60 minutes of air superiority"
        not in client.get(f"/players/{weak}/?tour=all").content.decode()
    )


# --- upgraded databases -----------------------------------------------------------------------------------------------


def test_the_backfill_derives_the_sortie_column_from_the_timelines_and_rebuilds_level_2() -> None:
    seed()
    GameObject.objects.update_or_create(log_name="B-29", defaults={"display_name": "B-29", "cls": "bomber"})
    rider = PlayerSortie.objects.get(name_at_time="Rider")  # an attack sortie: shooting it down is an interception
    plain = PlayerSortie.objects.get(name_at_time="Plain")  # an air superiority sortie
    killer = PlayerSortie.objects.get(name_at_time="Plain")
    killer.timeline = [
        {"kind": "kill", "counterpart": {"object_type": "B-29", "sortie_id": None, "coalition": 1}},
        {"kind": "kill", "counterpart": {"object_type": "MiG-15bis", "sortie_id": None, "coalition": 1}},
        {"kind": "kill", "counterpart": {"object_type": "MiG-15bis", "sortie_id": plain.pk, "coalition": 1}},
        {"kind": "kill", "counterpart": {"object_type": "MiG-15bis", "sortie_id": rider.pk, "coalition": 1}},
        {"kind": "assist", "counterpart": {"object_type": "B-29", "sortie_id": None, "coalition": 1}},
    ]
    killer.save(update_fields=["timeline"])
    PlayerSortie.objects.update(kills_air_intercept=0)
    Player.objects.update(kills_intercept=0, flight_time_air_s=0.0)
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])
    cfg = Config(data_dir=Path("."), server_uid=uuid.uuid4(), timezone_name="UTC")

    migrate._run_backfills(cfg, [migrate.BACKFILL_INTERCEPTION])  # pyright: ignore[reportPrivateUsage]

    assert PlayerSortie.objects.get(pk=killer.pk).kills_air_intercept == 2  # the B-29 and the attack sortie
    assert Player.objects.get(account_uuid=account(4)).flight_time_air_s == 3600.0
    assert Player.objects.get(account_uuid=account(4)).kills_intercept == 2
    assert migrate._already_done(migrate.BACKFILL_INTERCEPTION)  # pyright: ignore[reportPrivateUsage]
