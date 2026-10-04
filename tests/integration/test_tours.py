"""Tours (TD-26, FR-WEB-10, FR-ADM-8): assignment per mode and timezone, per-tour level 2, retour, admin, selector."""

import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core import management
from django.db import transaction
from django.db.migrations.executor import MigrationExecutor
from django.template import Context, Template
from django.test import Client, RequestFactory
from pytest_django.fixtures import Settings

from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.core.replay.result import MissionResult
from il2ks.core.tours import TourRules
from il2ks.db.models import (
    AircraftStats,
    Counters,
    GameObject,
    Mission,
    Player,
    PlayerAircraft,
    PlayerMission,
    PlayerTour,
    PlayerTourAircraft,
    Tour,
    TourAircraftStats,
)
from il2ks.db.site import current_data_version
from il2ks.ingest.aggregates import rebuild_aggregates, recompute_players
from il2ks.ingest.aircraft_stats import recompute_aircraft_stats
from il2ks.ingest.counters import COUNTER_FIELDS, FLOAT_COUNTERS
from il2ks.ingest.persist import save_mission
from il2ks.ingest.tours import assign_missing, retour, start_manual_tour, tour_problems
from il2ks.ops.checks import tours_check
from il2ks.ops.doctor import Level
from il2ks.ops.migrate import migrate_if_needed
from il2ks.queries.tours import (
    current_tour,
    player_tour,
    player_tour_aircraft,
    tour_choice,
    tour_leaderboard,
    tour_options,
)
from tests.factories import FakeCatalog, account, meta, mission, sortie
from tests.ops_helpers import make_instance, recording, returning

pytestmark = pytest.mark.django_db

MONTHLY = TourRules(mode="monthly", timezone_name="UTC")
SEOUL_MONTHLY = TourRules(mode="monthly", timezone_name="Asia/Seoul")  # UTC+9
MANUAL = TourRules(mode="manual", timezone_name="UTC")


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def put(result: MissionResult, started_at: datetime, rules: TourRules = MONTHLY, uid: str | None = None) -> Mission:
    """Save one mission at `started_at` under `rules` (the UID defaults to one that is unique per start time)."""
    mission_uid = uid or started_at.strftime("%Y-%m-%d_%H-%M-%S")
    with transaction.atomic():
        return save_mission(result, meta(mission_uid, started_at), FakeCatalog(), DEFAULT_RULES, rules)


def titles() -> list[str]:
    return [t.title for t in Tour.objects.order_by("started_at")]


def tour_of(mission_uid: str) -> Tour:
    tour = Mission.objects.get(mission_uid=mission_uid).tour
    assert tour is not None
    return tour


# --- assignment per mode ---


def test_monthly_creates_one_tour_per_month_on_first_use() -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 19, 20))
    put(mission((sortie(0, 1),)), at(2026, 9, 20, 20))
    put(mission((sortie(0, 1),)), at(2026, 10, 2, 20))

    assert titles() == ["September 2026", "October 2026"]
    sept, octo = Tour.objects.order_by("started_at")
    assert (sept.started_at, sept.ended_at, sept.mode) == (at(2026, 9, 1), at(2026, 10, 1), "monthly")
    assert (octo.started_at, octo.ended_at) == (at(2026, 10, 1), at(2026, 11, 1))
    assert Mission.objects.filter(tour=sept).count() == 2


def test_a_mission_at_0030_local_on_the_first_belongs_to_the_new_month() -> None:
    """TD-26: boundaries are drawn in `tours.timezone`. 00:30 on 1 October in Seoul is still 30 September in UTC."""
    put(mission((sortie(0, 1),)), at(2026, 9, 30, 15, 30), SEOUL_MONTHLY, uid="2026-10-01_00-30-00")
    put(mission((sortie(0, 1),)), at(2026, 9, 30, 14, 30), SEOUL_MONTHLY, uid="2026-09-30_23-30-00")

    assert tour_of("2026-10-01_00-30-00").title == "October 2026"
    assert tour_of("2026-09-30_23-30-00").title == "September 2026"
    assert tour_of("2026-10-01_00-30-00").started_at == at(2026, 9, 30, 15)


def test_the_same_instant_lands_in_another_tour_under_utc() -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 30, 15, 30), MONTHLY)

    assert titles() == ["September 2026"]


def test_days_mode_blocks_from_the_start_date() -> None:
    rules = TourRules(mode="days", days=14, start=date(2026, 10, 1), timezone_name="UTC")
    put(mission((sortie(0, 1),)), at(2026, 10, 14, 23), rules)
    put(mission((sortie(0, 1),)), at(2026, 10, 15, 1), rules)
    put(mission((sortie(0, 1),)), at(2026, 10, 20, 1), rules)

    assert titles() == ["Tour 1", "Tour 2"]
    first, second = Tour.objects.order_by("started_at")
    assert (first.mode, first.started_at, first.ended_at) == ("days:14", at(2026, 10, 1), at(2026, 10, 15))
    assert Mission.objects.filter(tour=second).count() == 2


def test_manual_mode_creates_the_open_tour_on_first_use_and_keeps_filling_it() -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 19, 20), MANUAL)
    put(mission((sortie(0, 1),)), at(2027, 3, 1, 20), MANUAL)  # months later: still the same tour

    tour = Tour.objects.get()
    assert (tour.title, tour.started_at, tour.ended_at, tour.mode) == ("Tour 1", at(2026, 9, 19, 20), None, "manual")
    assert Mission.objects.filter(tour=tour).count() == 2


def test_manual_new_tour_closes_the_open_one_and_takes_later_missions() -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 19, 20), MANUAL)
    new = start_manual_tour(at(2026, 10, 1, 12))
    put(mission((sortie(0, 1),)), at(2026, 10, 2, 20), MANUAL)
    put(mission((sortie(0, 1),)), at(2026, 9, 30, 20), MANUAL)  # an older mission arriving late: the old tour

    first, second = Tour.objects.order_by("started_at")
    assert second == new
    assert (first.ended_at, second.ended_at, second.title) == (at(2026, 10, 1, 12), None, "Tour 2")
    assert tour_of("2026-10-02_20-00-00") == second
    assert tour_of("2026-09-30_20-00-00") == first


def test_manual_tour_title_is_not_repeated_after_a_tour_is_deleted() -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 19, 20), MANUAL)
    start_manual_tour(at(2026, 10, 1, 12))  # Tour 2 (no missions, so it can be deleted)
    start_manual_tour(at(2026, 10, 3, 12))  # Tour 3
    Tour.objects.get(title="Tour 2").delete()

    fourth = start_manual_tour(at(2026, 10, 5, 12))

    assert fourth.title == "Tour 4"  # a count-based number would give "Tour 3" again


def test_manual_mission_older_than_every_tour_goes_to_the_first() -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 19, 20), MANUAL)
    put(mission((sortie(0, 1),)), at(2026, 9, 1, 20), MANUAL)

    assert Tour.objects.count() == 1


def test_start_manual_tour_refuses_a_moment_before_the_open_tours_start() -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 19, 20), MANUAL)

    with pytest.raises(ValueError, match="started at or after"):
        start_manual_tour(at(2026, 9, 19, 20))


# --- per-tour level 2 ---


def sums_by(model: type[PlayerMission] | type[PlayerTour]) -> dict[tuple[int, int | None], dict[str, float]]:
    """Counter sums per (player, tour), straight from `PlayerMission` or `PlayerTour` rows."""
    out: dict[tuple[int, int | None], dict[str, float]] = defaultdict(lambda: dict.fromkeys(COUNTER_FIELDS, 0.0))
    for row in model.objects.all():
        tour_id = row.mission.tour_id if isinstance(row, PlayerMission) else row.tour_id
        for field in COUNTER_FIELDS:
            out[(row.player_id, tour_id)][field] += getattr(row, field)
    return out


def assert_tour_rows_consistent() -> None:
    """Per-tour counters are exactly the sum of the tour's `PlayerMission` rows; per-aircraft rows sum to the same."""
    expected = sums_by(PlayerMission)
    actual = sums_by(PlayerTour)
    assert actual.keys() == expected.keys()
    for key, want in expected.items():
        assert actual[key] == pytest.approx(want), key
    aircraft: dict[tuple[int, int], dict[str, float]] = defaultdict(lambda: dict.fromkeys(COUNTER_FIELDS, 0.0))
    for row in PlayerTourAircraft.objects.all():
        for field in COUNTER_FIELDS:
            aircraft[(row.player_id, row.tour_id)][field] += getattr(row, field)
    assert {k: pytest.approx(v) for k, v in aircraft.items()} == {k: v for k, v in actual.items() if k[1] is not None}
    # and the tours add up to the all-time totals
    for player in Player.objects.all():
        for field in COUNTER_FIELDS:
            total = sum(v[field] for (pid, _), v in actual.items() if pid == player.pk)
            assert getattr(player, field) == pytest.approx(total), (player.pk, field)


def three_tour_history(rules: TourRules = MONTHLY) -> None:
    put(
        mission((sortie(0, 1, kills_air=2), sortie(1, 2, aircraft_type="F-86A-5", coalition=2, is_death=True))),
        at(2026, 8, 30, 20),
        rules,
    )
    put(
        mission(
            (
                sortie(0, 1, kills_ground=3, flight_time_s=777.7),
                sortie(1, 1, aircraft_type="IL-10", assists=1),
                sortie(2, 3, aircraft_type="Turret_IL10", role="gunner"),
            )
        ),
        at(2026, 9, 2, 20),
        rules,
    )
    put(
        mission((sortie(0, 2, aircraft_type="F-51D", coalition=2, kills_air=1), sortie(1, 1))),
        at(2026, 10, 3, 20),
        rules,
    )
    put(mission((sortie(0, 4, flight_time_s=0.02),)), at(2026, 10, 4, 20), rules)


def test_player_tour_is_the_sum_of_the_tours_player_missions() -> None:
    three_tour_history()

    assert_tour_rows_consistent()
    p1 = Player.objects.get(account_uuid=account(1))
    rows = {r.tour.title: r for r in PlayerTour.objects.filter(player=p1).select_related("tour")}
    assert set(rows) == {"August 2026", "September 2026", "October 2026"}
    assert (rows["August 2026"].kills_air, rows["September 2026"].kills_ground, rows["October 2026"].sorties) == (
        2,
        3,
        1,
    )
    assert rows["September 2026"].sorties == 2
    assert p1.sorties == 4  # all-time stays the sum
    assert not PlayerTour.objects.filter(player__account_uuid=account(3)).exists()  # a gunner-only player has no rows


def test_player_tour_aircraft_rows_group_by_tour_and_aircraft() -> None:
    three_tour_history()

    p1 = Player.objects.get(account_uuid=account(1))
    sept = Tour.objects.get(title="September 2026")
    by_aircraft = {r.aircraft.log_name: r.sorties for r in PlayerTourAircraft.objects.filter(player=p1, tour=sept)}
    assert by_aircraft == {"MiG-15bis": 1, "IL-10": 1}
    assert PlayerAircraft.objects.filter(player=p1).count() == 2  # all-time rows are not touched by tours


def tour_state() -> dict[str, list[dict[str, object]]]:
    def norm(row: dict[str, object]) -> dict[str, object]:
        return {k: pytest.approx(v) if k in FLOAT_COUNTERS else v for k, v in row.items() if k != "id"}

    return {
        "tours": [norm(r) for r in Tour.objects.order_by("started_at").values()],
        "player_tours": [norm(r) for r in PlayerTour.objects.order_by("player_id", "tour_id").values()],
        "aircraft": [
            norm(r) for r in PlayerTourAircraft.objects.order_by("player_id", "tour_id", "aircraft_id").values()
        ],
        "missions": list(Mission.objects.order_by("mission_uid").values_list("mission_uid", "tour_id")),
        "aircraft_stats": [norm(r) for r in TourAircraftStats.objects.order_by("tour_id", "aircraft_id").values()],
    }


def by_title() -> dict[tuple[int, str], dict[str, object]]:
    """PlayerTour rows keyed by (player, tour title): comparable across tours that were deleted and recreated."""
    return {
        (row.player_id, row.tour.title): {f: pytest.approx(getattr(row, f)) for f in COUNTER_FIELDS}
        for row in PlayerTour.objects.select_related("tour")
    }


def aircraft_by_title() -> dict[tuple[str, str], dict[str, object]]:
    """TourAircraftStats rows keyed by (aircraft log name, tour title), with pilots and side."""
    return {
        (row.aircraft.log_name, row.tour.title): {
            **{f: pytest.approx(getattr(row, f)) for f in COUNTER_FIELDS},
            "pilots": row.pilots,
            "side": row.side,
        }
        for row in TourAircraftStats.objects.select_related("tour", "aircraft")
    }


def test_incremental_equals_rebuild_with_reingests_and_out_of_order_missions() -> None:
    three_tour_history()
    # An older mission arrives last, and a re-ingest drops a player and changes another's numbers.
    put(mission((sortie(0, 1, kills_air=5), sortie(1, 4))), at(2026, 9, 1, 8))
    put(mission((sortie(0, 1, kills_ground=1),)), at(2026, 9, 2, 20))  # the September mission again, fewer sorties
    incremental = tour_state()
    assert_tour_rows_consistent()

    rebuild_aggregates(DEFAULT_RULES, MONTHLY)

    assert tour_state() == incremental


def test_a_reingest_that_moves_a_mission_to_another_tour_recomputes_both_tours() -> None:
    uid = "2026-09-30_23-00-00"
    put(mission((sortie(0, 1, kills_air=2),)), at(2026, 9, 30, 23), MONTHLY, uid=uid)
    p1 = Player.objects.get(account_uuid=account(1))
    assert PlayerTour.objects.get(player=p1).tour.title == "September 2026"

    # Same mission, now resolved to a start time in October (e.g. a corrected start time on reprocess).
    put(mission((sortie(0, 1, kills_air=2),)), at(2026, 10, 1, 1), MONTHLY, uid=uid)

    rows = {r.tour.title: r.kills_air for r in PlayerTour.objects.filter(player=p1).select_related("tour")}
    assert rows == {"October 2026": 2}  # the September row is gone, not stale
    assert {t: n for (_, t), n in ((k, v["sorties"]) for k, v in aircraft_by_title().items())} == {"October 2026": 1}
    assert_tour_rows_consistent()


def test_a_player_dropped_by_a_reingest_loses_the_tour_row() -> None:
    first = mission((sortie(0, 1), sortie(1, 2)))
    put(first, at(2026, 9, 5, 20))
    assert PlayerTour.objects.filter(player__account_uuid=account(2)).exists()

    put(mission((sortie(0, 1),)), at(2026, 9, 5, 20))

    assert not PlayerTour.objects.filter(player__account_uuid=account(2)).exists()
    assert_tour_rows_consistent()


def test_recompute_limited_to_tours_touches_only_those_tours() -> None:
    three_tour_history()
    sept = Tour.objects.get(title="September 2026")
    PlayerTour.objects.update(kills_air=99)
    p1 = Player.objects.get(account_uuid=account(1))

    recompute_players([p1.pk], [sept.pk])

    assert PlayerTour.objects.get(player=p1, tour=sept).kills_air == 0
    assert PlayerTour.objects.get(player=p1, tour__title="October 2026").kills_air == 99


def test_rebuild_repairs_drifted_tour_rows_and_keeps_pks() -> None:
    three_tour_history()
    good = tour_state()
    pks = sorted(PlayerTour.objects.values_list("pk", flat=True))
    PlayerTour.objects.update(kills_air=42)
    PlayerTourAircraft.objects.filter(pk=PlayerTourAircraft.objects.values_list("pk", flat=True)[0]).delete()
    PlayerTour.objects.filter(pk=pks[0]).delete()

    rebuild_aggregates(DEFAULT_RULES, MONTHLY)

    assert tour_state() == good
    assert set(pks[1:]) <= set(PlayerTour.objects.values_list("pk", flat=True))  # existing rows keep their PKs


def test_per_tour_counters_registry_matches_the_counters_base() -> None:
    names = [f.name for f in Counters._meta.get_fields()]
    for model in (PlayerTour, PlayerTourAircraft, TourAircraftStats):
        assert {n for n in names} <= {f.name for f in model._meta.get_fields()}


def test_elo_stays_all_time() -> None:
    """Elo is a global replay (OQ-28): there are no per-tour rating fields."""
    assert not [f.name for f in PlayerTour._meta.get_fields() if f.name.startswith("elo")]


# --- legacy missions, retour, doctor ---


def test_assign_missing_gives_legacy_missions_a_tour_on_rebuild() -> None:
    three_tour_history()
    Mission.objects.update(tour=None)
    Tour.objects.all().delete()
    assert PlayerTour.objects.count() == 0
    assert tour_problems(MONTHLY).missions_without_tour == 4

    rebuild_aggregates(DEFAULT_RULES, MONTHLY)

    assert titles() == ["August 2026", "September 2026", "October 2026"]
    assert not Mission.objects.filter(tour__isnull=True).exists()
    assert_tour_rows_consistent()
    assert not tour_problems(MONTHLY).needs_retour
    assert assign_missing(MONTHLY) == 0


def test_retour_after_a_mode_change_matches_ingesting_under_the_new_mode() -> None:
    three_tour_history(MONTHLY)
    biweekly = TourRules(mode="days", days=14, start=date(2026, 8, 30), timezone_name="UTC")
    assert tour_problems(biweekly).stale_tours == 3

    rebuild_aggregates(DEFAULT_RULES, biweekly, reassign_tours=True)
    retoured = by_title()
    retoured_aircraft = aircraft_by_title()
    # 14-day blocks from 30 August: the September missions are in block 1, the October ones in block 3
    assert titles() == ["Tour 1", "Tour 3"]
    assert not tour_problems(biweekly).needs_retour
    assert_tour_rows_consistent()

    # A fresh database fed under the new mode ends up the same.
    for model in (TourAircraftStats, PlayerTourAircraft, PlayerTour, PlayerMission):
        model.objects.all().delete()
    Mission.objects.all().delete()
    Tour.objects.all().delete()
    three_tour_history(biweekly)
    assert by_title() == retoured
    assert aircraft_by_title() == retoured_aircraft


def test_retour_keeps_unchanged_tours_with_their_renamed_titles() -> None:
    three_tour_history(MONTHLY)
    Tour.objects.filter(title="September 2026").update(title="The Sabre Summer")
    pk = Tour.objects.get(title="The Sabre Summer").pk

    with transaction.atomic():
        summary = retour(MONTHLY)

    assert (summary.missions, summary.tours) == (4, 3)
    assert Tour.objects.get(title="The Sabre Summer").pk == pk


def test_retour_drops_tours_left_without_missions() -> None:
    three_tour_history(MONTHLY)
    yearly_ish = TourRules(mode="days", days=365, start=date(2026, 1, 1), timezone_name="UTC")

    rebuild_aggregates(DEFAULT_RULES, yearly_ish, reassign_tours=True)

    assert titles() == ["Tour 1"]
    assert PlayerTour.objects.count() == Player.objects.filter(sorties__gt=0).count()
    assert_tour_rows_consistent()


def test_retour_to_manual_keeps_the_boundaries_and_opens_the_newest() -> None:
    three_tour_history(MONTHLY)

    rebuild_aggregates(DEFAULT_RULES, MANUAL, reassign_tours=True)

    august, september, october = Tour.objects.order_by("started_at")
    assert (august.ended_at, september.ended_at, october.ended_at) == (september.started_at, october.started_at, None)
    assert {t.mode for t in Tour.objects.all()} == {"manual"}
    assert Mission.objects.filter(tour=october).count() == 2
    assert not tour_problems(MANUAL).needs_retour
    assert_tour_rows_consistent()


def test_retour_to_manual_without_tours_makes_one_open_tour() -> None:
    three_tour_history(MONTHLY)
    Mission.objects.update(tour=None)
    Tour.objects.all().delete()

    rebuild_aggregates(DEFAULT_RULES, MANUAL, reassign_tours=True)

    tour = Tour.objects.get()
    assert (tour.started_at, tour.ended_at) == (at(2026, 8, 30, 20), None)
    assert Mission.objects.filter(tour=tour).count() == 4


def test_retour_in_an_empty_database_does_nothing() -> None:
    assert retour(MONTHLY).tours == 0
    assert retour(MANUAL).tours == 0


def test_retour_after_a_timezone_change_moves_the_boundary_missions() -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 30, 15, 30), MONTHLY)
    assert titles() == ["September 2026"]
    assert not tour_problems(MONTHLY).needs_retour
    assert tour_problems(SEOUL_MONTHLY).needs_retour  # Seoul puts it in October

    rebuild_aggregates(DEFAULT_RULES, SEOUL_MONTHLY, reassign_tours=True)

    assert titles() == ["October 2026"]
    assert_tour_rows_consistent()


def test_rebuild_bumps_the_data_version_when_tours_change() -> None:
    three_tour_history()
    before = current_data_version()

    rebuild_aggregates(DEFAULT_RULES, MONTHLY, reassign_tours=True)

    assert current_data_version() > before


# --- queries and the selector ---


def test_query_helpers() -> None:
    three_tour_history()
    october = Tour.objects.get(title="October 2026")
    p1 = Player.objects.get(account_uuid=account(1))

    assert current_tour(at(2026, 10, 10)) == october
    in_september = current_tour(at(2026, 9, 10))
    assert in_september is not None
    assert in_september.title == "September 2026"
    assert current_tour(at(2020, 1, 1)) is None
    row = player_tour(p1.pk, october)
    assert row is not None
    assert row.sorties == 1
    assert player_tour(Player.objects.get(account_uuid=account(3)).pk, october) is None
    assert [r.aircraft.log_name for r in player_tour_aircraft(p1.pk, october)] == ["MiG-15bis"]
    assert {r.player.account_uuid for r in tour_leaderboard(october)} == {account(1), account(2), account(4)}
    Player.objects.filter(account_uuid=account(4)).update(is_hidden=True)
    assert {r.player.account_uuid for r in tour_leaderboard(october)} == {account(1), account(2)}


def test_tour_choice_reads_the_query_parameter_forgivingly() -> None:
    three_tour_history()
    september = Tour.objects.get(title="September 2026")

    assert tour_choice(str(september.pk)).selected == september
    assert [t.title for t in tour_choice(None).tours] == ["October 2026", "September 2026", "August 2026"]
    october = Tour.objects.get(title="October 2026")
    for raw in (None, "", "abc", "-1", "999999", "1.5", "9" * 5000, "٣" * 40):
        assert tour_choice(raw).selected == october  # the current tour, never an error (a 5000-digit id included)
    assert tour_choice("all").selected is None  # the explicit all-time view
    assert tour_choice(None).current == october
    assert tour_options(tour_choice(None).tours)[0] == (Tour.objects.get(title="October 2026").pk, "October 2026")


def render_selector(url: str) -> str:
    request = RequestFactory().get(url)
    choice = tour_choice(request.GET.get("tour"))
    template = Template("{% load il2ks %}{% tour_select tours tour %}")
    return template.render(Context({"request": request, "tours": choice.tours, "tour": choice.selected}))


def test_tour_select_renders_all_time_and_marks_the_chosen_tour() -> None:
    three_tour_history()
    september = Tour.objects.get(title="September 2026")

    html = render_selector(f"/players/?tour={september.pk}&q=bob&sort=-kills&page=3")

    assert 'name="tour"' in html
    assert ">All time</option>" in html
    assert 'value="all"' in html
    assert "tour-toggle" not in html  # OQ-78: no segmented toggle, only the dropdown
    assert f'<option value="{september.pk}" selected>September 2026</option>' in html
    assert 'name="q" value="bob"' in html
    assert 'name="sort" value="-kills"' in html
    assert 'name="page"' not in html  # a new tour starts at page 1
    assert 'action="/players/"' in html


def test_tour_select_drops_every_page_parameter() -> None:
    """A tour switch must not carry `page_best` / `page_running` / `page_*` into the new tour (page 1 of each list)."""
    three_tour_history()

    html = render_selector("/players/?q=bob&page=2&page_best=3&page_running=4&page_missions=5&pager=x")

    assert 'name="q" value="bob"' in html
    assert 'name="pager" value="x"' in html  # only `page` and `page_*` are pagination
    for key in ("page", "page_best", "page_running", "page_missions"):
        assert f'name="{key}"' not in html


def test_tour_select_without_a_tour_selects_the_current_one_and_renders_nothing_without_tours() -> None:
    assert render_selector("/players/").strip() == ""
    three_tour_history()

    html = render_selector("/players/")
    assert html.count(" selected") == 1  # no parameter = the current tour
    assert '<option value="all">' in html
    assert '<option value="" selected>Current tour</option>' in html
    assert "tour-toggle" not in html
    all_time = render_selector("/players/?tour=all")
    assert '<option value="all" selected>All time</option>' in all_time
    assert all_time.count(" selected") == 1


def test_tour_select_lists_all_time_then_current_tour_then_the_tours_newest_first() -> None:
    """OQ-78: the dropdown alone; "Current tour" is the empty value (no parameter), "All time" is `all`."""
    three_tour_history()
    html = render_selector("/players/")
    options = re.findall(r"<option value=\"([^\"]*)\"[^>]*>([^<]*)</option>", html)
    ids = [tour.pk for tour in tour_choice(None).tours]
    assert len(ids) == 3
    assert options == [
        ("all", "All time"),
        ("", "Current tour"),
        (str(ids[0]), "October 2026"),
        (str(ids[1]), "September 2026"),
        (str(ids[2]), "August 2026"),
    ]
    chosen = render_selector(f"/players/?tour={ids[0]}")  # the current tour by id is still "Current tour"
    assert chosen.count(" selected") == 1
    assert '<option value="" selected>' in chosen
    older = render_selector(f"/players/?tour={ids[1]}")
    assert f'<option value="{ids[1]}" selected>' in older


# --- admin (FR-ADM-8) ---


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def test_tours_can_be_renamed_but_nothing_else_changes(admin: Client) -> None:
    three_tour_history()
    tour = Tour.objects.get(title="September 2026")
    before = current_data_version()

    page = admin.get(f"/admin/il2ks_db/tour/{tour.pk}/change/")
    assert page.status_code == 200
    response = admin.post(
        f"/admin/il2ks_db/tour/{tour.pk}/change/",
        {"title": "Sabre Summer", "started_at_0": "2000-01-01", "started_at_1": "00:00:00", "mode": "manual"},
    )

    assert response.status_code == 302
    tour.refresh_from_db()
    assert tour.title == "Sabre Summer"
    assert (tour.started_at, tour.mode) == (at(2026, 9, 1), "monthly")
    assert current_data_version() > before


def test_tours_cannot_be_added_or_deleted_in_the_admin(admin: Client) -> None:
    three_tour_history()
    tour = Tour.objects.first()
    assert tour is not None

    assert admin.get("/admin/il2ks_db/tour/add/").status_code == 403
    assert admin.get(f"/admin/il2ks_db/tour/{tour.pk}/delete/").status_code == 403


def test_start_a_new_tour_button_only_in_manual_mode(admin: Client, settings: Settings) -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 19, 20), MANUAL)
    settings.IL2KS_TOUR_MODE = "monthly"
    assert "Start a new tour now" not in admin.get("/admin/il2ks_db/tour/").content.decode()
    settings.IL2KS_TOUR_MODE = "manual"
    assert "Start a new tour now" in admin.get("/admin/il2ks_db/tour/").content.decode()


def test_start_a_new_tour_action_closes_the_current_tour(admin: Client, settings: Settings) -> None:
    settings.IL2KS_TOUR_MODE = "manual"
    old = put(mission((sortie(0, 1),)), at(2026, 9, 19, 20), MANUAL).tour
    assert old is not None
    before = current_data_version()

    response = admin.post("/admin/il2ks_db/tour/start/")

    assert response.status_code == 302
    old.refresh_from_db()
    assert old.ended_at is not None
    new = Tour.objects.get(ended_at__isnull=True)
    assert new.title == "Tour 2"
    assert new.started_at == old.ended_at
    assert current_data_version() > before
    # Missions ingested from now on go to the new tour (the mission started after the click).
    later = put(mission((sortie(0, 1),)), datetime.now(UTC) + timedelta(minutes=1), MANUAL, uid="2099-01-01_00-00-00")
    assert later.tour == new


def test_start_a_new_tour_is_refused_outside_manual_mode_and_for_get(admin: Client, settings: Settings) -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 19, 20), MONTHLY)
    settings.IL2KS_TOUR_MODE = "monthly"

    assert admin.post("/admin/il2ks_db/tour/start/", follow=True).status_code == 200
    assert Tour.objects.count() == 1
    settings.IL2KS_TOUR_MODE = "manual"
    assert admin.get("/admin/il2ks_db/tour/start/").status_code == 403
    assert Tour.objects.count() == 1


def test_start_a_new_tour_needs_the_change_permission(client: Client, settings: Settings) -> None:
    settings.IL2KS_TOUR_MODE = "manual"
    client.force_login(User.objects.create_user("clerk", password="x", is_staff=True))

    assert client.post("/admin/il2ks_db/tour/start/").status_code == 403


def test_player_tour_admin_is_read_only(admin: Client) -> None:
    three_tour_history()
    row = PlayerTour.objects.first()
    assert row is not None

    assert admin.get("/admin/il2ks_db/playertour/").status_code == 200
    assert admin.get(f"/admin/il2ks_db/playertour/{row.pk}/change/").status_code == 200  # view only
    assert admin.post(f"/admin/il2ks_db/playertour/{row.pk}/change/", {"kills_air": 99}).status_code == 403
    assert admin.get("/admin/il2ks_db/playertour/add/").status_code == 403
    assert admin.get(f"/admin/il2ks_db/playertour/{row.pk}/delete/").status_code == 403
    row.refresh_from_db()
    assert row.kills_air != 99


# --- il2ks doctor ---


def test_doctor_reports_tours_that_do_not_match_the_settings(tmp_path: Path) -> None:
    put(mission((sortie(0, 1),)), at(2026, 9, 30, 15, 30), MONTHLY)
    cfg = make_instance(tmp_path)  # monthly, UTC: matches
    assert [f.level for f in tours_check(cfg)] == [Level.OK]

    seoul = make_instance(tmp_path / "other", extra_toml='[tours]\ntimezone = "Asia/Seoul"\n')
    findings = list(tours_check(seoul))
    assert [f.level for f in findings] == [Level.WARN]
    assert "1 tour(s) made under other settings" in findings[0].detail
    assert "rebuild-aggregates --retour" in findings[0].fix

    Mission.objects.update(tour=None)
    assert "1 mission(s) without a tour" in next(iter(tours_check(cfg))).detail


def test_doctor_tours_check_is_quiet_without_a_database(tmp_path: Path) -> None:
    assert list(tours_check(make_instance(tmp_path, with_db=False))) == []


# --- upgrade path ---


def test_migrating_a_database_from_before_tours_assigns_the_missions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the schema update, existing missions get their tours without the admin running anything."""
    three_tour_history()
    Mission.objects.update(tour=None)
    Tour.objects.all().delete()
    monkeypatch.setattr(MigrationExecutor, "migration_plan", returning([("fake", False)]))
    monkeypatch.setattr(management, "call_command", recording([], "migrate"))

    migrate_if_needed(make_instance(tmp_path), "ingest", wait=None)

    assert titles() == ["August 2026", "September 2026", "October 2026"]
    assert_tour_rows_consistent()


def test_is_quiet_tour_ignores_paging_and_view_parameters_but_not_filters() -> None:
    from il2ks.db.models import Tour
    from il2ks.queries.tours import is_quiet_tour

    tour = Tour(title="October 2026")

    assert is_quiet_tour(tour, {"tour": "1", "page": "2", "page_missions": "3", "sort": "kills", "cols": "a"}, 0)
    assert not is_quiet_tour(tour, {"tour": "1", "aircraft": "5"}, 0)
    assert not is_quiet_tour(tour, {"tour": "1"}, 4)
    assert not is_quiet_tour(None, {}, 0)


# --- aircraft stats per tour (FR-WEB-8, TD-26) ---


def test_aircraft_stats_per_tour_sum_the_tours_pilots_and_sides() -> None:
    three_tour_history()

    rows = aircraft_by_title()

    assert set(rows) == {
        ("MiG-15bis", "August 2026"),
        ("F-86A-5", "August 2026"),
        ("MiG-15bis", "September 2026"),
        ("IL-10", "September 2026"),
        ("F-51D", "October 2026"),
        ("MiG-15bis", "October 2026"),
    }  # the gunner sortie is no row
    mig_october = TourAircraftStats.objects.get(aircraft__log_name="MiG-15bis", tour__title="October 2026")
    assert (mig_october.sorties, mig_october.pilots, mig_october.side) == (2, 2, "redfor")  # players 1 and 4
    sabre = TourAircraftStats.objects.get(aircraft__log_name="F-86A-5", tour__title="August 2026")
    assert (sabre.pilots, sabre.deaths, sabre.side) == (1, 1, "blufor")
    # the all-time row is the sum of its tours
    all_time = {s.aircraft.log_name: s.sorties for s in AircraftStats.objects.select_related("aircraft")}
    per_tour: dict[str, int] = defaultdict(int)
    for stat in TourAircraftStats.objects.select_related("aircraft"):
        per_tour[stat.aircraft.log_name] += stat.sorties
    assert dict(per_tour) == all_time


def test_aircraft_stats_per_tour_count_hidden_players_and_missions() -> None:
    """FR-ADM-3: hiding is presentation only, as for the all-time row."""
    three_tour_history()
    before = aircraft_by_title()
    Player.objects.filter(account_uuid=account(1)).update(is_hidden=True)
    Mission.objects.filter(tour__title="September 2026").update(is_hidden=True)

    rebuild_aggregates(DEFAULT_RULES, MONTHLY)

    assert aircraft_by_title() == before


def test_aircraft_stats_per_tour_incremental_touches_only_the_given_tours() -> None:
    three_tour_history()
    sept = Tour.objects.get(title="September 2026")
    TourAircraftStats.objects.update(sorties=99)
    mig = list(AircraftStats.objects.values_list("aircraft_id", flat=True))

    recompute_aircraft_stats(mig, [sept.pk])

    assert TourAircraftStats.objects.get(aircraft__log_name="MiG-15bis", tour=sept).sorties == 1
    assert TourAircraftStats.objects.get(aircraft__log_name="MiG-15bis", tour__title="October 2026").sorties == 99


def test_rebuild_repairs_drifted_aircraft_stats_per_tour() -> None:
    three_tour_history()
    good = tour_state()
    TourAircraftStats.objects.update(kills_air=42, pilots=7)
    TourAircraftStats.objects.filter(pk=TourAircraftStats.objects.values_list("pk", flat=True)[0]).delete()
    sabre = GameObject.objects.get(log_name="F-86A-5")  # no sortie in October: a stale row must go
    TourAircraftStats.objects.create(aircraft=sabre, tour=Tour.objects.get(title="October 2026"), sorties=3)

    rebuild_aggregates(DEFAULT_RULES, MONTHLY)

    assert tour_state() == good
