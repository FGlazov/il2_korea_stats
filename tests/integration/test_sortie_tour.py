"""`PlayerSortie.tour` is always the mission's tour (doc 06 "PlayerSortie", doc 14 "The sortie's tour"): the paths that
write `Mission.tour` keep it, and the migration backfills it. The big tour tests (`test_tours*.py`) assert it after
each of their operations (`assert_tour_rows_consistent`), every incremental == rebuild comparison does too
(`canonical_dump`)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor

from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.db.models import Mission, PlayerSortie, SiteSettings, Tour
from il2ks.ingest.persist import discard_provisional_mission, save_mission
from il2ks.ingest.tours import retour
from tests.factories import FakeCatalog, meta, mission, sortie
from tests.integration.test_tours import MONTHLY
from tests.sortie_tours import assert_sortie_tours_consistent

pytestmark = pytest.mark.django_db


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def put(uid: str, started_at: datetime, *, winner: int | None = None) -> Mission:
    result = mission((sortie(0, 1), sortie(1, 2)), end_tick=6000, winner=winner)
    with transaction.atomic():
        return save_mission(result, meta(uid, started_at), FakeCatalog(), DEFAULT_RULES, MONTHLY)


def sortie_tours() -> set[int | None]:
    return set(PlayerSortie.objects.values_list("tour_id", flat=True))


def test_a_new_mission_s_sorties_carry_its_tour() -> None:
    saved = put("m1", at(2026, 10, 1, 8))

    assert sortie_tours() == {saved.tour_id}
    assert saved.tour_id is not None
    assert_sortie_tours_consistent()


def test_a_re_ingest_that_moves_the_mission_moves_its_sorties() -> None:
    first = put("m1", at(2026, 9, 30, 23))
    put("m2", at(2026, 9, 30, 20))
    assert_sortie_tours_consistent()

    moved = put("m1", at(2026, 10, 1, 1))  # the same mission, now in the next tour

    assert moved.pk == first.pk
    assert moved.tour_id != first.tour_id
    assert set(PlayerSortie.objects.filter(mission=moved).values_list("tour_id", flat=True)) == {moved.tour_id}
    assert_sortie_tours_consistent()


def test_a_decisive_cut_moves_the_sorties_of_the_missions_after_it() -> None:
    SiteSettings.objects.update_or_create(pk=1, defaults={"tour_on_win": True, "tour_on_win_applied": True})
    put("a", at(2026, 10, 1, 8))
    put("c", at(2026, 10, 2, 8))
    assert len(sortie_tours()) == 1

    put("b", at(2026, 10, 1, 12), winner=2)  # late and decisive: "c" now starts a new tour

    assert len(sortie_tours()) == 2
    assert_sortie_tours_consistent()


def test_retour_and_discard_keep_the_sorties_in_step() -> None:
    put("a", at(2026, 9, 30, 23))
    live = put("b", at(2026, 10, 1, 3))
    Mission.objects.filter(pk=live.pk).update(is_live=True)
    with transaction.atomic():
        retour(MONTHLY)
    assert_sortie_tours_consistent()

    with transaction.atomic():
        discard_provisional_mission(Mission.objects.get(pk=live.pk), MONTHLY)

    assert Tour.objects.count() == 1
    assert_sortie_tours_consistent()


def test_the_guard_notices_a_sortie_in_another_tour_than_its_mission() -> None:
    put("a", at(2026, 9, 30, 23))
    put("b", at(2026, 10, 1, 3))
    PlayerSortie.objects.filter(mission__mission_uid="a").update(tour=Mission.objects.get(mission_uid="b").tour)

    with pytest.raises(AssertionError, match="differs from their mission"):
        assert_sortie_tours_consistent()


@pytest.mark.django_db(transaction=True)
def test_the_migration_backfills_the_tour_of_the_missions(tmp_path: Path) -> None:
    put("a", at(2026, 9, 30, 23))
    put("b", at(2026, 10, 1, 3))
    expected = dict(PlayerSortie.objects.values_list("pk", "tour_id"))
    assert len(set(expected.values())) == 2
    try:
        MigrationExecutor(connection).migrate([("il2ks_db", "0075_branding_backgrounds_favicon")])  # drops the column
        MigrationExecutor(connection).migrate([("il2ks_db", "0076_sortie_tour")])
        got = dict(PlayerSortie.objects.values_list("pk", "tour_id"))
    finally:
        final = MigrationExecutor(connection)
        final.migrate(final.loader.graph.leaf_nodes())
    assert got == expected
