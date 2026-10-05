"""Regressions from the Opus review #11 around tours (TD-26): a discarded live mission, what a decisive-tour save
touches, Old Hand after a tour was inserted in the past, titles after an admin's rename, old winners without a result.

Each one compares the incremental state with a rebuild (`rebuild_aggregates(reassign_tours=True)`)."""

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.db import transaction
from django.test import Client

from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import Mission, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.persist import discard_provisional_mission, save_level1, save_mission
from tests.db_canon import canonical_dump, diff_dumps
from tests.factories import FakeCatalog, kill, meta, mission, sortie
from tests.integration.test_achievements import held, pk, snapshot
from tests.integration.test_achievements_all_time import fly, month
from tests.integration.test_tours import MONTHLY
from tests.integration.test_tours_decisive import END_TICK, A, B, C, D, at, history, put, set_on_win, tour_titles

pytestmark = pytest.mark.django_db

STARTS = [at(2026, 10, 1, 8) + timedelta(hours=7 * i) for i in range(10)]
RESULTS = [1, None, 2, None, None, 1, None, 2, None, None]


def result(i: int, winner: int | None) -> MissionResult:
    p = (i % 3) + 1
    q = ((i + 1) % 3) + 1
    sorties = (
        sortie(0, p, kills_air=1, kills_air_pvp=1, combat_role="air_superiority"),
        sortie(
            1, q, aircraft_type="F-86A-5", coalition=2, is_death=True, outcome="crashed", combat_role="air_superiority"
        ),
    )
    return mission(sorties, (kill(500, 0, 1),), end_tick=6000, winner=winner)


def save_final(i: int, started: datetime | None = None) -> Mission:
    started = started or STARTS[i]
    uid = started.strftime("%Y-%m-%d_%H-%M-%S")
    with transaction.atomic():
        return save_mission(result(i, RESULTS[i]), meta(uid, started), FakeCatalog(), DEFAULT_RULES, MONTHLY)


def save_live(i: int, started: datetime) -> Mission:
    live_meta = replace(meta("live-1", started), live=True)
    with transaction.atomic():
        return save_mission(result(i, None), live_meta, FakeCatalog(), None, MONTHLY)


def assert_equals_rebuild() -> None:
    incremental = canonical_dump()
    with transaction.atomic():
        rebuild_aggregates(DEFAULT_RULES, MONTHLY, reassign_tours=True)
    diff = diff_dumps(incremental, canonical_dump())
    assert not diff, "\n".join(diff[:20])


def assert_no_empty_tour() -> None:
    assert not Tour.objects.filter(missions__isnull=True).exists()


# --- 1. a discarded live mission leaves no empty tour behind ---
def test_discarded_live_mission_in_a_decisive_part_leaves_no_empty_tour() -> None:
    set_on_win(wanted=True)
    for i in range(3):
        save_final(i)
    live = save_live(3, STARTS[3])  # after the last decisive mission (index 2): opens a new part
    assert Tour.objects.count() == 3
    with transaction.atomic():
        discard_provisional_mission(Mission.objects.get(pk=live.pk))

    assert_no_empty_tour()
    newest = Tour.objects.order_by("-started_at").first()
    assert newest is not None
    assert Mission.objects.filter(tour=newest).exists()  # the default tour of the site has missions
    assert_equals_rebuild()


def test_discarded_live_mission_that_opened_a_month_leaves_no_empty_tour() -> None:
    save_final(0, at(2026, 9, 30, 20))
    live = save_live(1, at(2026, 10, 1, 8))
    assert tour_titles() == ["September 2026", "October 2026"]
    with transaction.atomic():
        discard_provisional_mission(Mission.objects.get(pk=live.pk))

    assert tour_titles() == ["September 2026"]
    assert_equals_rebuild()  # the current streaks are those of September again


# --- 2. a save touches its own tour only ---
def test_a_save_in_a_later_part_touches_only_that_part() -> None:
    set_on_win(wanted=True)
    history()
    parts = {t.title: t.pk for t in Tour.objects.all()}
    assert set(parts) == {"October 2026", "October 2026 (2)", "October 2026 (3)"}
    late = A.replace(day=4)
    with transaction.atomic():
        saved, touched = save_level1(
            mission((sortie(0, 7),), end_tick=END_TICK), meta("late", late), FakeCatalog(), MONTHLY
        )

    assert Mission.objects.get(pk=saved.pk).tour_id == parts["October 2026 (3)"]
    assert touched == {parts["October 2026 (3)"]}


def test_a_new_part_touches_only_the_new_part() -> None:
    set_on_win(wanted=True)
    put(A, winner=1)
    base = Tour.objects.get().pk
    with transaction.atomic():
        saved, touched = save_level1(
            mission((sortie(0, 7),), end_tick=END_TICK), meta("next", B), FakeCatalog(), MONTHLY
        )

    new = Mission.objects.get(pk=saved.pk).tour_id
    assert new != base
    assert touched == {new}


# --- 3. Old Hand after a tour was inserted in the past ---
def test_tour_inserted_between_breaks_the_old_hand_run_like_a_rebuild() -> None:
    fly(0)
    fly(2)  # tours 0 and 2 exist only: consecutive in the tours table
    assert held(1).get("tours_in_a_row") == 1
    fly(1, player=2)  # a late import creates tour 1 in between, player 1 did not fly in it
    snap = snapshot()
    with transaction.atomic():
        rebuild_aggregates()

    assert snapshot() == snap


def test_a_discarded_tour_in_between_restores_the_old_hand_run_like_a_rebuild() -> None:
    fly(0)
    fly(2)
    live = save_live(1, month(1))  # a live mission opens the tour between them: player 1's run is broken
    assert held(1).get("tours_in_a_row") is None
    with transaction.atomic():
        discard_provisional_mission(Mission.objects.get(pk=live.pk))

    assert held(1).get("tours_in_a_row") == 1
    snap = snapshot()
    with transaction.atomic():
        rebuild_aggregates()
    assert snapshot() == snap


# --- 4. titles after an admin's rename ---
def test_parts_are_titled_from_the_renamed_base_incrementally_and_by_retour() -> None:
    set_on_win(wanted=True)
    put(A, winner=1)
    put(B, draw=True)
    Tour.objects.filter(title="October 2026").update(title="Season opener")  # the admin's rename
    put(C, winner=2)
    put(D)
    incremental = tour_titles()
    assert incremental[-1] == "Season opener (3)"
    assert_equals_rebuild()
    assert tour_titles() == incremental


# --- shuffled orders stay equal to a rebuild ---
def test_shuffled_on_win_ingest_equals_rebuild() -> None:
    set_on_win(wanted=True)
    order = list(range(len(STARTS)))
    random.Random(7).shuffle(order)
    for i in order:
        save_final(i)
    assert_equals_rebuild()


# --- 5. old winners: rows saved before results were read ---
def old_winner(started_at: datetime) -> None:
    """A mission as an old version saved it: a winner (possibly a false one), no result."""
    put(started_at, winner=1)
    Mission.objects.filter(started_at=started_at).update(result="unknown")


def test_an_old_winner_without_a_result_is_not_a_cut() -> None:
    set_on_win(wanted=True)
    old_winner(A)
    put(B)

    assert tour_titles() == ["October 2026"]
    assert_equals_rebuild()


def test_the_tours_page_counts_old_winners_apart(admin: Client) -> None:
    old_winner(A)
    put(B, winner=2)
    put(C, draw=True)
    page = admin.get("/admin/tours/").content.decode()

    assert "Won by one side: 1" in page
    assert "Old winners without a result: 1" in page
    assert "il2ks reprocess" in page.split("Old winners without a result: 1", 1)[1].split("</li>", 1)[0]
    assert "No result: 0" in page


# --- 6. what the option would do ---
def test_the_projection_counts_the_tours_the_option_would_create() -> None:
    history()  # the option off: one tour; wins at A and C, so B and D start parts

    from il2ks.ingest.tours import on_win_projection

    projection = on_win_projection(now=D + timedelta(days=1))

    assert projection.tours == 3
    assert projection.recent_wins == 2
    assert on_win_projection(now=D + timedelta(days=40)).recent_wins == 0


def test_the_tours_page_shows_the_projection(admin: Client) -> None:
    now = datetime.now(UTC)
    put(now - timedelta(minutes=50), winner=1)
    put(now - timedelta(minutes=20))
    page = admin.get("/admin/tours/").content.decode()

    assert "would form this many tours:" in page
    assert "Missions won by one side in the last 30 days: 1" in page


# --- 7. the absence notice names every tour the pilot flew in ---
def test_the_absence_notice_lists_all_the_tours_the_pilot_flew_in(client: Client) -> None:
    for n in range(14):  # more tours than the profile charts show
        fly(n)
    fly(14, player=2)  # the pilot did not fly in the newest tour
    first, newest = Tour.objects.order_by("started_at")[0], Tour.objects.order_by("-started_at")[0]
    page = client.get(f"/players/{pk(1)}/?tour={newest.pk}").content.decode()

    notice = page.split("tour-absent", 1)[1].split("</aside>", 1)[0]
    assert f'?tour={first.pk}"' in notice


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client
