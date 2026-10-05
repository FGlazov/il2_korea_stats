"""A decisive mission starts a new tour (maintainer request 2026-10-05, TD-26): the admin option "Start a new tour
when a mission is won by one side". The option adds boundaries on top of the `[tours]` mode: the next mission after
a mission won by one side starts a new part of the period. Draws and missions without a result change nothing.

The tours are a function of the missions alone, so an incremental ingest in any order, a re-ingest and
`rebuild-aggregates --retour` must all give the same tours."""

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.db import transaction

from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.core.tours import TourRules
from il2ks.db.models import Mission, SiteSettings, Tour
from il2ks.db.site import get_site_settings
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.persist import save_mission
from il2ks.ingest.reprocess import recompute_tours_with_wanted_rule
from il2ks.ingest.tours import on_win_pending, retour, start_manual_tour, tour_problems
from tests.factories import FakeCatalog, meta, mission, sortie
from tests.integration.test_tours import MANUAL, MONTHLY, assert_tour_rows_consistent
from tests.ops_helpers import make_instance

pytestmark = pytest.mark.django_db

END_TICK = 6000  # 120 s of mission


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def set_on_win(*, wanted: bool, applied: bool | None = None) -> None:
    get_site_settings()
    SiteSettings.objects.filter(pk=1).update(
        tour_on_win=wanted, tour_on_win_applied=wanted if applied is None else applied
    )


def put(started_at: datetime, *, winner: int | None = None, draw: bool = False, rules: TourRules = MONTHLY) -> Mission:
    result = mission((sortie(0, 1),), end_tick=END_TICK, winner=winner, draw=draw)
    uid = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    with transaction.atomic():
        return save_mission(result, meta(uid, started_at), FakeCatalog(), DEFAULT_RULES, rules)


def layout() -> list[tuple[str, str, bool, datetime, datetime | None]]:
    """Per mission: (uid, tour title, by_win, tour start, tour end) in time order."""
    out: list[tuple[str, str, bool, datetime, datetime | None]] = []
    for m in Mission.objects.order_by("started_at").select_related("tour"):
        assert m.tour is not None
        out.append((m.mission_uid, m.tour.title, m.tour.by_win, m.tour.started_at, m.tour.ended_at))
    return out


def tour_titles() -> list[str]:
    return [t.title for t in Tour.objects.order_by("started_at")]


A = at(2026, 10, 1, 8)
B = at(2026, 10, 1, 12)
C = at(2026, 10, 2, 8)
D = at(2026, 10, 3, 8)


def history(rules: TourRules = MONTHLY) -> None:
    put(A, winner=1, rules=rules)  # decisive: B starts the next tour
    put(B, draw=True, rules=rules)
    put(C, winner=2, rules=rules)  # decisive: D starts the next tour
    put(D, rules=rules)


def test_off_by_default_a_win_changes_no_tour() -> None:
    history()

    assert tour_titles() == ["October 2026"]
    assert not any(by_win for _, _, by_win, _, _ in layout())


def test_on_the_next_mission_after_a_decisive_one_starts_a_new_tour() -> None:
    set_on_win(wanted=True)

    history()

    assert tour_titles() == ["October 2026", "October 2026 (2)", "October 2026 (3)"]
    first, second, third = Tour.objects.order_by("started_at")
    assert (first.by_win, second.by_win, third.by_win) == (False, True, True)
    # the tours end where the deciding mission ended, and meet without a gap
    a_end = A + timedelta(seconds=END_TICK / 50)
    c_end = C + timedelta(seconds=END_TICK / 50)
    assert (second.started_at, third.started_at) == (a_end, c_end)
    assert (first.ended_at, second.ended_at) == (a_end, c_end)
    assert third.ended_at == at(2026, 11, 1)  # the last part runs to the end of the period
    assert [title for _, title, *_ in layout()] == [
        "October 2026",
        "October 2026 (2)",
        "October 2026 (2)",
        "October 2026 (3)",
    ]
    assert_tour_rows_consistent()
    assert not tour_problems(MONTHLY).needs_retour


def test_a_draw_or_an_unknown_result_starts_nothing() -> None:
    set_on_win(wanted=True)

    put(A, draw=True)
    put(B)
    put(C, draw=True)

    assert tour_titles() == ["October 2026"]


def test_the_period_boundary_still_applies_on_top() -> None:
    set_on_win(wanted=True)

    put(A, winner=1)
    put(at(2026, 11, 2, 8))

    assert tour_titles() == ["October 2026", "November 2026"]  # no empty "(2)" tour for the cut with no mission


def test_a_decisive_mission_late_in_the_period_leaves_no_empty_tour() -> None:
    set_on_win(wanted=True)

    put(A, winner=1)

    assert tour_titles() == ["October 2026"]


def test_a_decisive_mission_ending_in_the_next_period_cuts_that_period() -> None:
    set_on_win(wanted=True)
    late = at(2026, 10, 31, 23, 59)  # 120 s: ends at 00:01 on 1 November

    put(late, winner=2)
    put(at(2026, 11, 1, 3))

    assert [t.title for t in Tour.objects.order_by("started_at")] == ["October 2026", "November 2026 (2)"]
    assert Tour.objects.get(title="November 2026 (2)").by_win


def test_a_late_import_before_a_boundary_lands_in_the_earlier_tour() -> None:
    set_on_win(wanted=True)
    put(A, winner=1)
    put(C, winner=2)
    put(D)

    put(B, draw=True)  # imported late: after A, before C

    assert [(uid, title) for uid, title, *_ in layout()] == [
        ("2026-10-01_08-00-00", "October 2026"),
        ("2026-10-01_12-00-00", "October 2026 (2)"),
        ("2026-10-02_08-00-00", "October 2026 (2)"),
        ("2026-10-03_08-00-00", "October 2026 (3)"),
    ]
    assert_tour_rows_consistent()


def test_a_late_decisive_mission_moves_the_later_missions_to_a_new_tour() -> None:
    set_on_win(wanted=True)
    put(A)
    put(C)
    put(D)
    assert tour_titles() == ["October 2026"]

    put(B, winner=2)  # late, and decisive: C and D now start a new tour

    assert [(uid, title) for uid, title, *_ in layout()] == [
        ("2026-10-01_08-00-00", "October 2026"),
        ("2026-10-01_12-00-00", "October 2026"),
        ("2026-10-02_08-00-00", "October 2026 (2)"),
        ("2026-10-03_08-00-00", "October 2026 (2)"),
    ]
    assert_tour_rows_consistent()


def test_a_re_ingest_that_turns_a_win_into_a_draw_merges_the_tours() -> None:
    set_on_win(wanted=True)
    history()
    assert len(tour_titles()) == 3

    put(A, draw=True)  # the same mission, reprocessed with another result

    assert tour_titles() == ["October 2026", "October 2026 (2)"]
    assert_tour_rows_consistent()


def test_incremental_ingest_in_any_order_equals_a_rebuild() -> None:
    set_on_win(wanted=True)
    starts = [at(2026, 10, 1, 8) + timedelta(hours=7 * i) for i in range(14)]
    results = [1, None, 2, None, None, 1, None, 2, None, None, 1, None, None, 2]
    spec = list(zip(starts, results, strict=True))

    def ingest(order: list[int]) -> None:
        for i in order:
            put(spec[i][0], winner=spec[i][1])

    ingest(list(range(len(spec))))
    in_order = layout()
    assert len({title for _, title, *_ in in_order}) == 6

    for seed in (1, 2, 3):
        Mission.objects.all().delete()
        Tour.objects.all().delete()
        order = list(range(len(spec)))
        random.Random(seed).shuffle(order)
        ingest(order)
        assert layout() == in_order

    with transaction.atomic():
        retour(TourRules(mode="monthly", timezone_name="UTC", on_win=True))
    assert layout() == in_order
    assert_tour_rows_consistent()


def test_manual_mode_the_decisive_mission_adds_parts_and_the_admin_boundary_still_works() -> None:
    set_on_win(wanted=True)
    put(A, winner=1, rules=MANUAL)
    put(B, rules=MANUAL)
    assert tour_titles() == ["Tour 1", "Tour 1 (2)"]

    start_manual_tour(at(2026, 10, 2, 0))  # the admin starts Tour 2 (a boundary of its own)
    put(C, winner=2, rules=MANUAL)
    put(D, rules=MANUAL)

    assert tour_titles() == ["Tour 1", "Tour 1 (2)", "Tour 2", "Tour 2 (2)"]
    by_win = {t.title: t.by_win for t in Tour.objects.all()}
    assert by_win == {"Tour 1": False, "Tour 1 (2)": True, "Tour 2": False, "Tour 2 (2)": True}
    before = layout()
    with transaction.atomic():
        retour(TourRules(mode="manual", timezone_name="UTC", on_win=True))
    assert layout() == before


def test_switching_it_on_and_off_again_needs_a_retour_and_gives_back_the_old_tours() -> None:
    history()
    plain = layout()
    assert tour_titles() == ["October 2026"]

    get_site_settings()
    SiteSettings.objects.filter(pk=1).update(tour_on_win=True)  # the admin ticks the box
    assert on_win_pending()
    assert tour_titles() == ["October 2026"]  # nothing moves before the retour

    with transaction.atomic():
        retour(MONTHLY)
    assert not on_win_pending()
    assert tour_titles() == ["October 2026", "October 2026 (2)", "October 2026 (3)"]  # (level 2: the caller's rebuild)

    SiteSettings.objects.filter(pk=1).update(tour_on_win=False)
    assert on_win_pending()
    with transaction.atomic():
        retour(MONTHLY)
    assert layout() == plain
    assert Tour.objects.filter(by_win=True).count() == 0


def test_a_full_rebuild_with_retour_adopts_the_wanted_option_and_rebuilds_level_two() -> None:
    history()
    SiteSettings.objects.filter(pk=1).update(tour_on_win=True)

    rebuild_aggregates(DEFAULT_RULES, MONTHLY, reassign_tours=True)

    assert tour_titles() == ["October 2026", "October 2026 (2)", "October 2026 (3)"]
    assert_tour_rows_consistent()
    assert not on_win_pending()


def test_watch_applies_a_changed_option_under_the_lock(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    history()
    assert not recompute_tours_with_wanted_rule(cfg)  # nothing pending

    SiteSettings.objects.filter(pk=1).update(tour_on_win=True)
    assert recompute_tours_with_wanted_rule(cfg)

    assert tour_titles() == ["October 2026", "October 2026 (2)", "October 2026 (3)"]
    assert not on_win_pending()
    assert_tour_rows_consistent()
    assert not recompute_tours_with_wanted_rule(cfg)
