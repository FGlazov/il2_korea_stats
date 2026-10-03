"""Applying pending database migrations (FR-OPS-3), after a backup (FR-OPS-6, NFR-INS-4).

Every command that writes the database calls `migrate_if_needed` first. Needs Django to be set up already.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from il2ks.config import Config

log = logging.getLogger(__name__)


def django_setup() -> None:
    """Start Django. `IL2KS_DATA_DIR` must already name the data folder: `settings.py` reads it once."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
    import django

    django.setup()


def migrate_if_needed(cfg: Config, command: str, wait: float | None) -> Path | None:
    """Apply pending migrations under the writer lock; returns the pre-migration backup, if one was made.

    The backup is only made when migrations are pending and the database already holds data (`il2ks setup` on a fresh
    install has nothing to protect). If the backup fails, the migrations are not applied."""
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from il2ks.ingest.lock import WriterLock
    from il2ks.ops.backup import backup_before_migration

    with WriterLock(cfg.data_dir, command, wait=wait):
        executor = MigrationExecutor(connection)
        if not executor.migration_plan(executor.loader.graph.leaf_nodes()):
            return None
        backup = backup_before_migration(cfg)
        if backup is not None:
            log.info("backed up the database before updating it: %s", backup)
        log.info("applying database migrations")
        call_command("migrate", interactive=False, verbosity=0)
        _backfill_tours(cfg)
        _backfill_scores(cfg)
        return backup


def _backfill_scores(cfg: Config) -> None:
    """Sorties saved before the score existed have none: when there are pilot sorties and none has a score, compute
    every score from the stored columns and rebuild level 2 (FR-WEB-7). Rule changes: `il2ks rebuild-aggregates`."""
    from django.db import transaction

    from il2ks.db.models import PlayerSortie, Role
    from il2ks.ingest.aggregates import rebuild_aggregates

    pilots = PlayerSortie.objects.filter(role=Role.PILOT)
    if pilots.exists() and not pilots.exclude(air_points=0, ground_points=0).exists():
        log.info("scoring existing sorties")
        with transaction.atomic():
            rebuild_aggregates(cfg.ratings, cfg.tours, score=cfg.score)


def _backfill_tours(cfg: Config) -> None:
    """Missions saved before tours existed get their tour, and the per-tour rows are built (FR-WEB-10, TD-26)."""
    from django.db import transaction

    from il2ks.db.models import Mission
    from il2ks.ingest.aggregates import rebuild_aggregates

    if Mission.objects.filter(tour__isnull=True).exists():
        log.info("assigning existing missions to tours")
        with transaction.atomic():
            rebuild_aggregates(cfg.ratings, cfg.tours, score=cfg.score)
