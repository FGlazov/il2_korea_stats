"""A changed catalog file (`weapon_mods.csv` significant flags, `payloads.csv`, `object_aliases.csv`) reaches the stored
rows at the next start (aircraft_mods.py promises "a change applies with rebuild-aggregates"). A fingerprint of those
files is kept in `SiteSettings.catalog_fingerprint`."""

from pathlib import Path

import pytest

from il2ks.db.models import GameObject, PlayerSortie
from il2ks.db.site import get_site_settings
from il2ks.ops import migrate
from tests.factories import mission, save, sortie
from tests.ops_helpers import make_instance, recording, returning

pytestmark = pytest.mark.django_db


def _catalog_dir(folder: Path, significant: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "weapon_mods.csv").write_text(f"vehicle,mod_id,name,significant\nyak-9p,1,Gunsight,{significant}\n")
    (folder / "payloads.csv").write_text("vehicle,payload_id,editor_name,readable_name\n")
    (folder / "payload_aliases.csv").write_text("log_name,vehicle\n")
    (folder / "object_aliases.csv").write_text("log_name,same_as\nB 29,B-29\n")
    return folder


def test_the_fingerprint_changes_with_a_significant_flag_and_not_otherwise(tmp_path: Path) -> None:
    a = _catalog_dir(tmp_path / "a", "false")
    same = _catalog_dir(tmp_path / "same", "false")
    flipped = _catalog_dir(tmp_path / "flipped", "true")

    assert migrate.catalog_fingerprint(a) == migrate.catalog_fingerprint(same)
    assert migrate.catalog_fingerprint(a) != migrate.catalog_fingerprint(flipped)


def test_a_changed_catalog_rebuilds_once_and_an_unchanged_one_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(mission((sortie(0, 1),)))  # a database with data
    cfg = make_instance(tmp_path)
    rebuilds: list[str] = []
    monkeypatch.setattr(migrate, "_rebuild_all", recording(rebuilds, "rebuild"))
    monkeypatch.setattr(migrate, "catalog_fingerprint", returning("v1"))

    assert migrate.refresh_for_catalog_change(cfg) is True  # nothing recorded yet: the stored rows may be stale
    assert migrate.refresh_for_catalog_change(cfg) is False  # unchanged
    assert rebuilds == ["rebuild"]

    monkeypatch.setattr(migrate, "catalog_fingerprint", returning("v2"))
    assert migrate.catalog_changed() is True
    assert migrate.refresh_for_catalog_change(cfg) is True
    assert migrate.catalog_changed() is False
    assert rebuilds == ["rebuild", "rebuild"]
    assert get_site_settings().catalog_fingerprint == "v2"


def test_a_changed_alias_file_merges_the_new_duplicate_at_the_next_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(mission((sortie(0, 1, aircraft_type="B-29"),)))
    cfg = make_instance(tmp_path)
    monkeypatch.setattr(migrate, "_rebuild_all", returning(None))
    monkeypatch.setattr(migrate, "catalog_fingerprint", returning("v1"))
    migrate.refresh_for_catalog_change(cfg)
    dup = GameObject.objects.create(log_name="B 29", display_name="B-29", cls="bomber")
    PlayerSortie.objects.update(aircraft=dup)

    monkeypatch.setattr(migrate, "catalog_fingerprint", returning("v2"))
    migrate.refresh_for_catalog_change(cfg)

    assert list(GameObject.objects.values_list("log_name", flat=True)) == ["B-29"]


def test_an_empty_database_only_records_the_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_instance(tmp_path)
    rebuilds: list[str] = []
    monkeypatch.setattr(migrate, "_rebuild_all", recording(rebuilds, "rebuild"))

    migrate.refresh_for_catalog_change(cfg)

    assert rebuilds == []
    assert migrate.catalog_changed() is False


def test_a_refresh_for_a_changed_catalog_backs_up_first_and_only_then(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refresh merges and deletes level 1 rows, so a database with data is backed up first; an unchanged catalog
    costs no backup."""
    from il2ks.ops import backup

    save(mission((sortie(0, 1),)))
    cfg = make_instance(tmp_path)
    made: list[str] = []
    monkeypatch.setattr(backup, "backup_before_migration", recording(made, "backup"))
    monkeypatch.setattr(migrate, "_rebuild_all", returning(None))
    monkeypatch.setattr(migrate, "catalog_fingerprint", returning("v1"))

    migrate.refresh_for_catalog_change(cfg, backup=True)
    migrate.refresh_for_catalog_change(cfg, backup=True)  # unchanged

    assert made == ["backup"]


def test_migrate_if_needed_refreshes_once_after_applying_migrations_and_again_only_for_a_catalog_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.core import management
    from django.db.migrations.executor import MigrationExecutor

    save(mission((sortie(0, 1),)))
    cfg = make_instance(tmp_path)
    rebuilds: list[str] = []
    monkeypatch.setattr(migrate, "_rebuild_all", recording(rebuilds, "rebuild"))
    monkeypatch.setattr(migrate, "catalog_fingerprint", returning("v1"))
    monkeypatch.setattr(MigrationExecutor, "migration_plan", returning([("fake", False)]))
    monkeypatch.setattr(management, "call_command", recording([], "migrate"))

    migrate.migrate_if_needed(cfg, "ingest", wait=None)  # migrations applied: the stored rows are brought in line

    assert rebuilds == ["rebuild"]
    assert migrate.catalog_changed() is False

    monkeypatch.setattr(MigrationExecutor, "migration_plan", returning([]))
    migrate.migrate_if_needed(cfg, "ingest", wait=None)  # up to date, same catalog: nothing
    assert rebuilds == ["rebuild"]

    monkeypatch.setattr(migrate, "catalog_fingerprint", returning("v2"))
    migrate.migrate_if_needed(cfg, "ingest", wait=None)  # up to date, a changed catalog: one more rebuild
    assert rebuilds == ["rebuild", "rebuild"]
