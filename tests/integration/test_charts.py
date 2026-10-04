"""Charts (FR-WEB-16): the daily activity table at ingest, the home and profile charts, hidden rows, query counts."""

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from pytest_django.fixtures import Settings

from il2ks.db.models import ActivityDay, Mission, Player, PlayerTour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.queries.activity import ACTIVITY_DAYS
from il2ks.web.chart_data import activity_chart, player_charts
from tests.factories import STARTED_AT, meta, mission, rows, save, sortie
from tests.simple_reads import PROFILE_READS_TOUR, assert_simple_reads

pytestmark = pytest.mark.django_db

DAY1 = date(2026, 9, 19)  # STARTED_AT's UTC day


def fly(uid: str, started_at: datetime, *players: int, kills_air: int = 0, gunner: int | None = None) -> Mission:
    sorties = [sortie(i, p, name=f"P{p}", kills_air=kills_air if i == 0 else 0) for i, p in enumerate(players)]
    if gunner is not None:
        sorties.append(sortie(len(sorties), gunner, aircraft_type="Turret_IL10", role="gunner"))
    return save(mission(tuple(sorties)), meta(uid, started_at))


def seed_days() -> None:
    fly("2026-09-19_20-00-00", STARTED_AT, 1, 2, kills_air=3)
    fly("2026-09-19_23-00-00", STARTED_AT + timedelta(hours=3), 2, 3, gunner=9)  # same UTC day, player 2 again
    fly("2026-09-21_10-00-00", STARTED_AT + timedelta(days=2), 1)


def activity() -> dict[date, tuple[int, int, int, int]]:
    return {r.day: (r.missions, r.sorties, r.pilots, r.kills_air) for r in ActivityDay.objects.all()}


# --- level 2 ------------------------------------------------------------------------------------------------------
def test_days_hold_missions_sorties_distinct_pilots_and_kills() -> None:
    seed_days()

    # day 1: 2 missions, 2 + 2 pilot sorties (the gunner sortie is not counted), pilots 1, 2, 3
    assert activity() == {DAY1: (2, 4, 3, 3), date(2026, 9, 21): (1, 1, 1, 0)}


def test_incremental_equals_rebuild_and_reingest_is_stable() -> None:
    seed_days()
    fly("2026-09-19_20-00-00", STARTED_AT, 1, 2, kills_air=3)  # the same mission again
    before = rows(ActivityDay)

    rebuild_aggregates()

    assert [{k: v for k, v in r.items() if k != "id"} for r in rows(ActivityDay)] == [
        {k: v for k, v in r.items() if k != "id"} for r in before
    ]


def test_rebuild_repairs_a_drifted_or_stale_day() -> None:
    seed_days()
    ActivityDay.objects.filter(day=DAY1).update(sorties=999)
    ActivityDay.objects.create(day=date(2026, 1, 1), missions=1, sorties=5, pilots=2)

    rebuild_aggregates()

    assert activity()[DAY1][1] == 4
    assert date(2026, 1, 1) not in activity()


def test_hidden_missions_are_not_counted_and_hiding_through_the_admin_updates_the_day(client: Client) -> None:
    seed_days()
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    second = Mission.objects.get(mission_uid="2026-09-19_23-00-00")

    client.post("/admin/il2ks_db/mission/", {"action": "hide_selected", "_selected_action": [second.pk]})

    assert activity()[DAY1] == (1, 2, 2, 3)

    only = Mission.objects.get(mission_uid="2026-09-21_10-00-00")
    client.post("/admin/il2ks_db/mission/", {"action": "hide_selected", "_selected_action": [only.pk]})
    assert date(2026, 9, 21) not in activity()  # no visible mission left: no row

    client.post("/admin/il2ks_db/mission/", {"action": "unhide_selected", "_selected_action": [second.pk, only.pk]})
    assert activity() == {DAY1: (2, 4, 3, 3), date(2026, 9, 21): (1, 1, 1, 0)}


def test_a_hidden_mission_stays_out_of_a_rebuild() -> None:
    seed_days()
    Mission.objects.filter(mission_uid="2026-09-19_23-00-00").update(is_hidden=True)

    rebuild_aggregates()

    assert activity()[DAY1] == (1, 2, 2, 3)


# --- chart data ---------------------------------------------------------------------------------------------------
def day_row(day: date, sorties: int, pilots: int = 1) -> ActivityDay:
    return ActivityDay(day=day, missions=1, sorties=sorties, pilots=pilots, kills_air=0)


def test_activity_chart_fills_quiet_days_with_zeros_and_ends_at_the_newest_day() -> None:
    spec = activity_chart([day_row(date(2026, 9, 10), 5), day_row(date(2026, 9, 13), 8)])

    assert spec is not None
    assert len(spec.categories) == 4  # a young site starts at its first active day
    assert spec.series[0].values == (5, 0, 0, 8)
    assert spec.series[1].label == "Pilots"
    assert not spec.series[1].plotted


def test_activity_chart_keeps_only_the_last_30_days() -> None:
    last = date(2026, 9, 30)
    spec = activity_chart(
        [day_row(last - timedelta(days=40), 9), day_row(last - timedelta(days=29), 1), day_row(last, 2)]
    )

    assert spec is not None
    assert len(spec.categories) == ACTIVITY_DAYS
    assert spec.series[0].values[0] == 1
    assert spec.series[0].values[-1] == 2
    assert 9 not in spec.series[0].values


def test_activity_chart_is_none_without_rows() -> None:
    assert activity_chart([]) is None


# --- home page ----------------------------------------------------------------------------------------------------
def test_home_without_missions_has_no_chart(client: Client) -> None:
    assert b'class="chart"' not in client.get("/").content


def test_home_shows_the_activity_chart_with_a_table_and_accessible_names(client: Client) -> None:
    seed_days()

    assert_simple_reads(
        client, "/", max_queries=7
    )  # context processor 2, latest, top pilots, activity, streaks, one spare
    html = client.get("/").content.decode()

    assert 'class="chart"' in html
    assert 'role="img"' in html
    assert 'aria-labelledby="chart-activity-t chart-activity-d"' in html
    assert "<title>19 Sep - Sorties: 4</title>" in html  # a tooltip per bar
    assert "Show the numbers" in html


def test_home_chart_leaves_out_hidden_missions(client: Client) -> None:
    seed_days()
    Mission.objects.update(is_hidden=True)
    rebuild_aggregates()

    assert b'class="chart"' not in client.get("/").content


# --- profile ------------------------------------------------------------------------------------------------------
def fly_tours() -> int:
    """Player 1 flies in August (1 sortie) and September (2 sorties, 3 air kills, 1 death): two monthly tours."""
    save(mission((sortie(0, 1, name="Maverick"),)), meta("2026-08-10_10-00-00", datetime(2026, 8, 10, 8, tzinfo=UTC)))
    save(
        mission((sortie(0, 1, name="Maverick", kills_air=3, is_death=True), sortie(1, 1, name="Maverick"))),
        meta("2026-09-10_10-00-00", datetime(2026, 9, 10, 8, tzinfo=UTC)),
    )
    return Player.objects.get().pk


def test_profile_shows_per_tour_charts_for_two_tours(client: Client) -> None:
    pk = fly_tours()

    assert_simple_reads(client, f"/players/{pk}/", max_queries=PROFILE_READS_TOUR)
    html = client.get(f"/players/{pk}/").content.decode()

    assert html.count('class="chart"') == 2
    assert "Sorties per tour" in html
    assert "Air kills and deaths per tour" in html
    assert "August 2026" in html  # the tour title is a category (table row)
    assert "<title>September 2026 - Air kills: 3</title>" in html
    assert "<title>September 2026 - Deaths: 1</title>" in html


def test_profile_with_a_single_tour_has_no_chart(client: Client) -> None:
    fly("2026-09-19_20-00-00", STARTED_AT, 1)
    pk = Player.objects.get().pk

    assert b'class="chart"' not in client.get(f"/players/{pk}/").content


def test_hidden_player_still_404s_and_player_charts_need_two_tours(client: Client) -> None:
    pk = fly_tours()
    Player.objects.filter(pk=pk).update(is_hidden=True)

    assert client.get(f"/players/{pk}/").status_code == 404
    assert player_charts(list(PlayerTour.objects.select_related("tour")[:1])) == ()


def test_styleguide_shows_the_charts(client: Client, settings: Settings) -> None:
    settings.DEBUG = True
    html = client.get("/_styleguide/").content.decode()

    assert html.count('class="chart"') == 4
    assert "Nothing to show yet." in html  # the all-zero sample


# --- localized numbers (regression: floatformat localizes in de/ru/fr/pt-br/es) ---------------------------------
@pytest.mark.parametrize("path", ["home", "profile"])
def test_chart_coordinates_stay_dot_decimals_in_german(client: Client, path: str) -> None:
    """SVG lengths must use '.' whatever the language: '134,0' is an invalid length."""
    if path == "home":
        seed_days()
        url = "/"
    else:
        url = f"/players/{fly_tours()}/"
    client.get("/language/", {"language": "de", "next": "/"})

    html = client.get(url).content.decode()

    svg = html[html.index('<svg class="chart__svg"') :].split("</svg>")[0]
    numeric = re.findall(r'\b(?:x|y|x1|x2|y1|y2|dx|dy)="([^"]*)"', svg)
    assert numeric
    assert not [v for v in numeric if "," in v]
    assert re.search(r'y1="\d+\.\d"', svg)
