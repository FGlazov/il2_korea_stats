"""The upgrade merge of duplicate aircraft rows (`Il-10` / `IL-10`, `B 29` / `B-29`; OQ-120): every relation of the
duplicate is handled, counters of unique-conflicting level 1 rows are summed, an admin's custom name is kept."""

from pathlib import Path

import pytest

from il2ks.db.models import (
    GameObject,
    Mission,
    MissionAircraftAmmo,
    MissionAircraftAmmoMix,
    Player,
    PlayerSortie,
    PlayerTypeKillboard,
)
from tests.factories import mission, save, sortie

pytestmark = pytest.mark.django_db


def _upgrade(tmp_path: Path) -> None:
    from il2ks.ops import migrate
    from tests.ops_helpers import make_instance

    migrate._run_backfills(make_instance(tmp_path), [migrate.BACKFILL_AIRCRAFT_ALIASES])  # pyright: ignore[reportPrivateUsage]


def _spaced_duplicate() -> GameObject:
    """A saved B-29 sortie, moved onto a `B 29` row as an old database has it; returns that duplicate."""
    save(mission((sortie(0, 1, aircraft_type="B-29"),)))
    dup = GameObject.objects.create(log_name="B 29", display_name="B-29", cls="bomber")
    PlayerSortie.objects.update(aircraft=dup)
    return dup


def test_the_upgrade_merges_rows_that_a_by_type_killboard_row_points_at(tmp_path: Path) -> None:
    """The by-type killboard's relations have `related_name="+"` and PROTECT: they used to stop the merge with a
    ProtectedError, which rolled the whole upgrade back."""
    dup = _spaced_duplicate()
    player = Player.objects.get()
    PlayerTypeKillboard.objects.create(player=player, enemy_aircraft=dup, kills_with=dup, deaths_in=dup)

    _upgrade(tmp_path)

    assert list(GameObject.objects.values_list("log_name", flat=True)) == ["B-29"]
    assert not PlayerTypeKillboard.objects.filter(enemy_aircraft__log_name="B 29").exists()


def test_the_upgrade_adds_up_the_ammo_rows_of_a_mission_that_logged_both_spellings(tmp_path: Path) -> None:
    dup = _spaced_duplicate()
    keep = GameObject.objects.get(log_name="B-29")
    m = Mission.objects.get()
    MissionAircraftAmmo.objects.create(mission=m, aircraft=keep, ammo="A", kills=2, hits=10)
    MissionAircraftAmmo.objects.create(mission=m, aircraft=dup, ammo="A", kills=3, hits=7)
    MissionAircraftAmmo.objects.create(mission=m, aircraft=dup, ammo="B", kills=1, hits=1)
    MissionAircraftAmmoMix.objects.create(mission=m, aircraft=keep, mix="A", ammo="A", kills=2, hits=10)
    MissionAircraftAmmoMix.objects.create(mission=m, aircraft=dup, mix="A", ammo="A", kills=3, hits=7)

    _upgrade(tmp_path)

    rows = {r.ammo: (r.kills, r.hits) for r in MissionAircraftAmmo.objects.filter(aircraft__log_name="B-29")}
    assert rows == {"A": (5, 17), "B": (1, 1)}
    mix = MissionAircraftAmmoMix.objects.get(aircraft__log_name="B-29")
    assert (mix.kills, mix.hits) == (5, 17)


def test_the_upgrade_keeps_a_custom_name_set_on_the_duplicate(tmp_path: Path) -> None:
    dup = _spaced_duplicate()
    dup.display_name = "Superfortress"
    dup.name_overridden = True
    dup.save()

    _upgrade(tmp_path)

    kept = GameObject.objects.get()
    assert (kept.log_name, kept.display_name, kept.name_overridden) == ("B-29", "Superfortress", True)
