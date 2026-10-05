"""A changed catalog file (`weapon_mods.csv` significant flags, `payloads.csv`, `object_aliases.csv`) reaches the stored
rows at the next upgrade: the one-time backfill markers alone never re-ran (aircraft_mods.py promises "a change applies
with rebuild-aggregates"). A fingerprint of those files is kept in `SiteSettings.backfills_done`."""

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
    assert [m for m in get_site_settings().backfills_done if m.startswith("catalog:")] == ["catalog:v2"]


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
