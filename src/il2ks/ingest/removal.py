"""Delete a tour or single missions (FR-ADM-8, FR-ADM-3): the admin's requests, which the `watch` loop runs
(`ReprocessRequest.remove_tour_id`, `remove_mission_ids`).

Under the writer lock, after a backup, in one transaction: the missions and sorties go, and so do the players who flew
nowhere else (a player without a sortie would stay on the pilot list with empty counters). Each mission gets a
`RemovedMission` row, because its archive stays on disk: without the row the next reprocess would build it again. Then
the tours are drawn again where the mode needs it (manual mode: the tour before takes over a deleted tour's time, the
newest one is open; the decisive-tour option: `resegment`'s parts follow the missions that are left) and level 2 is
rebuilt whole: a rare admin action, so the one path that cannot drift from a fresh ingest.

A tour can keep its missions only in manual mode (`tours.tour_taking_over`): they join the tour next to it. In the
calendar modes a period and its tour are the same thing, so missions that stayed would bring the tour straight back. A
tour that loses its last mission goes as well, except in manual mode, where it is the admin's boundary.

A running mission stops either request: its final save would bring it back. In the calendar modes, a mission flown later
in a deleted tour's period simply starts that period's tour again.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from django.db import transaction
from django.db.models import QuerySet

from il2ks.config import Config
from il2ks.core.tours import TourRules
from il2ks.db.models import Mission, Player, PlayerSortie, RemovedMission, Tour
from il2ks.db.site import bump_data_version, clear_level2_pending
from il2ks.ingest import wipe_state
from il2ks.ingest.activity import day_of, recompute_days
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.lock import WriterLock
from il2ks.ingest.rule_store import adopt_overrides, config_with, effective_config, rebuild_overrides
from il2ks.ingest.runner import utcnow
from il2ks.ingest.tours import CHUNK, delete_empty_tours, effective_rules, move_missions, retour_all, tour_taking_over

log = logging.getLogger(__name__)


class RemovalPhase(StrEnum):
    BACKUP = "backup"
    DELETE = "delete"


class RemovalError(Exception):
    """Nothing was deleted: the message says why."""


type BackupFn = Callable[[Config], Path]


def default_backup(cfg: Config) -> Path:
    from il2ks.ops.backup import create_backup

    return create_backup(cfg, "pre-tour-delete")


def _delete_missions(missions: QuerySet[Mission], removed_by: str, now: datetime) -> set[int]:
    """Delete these missions and the players left without a sortie, recording what `archived_targets` must leave out.
    Returns the tours they were in."""
    rows = list(missions.values_list("pk", "mission_uid", "started_at", "tour_id", "tour__title"))
    RemovedMission.objects.bulk_create(
        [RemovedMission(mission_uid=r[1], tour_title=r[4] or "", removed_at=now, removed_by=removed_by) for r in rows],
        ignore_conflicts=True,  # the same UID on a second server
    )
    pks = [row[0] for row in rows]
    players: set[int] = set()
    for start in range(0, len(pks), CHUNK):
        chunk = pks[start : start + CHUNK]
        players |= set(PlayerSortie.objects.filter(mission_id__in=chunk).values_list("player_id", flat=True))
        Mission.objects.filter(pk__in=chunk).delete()
    gone = sorted(players)
    for start in range(0, len(gone), CHUNK):
        Player.objects.filter(pk__in=gone[start : start + CHUNK], sortie_rows__isnull=True).delete()
    recompute_days({day_of(row[2]) for row in rows})
    return {row[3] for row in rows if row[3] is not None}


def _in_lock(
    cfg: Config,
    check: Callable[[TourRules], None],
    delete: Callable[[TourRules, datetime], int],
    backup: BackupFn,
    on_phase: Callable[[RemovalPhase], None] | None,
    now: Callable[[], datetime],
    lock_wait: float | None,
) -> int:
    def phase(step: RemovalPhase) -> None:
        if on_phase is not None:
            on_phase(step)

    with WriterLock(cfg.data_dir, "remove", wait=lock_wait):
        if wipe_state.pending(cfg):
            raise RemovalError("a reprocess of everything has not finished yet: run it first")
        check(effective_rules(effective_config(cfg).tours))
        phase(RemovalPhase.BACKUP)
        try:
            path = backup(cfg)
        except Exception as exc:
            raise RemovalError(f"the backup failed, so nothing was deleted: {exc}") from exc
        log.info("backup before the deletion: %s", path)
        phase(RemovalPhase.DELETE)
        with transaction.atomic():
            overrides = rebuild_overrides(reassign_tours=False)
            run = config_with(cfg, overrides)
            rules = effective_rules(run.tours)
            deleted = delete(rules, now())
            if rules.mode == "manual" or rules.on_win:
                retour_all(rules)
            rebuild_aggregates(run.ratings, run.tours, marks=run.marks, score=run.score, board=run.board)
            adopt_overrides(overrides)
            clear_level2_pending()
            bump_data_version()
    log.info("deleted %d mission(s)", deleted)
    return deleted


def remove_tour(
    cfg: Config,
    tour_id: int,
    removed_by: str,
    /,
    *,
    keep_missions: bool = False,
    backup: BackupFn = default_backup,
    on_phase: Callable[[RemovalPhase], None] | None = None,
    now: Callable[[], datetime] = utcnow,
    lock_wait: float | None = None,
) -> int:
    """Delete one tour, with its missions or (`keep_missions`) moving them to the tour next to it, and rebuild level 2.
    Returns the missions deleted. Raises `LockBusyError` when the writer lock is taken and `RemovalError` when the tour
    cannot go; neither changes anything."""

    def check(rules: TourRules) -> None:
        tour = Tour.objects.filter(pk=tour_id).first()
        if tour is None:
            raise RemovalError("that tour no longer exists")
        if keep_missions:
            if tour_taking_over(tour, rules.mode) is None:
                raise RemovalError("no other tour can take over these missions")
        elif Mission.objects.filter(tour=tour, is_live=True).exists():
            raise RemovalError("a mission of this tour is still running: try again when it has ended")

    def delete(rules: TourRules, moment: datetime) -> int:
        tour = Tour.objects.get(pk=tour_id)
        missions = Mission.objects.filter(tour=tour)
        target = tour_taking_over(tour, rules.mode) if keep_missions else None
        if target is not None:
            move_missions(list(missions.values_list("pk", flat=True)), target.pk)
            count = 0
        else:
            count = missions.count()
            _delete_missions(missions, removed_by, moment)
        tour.delete()
        return count

    return _in_lock(cfg, check, delete, backup, on_phase, now, lock_wait)


def remove_missions(
    cfg: Config,
    mission_ids: Sequence[int],
    removed_by: str,
    /,
    *,
    backup: BackupFn = default_backup,
    on_phase: Callable[[RemovalPhase], None] | None = None,
    now: Callable[[], datetime] = utcnow,
    lock_wait: float | None = None,
) -> int:
    """Delete single missions and rebuild level 2. Returns the missions deleted (one that is gone already is no
    problem). Raises as `remove_tour` does."""

    def check(_rules: TourRules) -> None:
        if Mission.objects.filter(pk__in=mission_ids, is_live=True).exists():
            raise RemovalError("one of these missions is still running: try again when it has ended")

    def delete(rules: TourRules, moment: datetime) -> int:
        missions = Mission.objects.filter(pk__in=mission_ids)
        count = missions.count()
        delete_empty_tours(_delete_missions(missions, removed_by, moment), rules)
        return count

    return _in_lock(cfg, check, delete, backup, on_phase, now, lock_wait)
