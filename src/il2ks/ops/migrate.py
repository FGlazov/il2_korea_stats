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
    from django.db.migrations.executor import MigrationExecutor

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

    from il2ks.ingest.lock import WriterLock
    from il2ks.ops.backup import backup_before_migration

    if not _pending(MigrationExecutor):
        return None
    with WriterLock(cfg.data_dir, command, wait=wait):
        if not _pending(MigrationExecutor):
            return None
        backup = backup_before_migration(cfg)
        if backup is not None:
            log.info("backed up the database before updating it: %s", backup)
        log.info("applying database migrations")
        call_command("migrate", interactive=False, verbosity=0)
        _backfill_tours(cfg)
        _backfill_scores(cfg)
        _backfill_type_ratings(cfg)
        _backfill_type_killboard(cfg)
        _backfill_interception(cfg)
        _backfill_assist_split(cfg)
        _backfill_accuracy(cfg)
        _backfill_achievements()
        return backup


BACKFILL_TOURS = "tours"  # missions without a tour, per-tour rows, best streaks
BACKFILL_SCORES = "scores"  # sortie scores (FR-WEB-7)
BACKFILL_TYPE_RATINGS = "type_ratings"  # per-type Elo and the prop / jet pools (OQ-49)
BACKFILL_TYPE_KILLBOARD = "type_killboard"  # killboard by aircraft type, per-tour / intercept matchups
BACKFILL_INTERCEPTION = "interception"  # kills of bombers / attackers per sortie, the skill boards' counters
BACKFILL_ASSIST_SPLIT = "assist_split"  # assists on air vs ground victims
BACKFILL_ACCURACY = "accuracy"  # rounds fired and gun hits per sortie (from the stored ammo JSON)
BACKFILL_ACHIEVEMENTS = "achievements"  # medals (FR-WEB-26)


def _already_done(name: str) -> bool:
    """Whether this one-time backfill ran on this database (`SiteSettings.backfills_done`). The data triggers alone
    can't tell: with percentage penalties a legitimate database may hold only zero scores or no rated games, and would
    be rebuilt after every migration."""
    from il2ks.db.site import get_site_settings

    return name in get_site_settings().backfills_done


def _mark_done(name: str) -> None:
    """Record that the backfill ran (or was not needed), so a later migration doesn't repeat it."""
    from il2ks.db.models import SiteSettings
    from il2ks.db.site import get_site_settings

    done = get_site_settings().backfills_done
    if name not in done:
        SiteSettings.objects.filter(pk=1).update(backfills_done=[*done, name])


def _rebuild_all(cfg: Config) -> None:
    """The one place the backfills call `rebuild_aggregates`, so no configured section (ratings, tours, marks, score,
    killboard) can be forgotten by one of them."""
    from il2ks.ingest.aggregates import rebuild_aggregates

    rebuild_aggregates(cfg.ratings, cfg.tours, marks=cfg.marks, score=cfg.score, board=cfg.board)


def _backfill_assist_split(cfg: Config) -> None:
    """A database from before assists were split has `assists` but no `assists_air` / `assists_ground`. The timeline's
    `assist` entries name the victim (a player sortie, or the object type), so air and ground are derived from them with
    the replay's own rule (a victim is air when it is a player's aircraft or its catalog class is an air class); the
    remainder of `assists` (a timeline that lost entries) counts as ground. Then level 2 is rebuilt once, which also
    rescores (ground assists no longer score). `il2ks reprocess` gives the same."""
    from django.db import transaction

    from il2ks.core.catalog.loader import AIR_CLASSES
    from il2ks.db.models import GameObject, PlayerSortie
    from il2ks.ingest.dbutil import update_rows

    if _already_done(BACKFILL_ASSIST_SPLIT):
        return
    sorties = PlayerSortie.objects.filter(assists__gt=0)
    with transaction.atomic():
        if (
            sorties.exists()
            and not sorties.filter(assists_air__gt=0).exists()
            and not sorties.filter(assists_ground__gt=0).exists()
        ):
            log.info("splitting assists into air and ground")
            classes = dict(GameObject.objects.values_list("log_name", "cls"))
            changed: list[PlayerSortie] = []
            for sortie in sorties.only("pk", "assists", "timeline", "assists_air", "assists_ground"):
                air = 0
                for entry in sortie.timeline:
                    other = entry.get("counterpart")
                    if entry.get("kind") != "assist" or not isinstance(other, dict):
                        continue
                    victim = cast("dict[str, object]", other)
                    air += (
                        victim.get("sortie_id") is not None
                        or classes.get(str(victim.get("object_type"))) in AIR_CLASSES
                    )
                sortie.assists_air = min(air, sortie.assists)
                sortie.assists_ground = sortie.assists - sortie.assists_air
                changed.append(sortie)
            update_rows(PlayerSortie, changed, ["assists_air", "assists_ground"])
            _rebuild_all(cfg)
        _mark_done(BACKFILL_ASSIST_SPLIT)


def accuracy_from_stored(
    ammo: dict[str, object], damage_breakdown: list[dict[str, object]], air_types: set[str], *, resupplied: bool
) -> tuple[int | None, int, int]:
    """(rounds fired, gun hits on aircraft, gun hits on the ground) of a pilot sortie from its stored JSON: the numbers
    a replay gives (`core.replay.ammo.rounds_fired`, `breakdown.GunHits`).

    Gun hits = every stored hit line given of a gun ammo name (`ammo.hits[].hits_given`). The aircraft share is what
    `damage_breakdown` says was hit on aircraft (`hits_dealt` per counterpart: a player sortie or an air type), capped
    at the gun hits (it also holds the rare named bomb hit on an aircraft); the rest is ground."""
    from il2ks.core.replay.model import is_gun_ammo

    hits = ammo.get("hits")
    gun = 0
    if isinstance(hits, list):
        for row in cast("list[dict[str, object]]", hits):
            name, given = row.get("ammo"), row.get("hits_given")
            if isinstance(name, str) and isinstance(given, int) and is_gun_ammo(name):
                gun += given
    on_aircraft = 0
    for entry in damage_breakdown:
        other, dealt = entry.get("counterpart"), entry.get("hits_dealt")
        if not isinstance(other, dict) or not isinstance(dealt, int):
            continue
        victim = cast("dict[str, object]", other)
        if victim.get("sortie_id") is not None or str(victim.get("object_type")) in air_types:
            on_aircraft += dealt
    air = min(on_aircraft, gun)
    used = ammo.get("used")
    rounds: int | None = None
    if isinstance(used, dict) and not resupplied:
        counts = cast("dict[str, object]", used)
        bullets, shells = counts.get("bullets"), counts.get("shells")
        if (
            isinstance(bullets, int)
            and isinstance(shells, int)
            and bullets >= 0
            and shells >= 0
            and gun <= bullets + shells
        ):
            rounds = bullets + shells
    return rounds, air, gun - air


def _backfill_accuracy(cfg: Config) -> None:
    """A database from before accuracy has no `rounds_fired`, `gun_hits_air` or `gun_hits_ground` on its sorties. They
    follow from the stored JSON (`accuracy_from_stored`): rounds from the ammo `used` counts (unknown where "left"
    cannot be trusted, FR-ING-24), gun hits from the per-ammo hit counts split by target through the damage breakdown.
    Then level 2 is rebuilt once. `il2ks reprocess` gives the exact values."""
    from django.db import transaction
    from django.db.models import Q

    from il2ks.core.catalog.loader import AIR_CLASSES
    from il2ks.db.models import GameObject, PlayerSortie, Role
    from il2ks.ingest.dbutil import update_rows

    if _already_done(BACKFILL_ACCURACY):
        return
    pilots = PlayerSortie.objects.filter(role=Role.PILOT)
    with transaction.atomic():
        if (
            pilots.exists()
            and not pilots.filter(rounds_fired__isnull=False).exists()
            and not pilots.filter(Q(gun_hits_air__gt=0) | Q(gun_hits_ground__gt=0)).exists()
        ):
            log.info("deriving accuracy figures from the stored ammo")
            air_types = {name for name, cls in GameObject.objects.values_list("log_name", "cls") if cls in AIR_CLASSES}
            changed: list[PlayerSortie] = []
            for sortie in pilots.only("pk", "ammo", "damage_breakdown", "resupplied"):
                ammo = sortie.ammo
                rounds, air, ground = accuracy_from_stored(
                    ammo, sortie.damage_breakdown, air_types, resupplied=sortie.resupplied
                )
                sortie.rounds_fired, sortie.gun_hits_air, sortie.gun_hits_ground = rounds, air, ground
                changed.append(sortie)
            update_rows(PlayerSortie, changed, ["rounds_fired", "gun_hits_air", "gun_hits_ground"])
            _rebuild_all(cfg)
        _mark_done(BACKFILL_ACCURACY)


def _backfill_interception(cfg: Config) -> None:
    """A database from before the interception board has no `PlayerSortie.kills_air_intercept`. The sortie timelines
    name every kill's victim type (and the victim's sortie), so the column is derived from them (the same rule as the
    replay, `is_interception_victim`), then level 2 is rebuilt once (FR-WEB-7). `il2ks reprocess` gives the same."""
    from django.db import transaction

    from il2ks.core.replay.attack import is_interception_victim
    from il2ks.db.models import GameObject, PlayerSortie
    from il2ks.ingest.dbutil import update_rows

    if _already_done(BACKFILL_INTERCEPTION):
        return
    sorties = PlayerSortie.objects.filter(kills_air__gt=0)
    with transaction.atomic():
        if PlayerSortie.objects.exists() and not sorties.filter(kills_air_intercept__gt=0).exists():
            log.info("counting kills of bombers and attackers")
            classes = dict(GameObject.objects.values_list("log_name", "cls"))
            roles = dict(PlayerSortie.objects.exclude(combat_role=None).values_list("pk", "combat_role"))
            changed: list[PlayerSortie] = []
            for sortie in sorties.only("pk", "timeline", "kills_air_intercept"):
                count = 0
                for entry in sortie.timeline:
                    other = entry.get("counterpart")
                    if entry.get("kind") != "kill" or not isinstance(other, dict):
                        continue
                    victim = cast("dict[str, object]", other)
                    victim_id = victim.get("sortie_id")
                    victim_role = roles.get(victim_id) if isinstance(victim_id, int) else None
                    count += is_interception_victim(classes.get(str(victim.get("object_type"))), victim_role)
                if count:
                    sortie.kills_air_intercept = count
                    changed.append(sortie)
            update_rows(PlayerSortie, changed, ["kills_air_intercept"])
            _rebuild_all(cfg)
        _mark_done(BACKFILL_INTERCEPTION)


def _backfill_type_killboard(cfg: Config) -> None:
    """A database from before the killboard by aircraft type and the per-tour / intercept matchups has matchup rows
    (all-time only) but no `PlayerTypeKillboard` row (FR-WEB-8, FR-WEB-9): rebuild level 2 once."""
    from django.db import transaction

    from il2ks.db.models import AircraftMatchup, PlayerTypeKillboard

    if _already_done(BACKFILL_TYPE_KILLBOARD):
        return
    with transaction.atomic():
        if AircraftMatchup.objects.exists() and not PlayerTypeKillboard.objects.exists():
            log.info("building the killboard by aircraft type")
            _rebuild_all(cfg)
        _mark_done(BACKFILL_TYPE_KILLBOARD)


def _backfill_type_ratings(cfg: Config) -> None:
    """A database from before the per-type Elo (OQ-49) and the prop / jet pools has no rated games in any
    `PlayerAircraft` row and no `PlayerPool` rows: rebuild level 2 once, which also applies the current `[score]` rules
    (the penalty rules changed in the same release)."""
    from django.db import transaction

    from il2ks.db.models import PlayerAircraft, PlayerPool

    if _already_done(BACKFILL_TYPE_RATINGS):
        return
    rows = PlayerAircraft.objects.all()
    with transaction.atomic():
        if rows.exists() and not (rows.filter(elo_games__gt=0).exists() or PlayerPool.objects.exists()):
            log.info("rating aircraft types and rescoring sorties")
            _rebuild_all(cfg)
        _mark_done(BACKFILL_TYPE_RATINGS)


def _backfill_scores(cfg: Config) -> None:
    """Sorties saved before the score existed have none: when there are pilot sorties and none has a score, compute
    every score from the stored columns and rebuild level 2 (FR-WEB-7). Rule changes: `il2ks rebuild-aggregates`."""
    from django.db import transaction

    from il2ks.db.models import PlayerSortie, Role

    if _already_done(BACKFILL_SCORES):
        return
    pilots = PlayerSortie.objects.filter(role=Role.PILOT)
    with transaction.atomic():
        if pilots.exists() and not pilots.exclude(air_points=0, ground_points=0).exists():
            log.info("scoring existing sorties")
            _rebuild_all(cfg)
        _mark_done(BACKFILL_SCORES)


def _backfill_tours(cfg: Config) -> None:
    """Missions saved before tours existed get their tour, and the per-tour rows are built (FR-WEB-10, TD-26)."""
    from django.db import transaction

    from il2ks.db.models import Mission, PlayerBestStreak, PlayerStreak

    # Also a database from before the per-tour killboard and the best streaks: streaks exist, their best rows don't.
    old_streaks = PlayerStreak.objects.exists() and not PlayerBestStreak.objects.exists()
    if _already_done(BACKFILL_TOURS):
        needs_rebuild = False
    else:
        needs_rebuild = Mission.objects.filter(tour__isnull=True).exists() or old_streaks
        if not needs_rebuild:
            _mark_done(BACKFILL_TOURS)
    if needs_rebuild:
        log.info("assigning existing missions to tours and rebuilding the aggregates")
        with transaction.atomic():
            _rebuild_all(cfg)
            _mark_done(BACKFILL_TOURS)
    else:
        from il2ks.db.models import Player, StatThreshold
        from il2ks.ingest.stat_marks import recompute_thresholds

        # A database from before stat marks (FR-WEB-22), or before the score and Elo marks (the newest metric is the
        # marker; a site without enough pilots just recomputes its few rows at each start): build the thresholds once,
        # without waiting for a mission.
        if not StatThreshold.objects.filter(metric="air_score").exists() and Player.objects.exists():
            log.info("computing stat thresholds")
            with transaction.atomic():
                recompute_thresholds(cfg.marks)


def _backfill_achievements() -> None:
    """A database from before the medals (FR-WEB-26) has pilot sorties but no `PlayerAchievement` row: compute them once
    (medals are all-time level-2 rows; no other aggregate changes)."""
    from django.db import transaction

    from il2ks.db.models import PlayerAchievement, PlayerSortie, Role
    from il2ks.ingest.achievements import rebuild_achievements

    if _already_done(BACKFILL_ACHIEVEMENTS):
        return
    with transaction.atomic():
        if PlayerSortie.objects.filter(role=Role.PILOT).exists() and not PlayerAchievement.objects.exists():
            log.info("computing medals")
            rebuild_achievements()
        _mark_done(BACKFILL_ACHIEVEMENTS)
