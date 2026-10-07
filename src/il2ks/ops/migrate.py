"""Applying pending database migrations (FR-OPS-3), after a backup (FR-OPS-6, NFR-INS-4).

Every command that writes the database calls `migrate_if_needed` first. Needs Django to be set up already.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

from il2ks.config import Config

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

    from django.db import models
    from django.db.migrations.executor import MigrationExecutor

    from il2ks.db.models import Page

log = logging.getLogger(__name__)


def django_setup() -> None:
    """Start Django. `IL2KS_DATA_DIR` must already name the data folder: `settings.py` reads it once."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
    import django

    django.setup()


def _pending(executor_cls: type[MigrationExecutor]) -> bool:
    from django.db import connection

    executor = executor_cls(connection)
    return bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))


def migrate_if_needed(cfg: Config, command: str, wait: float | None) -> Path | None:
    """Apply pending migrations; returns the pre-migration backup, if one was made.

    Looking for pending migrations is a read and takes no lock, so `web` runs next to a long `ingest` (FR-ING-20). Only
    applying migrations takes the writer lock (`LockBusyError` is possible only then); the plan is checked again under
    the lock, because another process may have migrated meanwhile.

    The backup is only made when migrations are pending and the database already holds data (`il2ks setup` on a fresh
    install has nothing to protect). If the backup fails, the migrations are not applied."""
    from django.core.management import call_command
    from django.db.migrations.executor import MigrationExecutor

    from il2ks.ingest.lock import LockBusyError, WriterLock
    from il2ks.ops.backup import backup_before_migration

    if not _pending(MigrationExecutor):
        if catalog_changed() or pages_stale():
            try:
                with WriterLock(cfg.data_dir, command, wait=wait):
                    refresh_for_catalog_change(cfg, backup=True)
                    refresh_stale_pages()
            except LockBusyError:  # a writer is running (and did or will do this itself): `web` must still start
                log.info("the catalog files changed; the next writer refreshes the stored rows")
        return None
    with WriterLock(cfg.data_dir, command, wait=wait):
        if not _pending(MigrationExecutor):
            return None
        backup = backup_before_migration(cfg)
        if backup is not None:
            log.info("backed up the database before updating it: %s", backup)
        log.info("applying database migrations")
        call_command("migrate", interactive=False, verbosity=0)
        refresh_for_catalog_change(cfg)  # a fresh database only records the fingerprint; an upgrade refreshes once
        refresh_stale_pages()
        return backup


def _stale_pages() -> models.QuerySet[Page]:
    from django.db.models import Q

    from il2ks.db.models import Page
    from il2ks.web.pages import RENDERER_VERSION

    return Page.objects.filter(
        Q(renderer_version__lt=RENDERER_VERSION) | Q(page_translations__renderer_version__lt=RENDERER_VERSION)
    ).distinct()


def pages_stale() -> bool:
    """Whether a Markdown page (or one of its translations) was rendered by an older renderer. Two cheap reads."""
    return _stale_pages().exists()


def refresh_stale_pages() -> bool:
    """Render the Markdown pages again whose stored HTML comes from an older renderer (`RENDERER_VERSION`), so a
    tightened allowlist reaches pages saved before the upgrade; re-publishes their navigation links and bumps the data
    version (TD-28). Returns whether anything was rendered. The caller holds the writer lock."""
    from django.db import transaction

    from il2ks.db.site import bump_data_version
    from il2ks.web.pages import publish_page

    pages = list(_stale_pages())
    if not pages:
        return False
    with transaction.atomic():
        for page in pages:
            publish_page(page)
        bump_data_version()
    log.info("rendered %d Markdown page(s) again after an update of the renderer", len(pages))
    return True


def _rebuild_all(cfg: Config) -> None:
    """The one place the catalog refresh calls `rebuild_aggregates`, so no configured section (ratings, tours, marks,
    score, killboard) can be forgotten."""
    from il2ks.ingest.aggregates import rebuild_aggregates
    from il2ks.ingest.rule_store import effective_config

    rules = effective_config(cfg)
    rebuild_aggregates(rules.ratings, rules.tours, marks=rules.marks, score=rules.score, board=rules.board)


def _refresh_level_1_for_catalog() -> bool:
    """The level 1 part of a catalog refresh (loadout names, alias duplicates merged). Returns whether level 2 must be
    rebuilt: always when there are sorties, because a flipped `significant` flag shows nowhere in level 1."""
    from il2ks.db.models import PlayerSortie

    if not PlayerSortie.objects.exists():
        return False
    _refresh_payload_names()
    _merge_alias_duplicates()
    return True


def _refresh_payload_names() -> bool:
    """The loadout table changed (renumbered ids, newly resolving types, new rows): every sortie's
    `payload_name` is looked up again from its stored `payload_id` and aircraft type. `weapon_mods` needs nothing (the
    raw `WM` is stored, names are looked up when a page is shown). Returns whether a name changed, which means
    the per-loadout aircraft stats (level 2) must be rebuilt. Unknown ids keep the empty name (OQ-25)."""
    from il2ks.core.catalog.loader import load_default_catalog
    from il2ks.db.models import PlayerSortie
    from il2ks.ingest.persist import PAYLOAD_NAME_MAX

    catalog = load_default_catalog()
    groups = PlayerSortie.objects.values_list("aircraft__log_name", "payload_id").distinct()
    changed = False
    for aircraft_type, payload_id in list(groups):
        payload = catalog.payload(aircraft_type, payload_id)
        name = payload.readable_name[:PAYLOAD_NAME_MAX] if payload is not None else ""
        rows = PlayerSortie.objects.filter(aircraft__log_name=aircraft_type, payload_id=payload_id).exclude(
            payload_name=name
        )
        changed = bool(rows.update(payload_name=name)) or changed
    return changed


def _merge_alias_duplicates() -> bool:
    """A type written in two spellings by the logs (`Il-10` / `IL-10`, `B 29` / `B-29`) can have two `GameObject` rows,
    so two aircraft pages and split stats; the catalog (`object_aliases.csv`) says which spellings are one type. Every
    known type that has several rows keeps the one named like the catalog (or the oldest, renamed), the others are
    merged into it: level 1 rows (sorties, per-mission ammo) are repointed, level 2 rows of the duplicate are dropped
    (the rebuild recreates them). Returns whether anything was merged."""
    from django.db.models.fields.reverse_related import ManyToOneRel

    from il2ks.core.catalog.loader import load_default_catalog
    from il2ks.db.models import GameObject, MissionAircraftAmmo, MissionAircraftAmmoMix, PlayerSortie

    catalog = load_default_catalog()
    groups: dict[str, list[GameObject]] = {}
    for obj in GameObject.objects.order_by("pk"):
        info = catalog.lookup(obj.log_name)
        if info.is_known:
            groups.setdefault(info.log_name, []).append(obj)
    # Every reverse relation, hidden ones included: `related_name="+"` ones (`PlayerTypeKillboard`) are left out of
    # `related_objects`, and their PROTECT would stop `dup.delete()`. A relation added later is handled as level 2.
    relations = [rel for rel in GameObject._meta.get_fields(include_hidden=True) if isinstance(rel, ManyToOneRel)]
    # The level 1 tables with a unique key that holds the aircraft: the other columns of that key.
    ammo_keys: dict[type[models.Model], tuple[str, ...]] = {
        MissionAircraftAmmo: ("mission_id", "combat_role", "weapon_mods", "ammo"),
        MissionAircraftAmmoMix: ("mission_id", "combat_role", "weapon_mods", "mix", "ammo"),
    }
    merged = False
    for canonical, objs in groups.items():
        if len(objs) < 2:
            continue
        keep = next((o for o in objs if o.log_name == canonical), objs[0])
        for dup in objs:
            if dup.pk == keep.pk:
                continue
            for rel in relations:
                model, field = rel.related_model, rel.field.name
                manager = cast("models.Manager[models.Model]", model._default_manager)
                if model is PlayerSortie:  # no unique key holds the aircraft: one UPDATE
                    manager.filter(**{field: dup.pk}).update(**{field: keep.pk})
                elif model in ammo_keys:
                    _merge_ammo_rows(manager, field, dup.pk, keep.pk, ammo_keys[model])
                else:  # level 2: the rebuild recreates it
                    manager.filter(**{field: dup.pk}).delete()
            if dup.name_overridden and not keep.name_overridden:  # the admin's custom name survives the merge
                keep.display_name, keep.name_overridden = dup.display_name, True
                keep.save(update_fields=["display_name", "name_overridden"])
            dup.delete()
            merged = True
        if keep.log_name != canonical:
            keep.log_name = canonical
            keep.save(update_fields=["log_name"])
    return merged


def _merge_ammo_rows(
    manager: models.Manager[models.Model], field: str, dup_pk: int, keep_pk: int, key: tuple[str, ...]
) -> None:
    """Move the per-mission ammo rows of the duplicate to the kept aircraft; where the mission already has a row of the
    kept one for the same key (it logged both spellings), the counters are added to that row and the duplicate goes."""
    from django.db.models import F

    for row in manager.filter(**{field: dup_pk}):
        twin = manager.filter(**{field: keep_pk}, **{name: getattr(row, name) for name in key})
        if twin.exists():
            kills, hits = getattr(row, "kills"), getattr(row, "hits")  # noqa: B009
            twin.update(kills=F("kills") + kills, hits=F("hits") + hits)
            row.delete()
        else:
            manager.filter(pk=row.pk).update(**{field: keep_pk})


CATALOG_FILES = ("weapon_mods.csv", "payloads.csv", "payload_aliases.csv", "object_aliases.csv")
"""The catalog files whose content is copied into stored rows: significant mods (the filter scopes of level 2), loadout
names (`PlayerSortie.payload_name`), and the log-name aliases (merged `GameObject` rows)."""


def catalog_fingerprint(data: Traversable | None = None) -> str:
    """A short hash of the `CATALOG_FILES` (line endings normalised, so a Windows checkout hashes like Linux's);
    `data` is the folder to read, the shipped catalog by default."""
    import hashlib
    from importlib.resources import files

    folder = data if data is not None else files("il2ks.core.catalog").joinpath("data")
    digest = hashlib.sha256()
    for name in CATALOG_FILES:
        digest.update(name.encode())
        digest.update(folder.joinpath(name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def _stored_catalog_fingerprint() -> str:
    from il2ks.db.site import get_site_settings

    return get_site_settings().catalog_fingerprint


def _record_catalog_fingerprint(fingerprint: str) -> None:
    from il2ks.db.models import SiteSettings

    SiteSettings.objects.filter(pk=1).update(catalog_fingerprint=fingerprint)


def catalog_changed() -> bool:
    """Whether the shipped catalog files differ from what the stored rows were last brought in line with. A read of
    one row and four small files: cheap enough for every start."""
    return _stored_catalog_fingerprint() != catalog_fingerprint()


def refresh_for_catalog_change(cfg: Config, *, backup: bool = False) -> bool:
    """Apply a changed `weapon_mods.csv` / `payloads.csv` / `payload_aliases.csv` / `object_aliases.csv` to the stored
    rows: loadout names are looked up again, new alias duplicates merged, and level 2 rebuilt once (the significant
    mods and so the filter scopes come from the catalog). Nothing happens when the fingerprint is unchanged; an empty
    database only records it. With `backup`, a database that holds sorties is backed up first (the refresh merges and
    deletes level 1 rows). Returns whether anything was refreshed. The caller holds the writer lock."""
    from django.db import transaction

    from il2ks.db.models import PlayerSortie

    current = catalog_fingerprint()
    if _stored_catalog_fingerprint() == current:
        return False
    if backup and PlayerSortie.objects.exists():
        from il2ks.ops.backup import backup_before_migration

        saved = backup_before_migration(cfg)
        if saved is not None:
            log.info("backed up the database before refreshing it for the catalog change: %s", saved)
    with transaction.atomic():
        if PlayerSortie.objects.exists():
            log.info("the catalog files changed: refreshing the stored loadout names, aircraft rows and aggregates")
            if _refresh_level_1_for_catalog():
                _rebuild_all(cfg)
        _record_catalog_fingerprint(current)
    return True
