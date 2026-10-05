"""Applying pending database migrations (FR-OPS-3), after a backup (FR-OPS-6, NFR-INS-4).

Every command that writes the database calls `migrate_if_needed` first. Needs Django to be set up already.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from il2ks.config import Config

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

    from django.db import models
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

    from il2ks.ingest.lock import LockBusyError, WriterLock
    from il2ks.ops.backup import backup_before_migration

    if not _pending(MigrationExecutor):
        if catalog_changed():
            try:
                with WriterLock(cfg.data_dir, command, wait=wait):
                    refresh_for_catalog_change(cfg, backup=True)
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
        _run_backfills(cfg)
        return backup


BACKFILL_TOURS = "tours"  # missions without a tour, per-tour rows, best streaks
BACKFILL_SCORES = "scores"  # sortie scores (FR-WEB-7)
BACKFILL_TOUR_ELO = "tour_elo"  # Elo per tour (reset at every tour, OQ-128)
BACKFILL_TYPE_RATINGS = "type_ratings"  # per-type Elo and the prop / jet pools (OQ-49)
BACKFILL_TYPE_KILLBOARD = "type_killboard"  # killboard by aircraft type, per-tour / intercept matchups
BACKFILL_INTERCEPTION = "interception"  # kills of bombers / attackers per sortie, the skill boards' counters
BACKFILL_ASSIST_SPLIT = "assist_split"  # assists on air vs ground victims
BACKFILL_ACCURACY = "accuracy"  # rounds fired and gun hits per sortie (from the stored ammo JSON)
BACKFILL_STREAK_RUNS = "streak_runs"  # the history of streak runs and assists received (OQ-81, OQ-82)
BACKFILL_TOUR_AIRCRAFT = "tour_aircraft"  # aircraft stats per tour (FR-WEB-8, TD-26)
BACKFILL_MOD_FILTERS = "mod_filters"  # weapon-mod sets and filter scopes of the aircraft stats (FR-WEB-8)
BACKFILL_SCOPED_AIRCRAFT = "scoped_aircraft"  # every aircraft-page section per tour, role and mod filter (FR-WEB-8)
BACKFILL_PAYLOAD_NAMES = "payload_names"  # loadout names from the stored payload ids and the current catalog table
BACKFILL_AIRCRAFT_CASE = "aircraft_case"  # `Il-10` / `IL-10` rows merged into one GameObject
BACKFILL_AIRCRAFT_ALIASES = "aircraft_aliases"  # `B 29` / `B-29` rows merged through object_aliases.csv (OQ-120)
BACKFILL_ACHIEVEMENTS = "achievements"  # medals (FR-WEB-26)
BACKFILL_BUILDS = "builds"  # the favourite loadout rows
BACKFILL_ACHIEVEMENT_TOURS = "achievement_tours"  # per-tour medals and the rarity denominators (doc 17, OQ-105)
BACKFILL_TOUR_CLEAN_SLATE = "tour_clean_slate"  # streaks and medals restart in every tour, all time rolled up (doc 17)
BACKFILL_PLAYER_ROLLUP = "player_rollup"  # per-tour identity rows; every mission has a tour (all-time rows are sums)
BACKFILL_TOUR_AIRCRAFT_SIDES = "tour_aircraft_sides"  # side counters of the tour aircraft rows (all-time side = argmax)
BACKFILL_ELO_MIN_GAMES = "elo_min_games"  # all-time Elo = best tour with at least min_elo_games games (doc 13)
BACKFILL_ACHIEVEMENT_FACTS = "achievement_facts"  # rams, first blood, multi-kills, Elo peaks (doc 17, OQ-105)


def _already_done(name: str) -> bool:
    """Whether this one-time backfill ran on this database (`SiteSettings.backfills_done`). The data triggers alone
    can't tell: with percentage penalties a legitimate database may hold only zero scores or no rated games, and would
    be rebuilt after every migration."""
    from il2ks.db.site import get_site_settings

    return name in get_site_settings().backfills_done


def _mark_done(*names: str) -> None:
    """Record that these backfills ran (or were not needed), so a later migration doesn't repeat them."""
    from il2ks.db.models import SiteSettings
    from il2ks.db.site import get_site_settings

    done = get_site_settings().backfills_done
    missing = [name for name in names if name not in done]
    if missing:
        SiteSettings.objects.filter(pk=1).update(backfills_done=[*done, *missing])


def _rebuild_all(cfg: Config) -> None:
    """The one place the backfills call `rebuild_aggregates`, so no configured section (ratings, tours, marks, score,
    killboard) can be forgotten by one of them."""
    from il2ks.ingest.aggregates import rebuild_aggregates
    from il2ks.ingest.rule_store import effective_config

    rules = effective_config(cfg)
    rebuild_aggregates(rules.ratings, rules.tours, marks=rules.marks, score=rules.score, board=rules.board)


def _run_backfills(cfg: Config, only: Sequence[str] | None = None) -> None:
    """Run the one-time data fix-ups an upgrade needs (`only` limits them to those names; tests).

    Each level-1 step fixes the stored sortie columns it is responsible for and says whether level 2 needs a rebuild;
    the rebuild then runs once for all of them (it used to run once per step, up to three times on a very old database),
    and all the markers are written in the same transaction. Only backfills not yet marked done run, and they run only
    when migrations were pending (`migrate_if_needed`): a database that is up to date never gets here."""
    from django.db import transaction

    steps: list[tuple[str, Callable[[], bool]]] = [
        (BACKFILL_TOURS, _check_tours),
        (BACKFILL_SCORES, _check_scores),
        (BACKFILL_TYPE_RATINGS, _check_type_ratings),
        (BACKFILL_TOUR_ELO, _check_tour_elo),
        (BACKFILL_TYPE_KILLBOARD, _check_type_killboard),
        (BACKFILL_INTERCEPTION, _check_interception),
        (BACKFILL_ASSIST_SPLIT, _check_assist_split),
        (BACKFILL_ACCURACY, _check_accuracy),
        (BACKFILL_STREAK_RUNS, _check_streak_runs),
        (BACKFILL_TOUR_AIRCRAFT, _check_tour_aircraft),
        (BACKFILL_BUILDS, _check_builds),
        (BACKFILL_ACHIEVEMENT_FACTS, _check_achievement_facts),
        (BACKFILL_PAYLOAD_NAMES, _check_payload_names),
        (BACKFILL_AIRCRAFT_CASE, _check_aircraft_case),
        (BACKFILL_AIRCRAFT_ALIASES, _check_aircraft_case),  # the same merge, now that the catalog knows aliases
        (BACKFILL_MOD_FILTERS, _check_mod_filters),
        (BACKFILL_SCOPED_AIRCRAFT, _check_scoped_aircraft),
        (BACKFILL_PLAYER_ROLLUP, _check_player_rollup),
        (BACKFILL_TOUR_AIRCRAFT_SIDES, _check_tour_aircraft_sides),
        (BACKFILL_ELO_MIN_GAMES, _check_elo_min_games),
    ]
    wanted = [(name, check) for name, check in steps if (only is None or name in only) and not _already_done(name)]
    with transaction.atomic():
        rebuild = False
        for _, check in wanted:  # every step runs: each one also fixes level 1
            rebuild = check() or rebuild
        stale_catalog = only is None and BACKFILL_PAYLOAD_NAMES not in (n for n, _ in wanted) and catalog_changed()
        if (
            stale_catalog
        ):  # a database from before the fingerprint, or a changed catalog: level 1 now, one rebuild below
            log.info("the catalog files changed: refreshing the stored loadout names and aircraft rows")
            rebuild = _refresh_level_1_for_catalog() or rebuild
        if rebuild:
            log.info("rebuilding the aggregates for the upgrade")
            _rebuild_all(cfg)
        elif only is None or BACKFILL_TOURS in only:
            _backfill_thresholds(cfg)
        _mark_done(*(name for name, _ in wanted))
        if only is None:
            _record_catalog_fingerprint(catalog_fingerprint())  # applied just now, with the one rebuild above
        if only is None or BACKFILL_ACHIEVEMENTS in only:
            _backfill_achievements()  # after the rebuild, which computes the medals itself
        if only is None or BACKFILL_ACHIEVEMENT_TOURS in only:
            _backfill_achievement_tours()  # after the plain medals: no-op where they were just computed with tours
        if only is None or BACKFILL_TOUR_CLEAN_SLATE in only:
            _backfill_tour_clean_slate(rebuilt=rebuild)  # after `tour_elo`'s rebuild, which also reads the Elo peaks


def _refresh_level_1_for_catalog() -> bool:
    """The level 1 part of a catalog refresh (loadout names, alias duplicates merged). Returns whether level 2 must be
    rebuilt: always when there are sorties, because a flipped `significant` flag shows nowhere in level 1."""
    from il2ks.db.models import PlayerSortie

    if not PlayerSortie.objects.exists():
        return False
    _check_payload_names()
    _check_aircraft_case()
    return True


def _check_payload_names() -> bool:
    """The loadout table was replaced (renumbered IL-10 ids, F-86A-5 now resolves, new rows): every sortie's
    `payload_name` is looked up again from its stored `payload_id` and aircraft type. `weapon_mods` needs nothing (the
    raw `WM` was always stored, names are looked up when a page is shown). Returns whether a name changed, which means
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


def _check_aircraft_case() -> bool:
    """Before this fix a type written in two cases by the logs (`Il-10` / `IL-10`) had two `GameObject` rows, so two
    aircraft pages and split stats. Every known type that has several rows keeps the one named like the catalog (or the
    oldest, renamed), the others are merged into it: level 1 rows (sorties, per-mission ammo) are repointed, level 2
    rows of the duplicate are dropped (the rebuild recreates them). Returns whether anything was merged."""
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


def _check_mod_filters() -> bool:
    """A database from before the weapon-mod tables of the aircraft page has pilot sorties but no `AircraftMods` row:
    level 2 must be rebuilt (the mod-filter scopes of the types with significant mods come with it)."""
    from il2ks.db.models import AircraftMods, PlayerSortie, Role

    return PlayerSortie.objects.filter(role=Role.PILOT).exists() and not AircraftMods.objects.exists()


def _check_scoped_aircraft() -> bool:
    """A database from before the aircraft page followed every filter has pilot sorties but no `PlayerAircraftScope`
    row: level 2 must be rebuilt (loadouts and mods per tour, scoped matchups, top pilots and hits to destroy come
    with it). The destroyed aircraft's role and mods of the hits-to-destroy rows are level 1 and need
    `il2ks reprocess --all`; until then those rows count for every role without a modification filter only."""
    from il2ks.db.models import PlayerAircraftScope, PlayerSortie, Role

    return PlayerSortie.objects.filter(role=Role.PILOT).exists() and not PlayerAircraftScope.objects.exists()


def _check_builds() -> bool:
    """A database from before the favourite loadout has pilot sorties but no `PlayerAircraftBuild` row: level 2 must be
    rebuilt."""
    from il2ks.db.models import PlayerAircraftBuild, PlayerSortie, Role

    return PlayerSortie.objects.filter(role=Role.PILOT).exists() and not PlayerAircraftBuild.objects.exists()


def _check_player_rollup() -> bool:
    """The all-time player rows are now the sum of the per-tour rows (doc 14), which needs a tour for every mission and
    the per-tour identity rows (`PlayerTourName`). A database from before has sorties but no identity row, or missions
    without a tour: level 2 must be rebuilt (which gives every mission its tour, `rebuild_aggregates`)."""
    from il2ks.db.models import Mission, PlayerSortie, PlayerTourName

    return Mission.objects.filter(tour__isnull=True).exists() or (
        PlayerSortie.objects.exists() and not PlayerTourName.objects.exists()
    )


def _check_elo_min_games() -> bool:
    """The all-time Elo is now the best final rating among the tours with at least `min_elo_games` rated games (doc 13);
    a database that stored it as the best of any tour keeps the lucky one-game tours until the ratings are recomputed.
    Once per database (the marker): level 2 must be rebuilt if there are rated games, folded into the upgrade's one
    rebuild. A database without rated games has nothing to recompute."""
    from il2ks.db.models import PlayerTourPool

    return PlayerTourPool.objects.filter(elo_games__gt=0).exists()


def _check_tour_aircraft_sides() -> bool:
    """The all-time aircraft rows are the roll-up of the tour rows (doc 14), and the all-time side is the larger of the
    summed per-side sorties of the tour rows (`TourAircraftStats.sorties_redfor` / `sorties_blufor`, migration 0065).
    A database from before has tour rows with sorties and no side counters: level 2 must be rebuilt."""
    from il2ks.db.models import TourAircraftStats

    return TourAircraftStats.objects.filter(sorties__gt=0, sorties_redfor=0, sorties_blufor=0).exists()


def _check_tour_aircraft() -> bool:
    """A database from before the aircraft stats per tour and combat role (FR-WEB-8, TD-26) has counted sorties with a
    role but no role row in `TourAircraftStats`: level 2 must be rebuilt (the loadout roles and Elo come with it)."""
    from il2ks.db.models import AircraftRole, PlayerSortie, TourAircraftStats

    has_roles = PlayerSortie.objects.filter(role="pilot", combat_role__isnull=False).exists()
    return has_roles and not TourAircraftStats.objects.exclude(role=AircraftRole.ALL).exists()


def _check_streak_runs() -> bool:
    """A database from before the streak history (OQ-82) and the assists received (OQ-81) has pilot sorties but no
    `PlayerStreakRun` row: level 2 must be rebuilt. A database with no pilot sorties returns False;
    the step is marked done either way, like every step that ran."""
    from il2ks.db.models import PlayerSortie, PlayerStreakRun, Role

    return PlayerSortie.objects.filter(role=Role.PILOT).exists() and not PlayerStreakRun.objects.exists()


def _close(a: tuple[float, float, float] | None, b: tuple[float, float, float] | None, metres: float) -> bool:
    """Whether two kill positions are within `metres` of each other (unknown positions are not held against a ram)."""
    if a is None or b is None:
        return True
    return sum((p - q) ** 2 for p, q in zip(a, b, strict=True)) <= metres**2


def _hits_dealt(pairs: list[tuple[int, int]]) -> dict[int, set[int]]:
    """Sortie id -> the player sorties it landed gun hits on, read from the stored damage breakdown, for the sorties
    in `pairs` only (the JSON is big)."""
    from il2ks.db.models import PlayerSortie

    ids = sorted({pk for pair in pairs for pk in pair})
    hit: dict[int, set[int]] = {}
    for start in range(0, len(ids), 400):
        rows = PlayerSortie.objects.filter(pk__in=ids[start : start + 400]).values_list("pk", "damage_breakdown")
        for pk, breakdown in rows:
            for entry in cast("list[dict[str, object]]", breakdown or []):
                other, dealt = entry.get("counterpart"), entry.get("hits_dealt")
                if isinstance(other, dict) and isinstance(dealt, int) and dealt > 0:
                    target = cast("dict[str, object]", other).get("sortie_id")
                    if isinstance(target, int):
                        hit.setdefault(pk, set()).add(target)
    return hit


def _check_achievement_facts() -> bool:
    """A database from before the second set of achievements (doc 17) has no `rams`, `first_blood`, `multi_kill` or
    `elo_peak` on its sorties. Derived from what is stored; the rebuild this asks for replays the Elo games (the peaks)
    and computes every medal:

    - `multi_kill`: the timeline's air `kill` entries (a victim that is a player's aircraft, or of an air class), the
      replay's own window (`max_burst`).
    - `first_blood`: the first PvP air kill of each mission in `Kill` (earliest tick, then id); AI victims have no row,
      which is the rule (doc 17).
    - `rams`: only approximated. The log has no collision event and the timelines do not mark rams, so a kill counts as
      a ram when its victim killed the killer back within the ram window (`[rules] ram_window_s`): two enemies that
      credit each other at the same moment and place (`ram_distance_m`). `il2ks reprocess` gives the exact value.
    """
    from il2ks.core.catalog.loader import AIR_CLASSES
    from il2ks.core.replay.kills import max_burst
    from il2ks.core.replay.toggles import RuleToggles
    from il2ks.db.models import GameObject, Kill, KillCredit, PlayerSortie, Role
    from il2ks.ingest.dbutil import update_partial_rows

    pilots = PlayerSortie.objects.filter(role=Role.PILOT)
    if not pilots.exists() or pilots.filter(first_blood=True).exists() or pilots.filter(multi_kill__gt=0).exists():
        return False
    log.info("deriving rams, first bloods and multi-kills")
    classes = dict(GameObject.objects.values_list("log_name", "cls"))
    bursts: dict[int, int] = {}
    for pk, timeline in pilots.filter(kills_air__gt=0).values_list("pk", "timeline").iterator(chunk_size=500):
        ticks: list[int] = []
        for entry in timeline:
            other = entry.get("counterpart")
            tick = entry.get("tick")
            if entry.get("kind") != "kill" or not isinstance(other, dict) or not isinstance(tick, int):
                continue
            victim = cast("dict[str, object]", other)
            if victim.get("sortie_id") is not None or classes.get(str(victim.get("object_type"))) in AIR_CLASSES:
                ticks.append(tick)
        if ticks:
            bursts[pk] = max_burst(ticks)

    first: dict[int, int] = {}
    mutual: dict[tuple[int, int], tuple[datetime, tuple[float, float, float] | None]] = {}
    toggles = RuleToggles()
    window = timedelta(seconds=toggles.ram_window_s)
    kills = Kill.objects.filter(credit=KillCredit.KILL, is_friendly=False).order_by("mission_id", "tick", "pk")
    rams: dict[int, int] = {}
    pairs: list[tuple[int, int]] = []
    for mission_id, killer, killer_role, victim, when, x, y, z in kills.values_list(
        "mission_id", "killer_sortie_id", "killer_sortie__role", "victim_sortie_id", "time", "pos_x", "pos_y", "pos_z"
    ).iterator():
        if killer_role == Role.PILOT:  # the replay's rule: gunners never draw first blood (`first_blood_sortie`)
            first.setdefault(mission_id, killer)
        where = (x, y, z) if x is not None and y is not None and z is not None else None
        back = mutual.get((victim, killer))
        if back is not None and abs(when - back[0]) <= window and _close(where, back[1], toggles.ram_distance_m):
            pairs.append((killer, victim))
        mutual[(killer, victim)] = (when, where)
    shots = _hits_dealt(pairs)
    for killer, victim in pairs:
        if victim not in shots.get(killer, ()) and killer not in shots.get(victim, ()):  # a ram: no guns either way
            rams[killer] = rams.get(killer, 0) + 1
            rams[victim] = rams.get(victim, 0) + 1

    first_ids = set(first.values())
    changed = [
        PlayerSortie(pk=pk, multi_kill=bursts.get(pk, 0), rams=rams.get(pk, 0), first_blood=pk in first_ids)
        for pk in sorted(bursts.keys() | rams.keys() | first_ids)
    ]
    update_partial_rows(PlayerSortie, changed, ["multi_kill", "rams", "first_blood"])
    return True


def _check_accuracy() -> bool:
    """A database from before accuracy has no `rounds_fired`, `gun_hits_air` or `gun_hits_ground` on its sorties. They
    follow from the stored JSON (`accuracy_from_stored`): rounds from the ammo `used` counts (unknown where "left"
    cannot be trusted, FR-ING-24), gun hits from the per-ammo hit counts split by target through the damage breakdown.
    Only the needed columns are read (streamed) and only the three columns written. Returns whether level 2 must be
    rebuilt. `il2ks reprocess` gives the exact values."""
    from django.db.models import Q

    from il2ks.core.catalog.loader import AIR_CLASSES
    from il2ks.db.models import GameObject, PlayerSortie, Role
    from il2ks.ingest.dbutil import update_partial_rows

    pilots = PlayerSortie.objects.filter(role=Role.PILOT)
    if (
        not pilots.exists()
        or pilots.filter(rounds_fired__isnull=False).exists()
        or pilots.filter(Q(gun_hits_air__gt=0) | Q(gun_hits_ground__gt=0)).exists()
    ):
        return False
    log.info("deriving accuracy figures from the stored ammo")
    air_types = {name for name, cls in GameObject.objects.values_list("log_name", "cls") if cls in AIR_CLASSES}
    changed: list[PlayerSortie] = []
    rows = pilots.values_list(
        "pk", "ammo", "damage_breakdown", "resupplied", "rounds_fired", "gun_hits_air", "gun_hits_ground"
    ).iterator(chunk_size=500)
    for pk, ammo, breakdown, resupplied, *stored in rows:
        rounds, air, ground = accuracy_from_stored(ammo or {}, breakdown or [], air_types, resupplied=resupplied)
        if (rounds, air, ground) == tuple(stored):
            continue  # nothing to write (most rows stay (None, 0, 0))
        changed.append(PlayerSortie(pk=pk, rounds_fired=rounds, gun_hits_air=air, gun_hits_ground=ground))
    update_partial_rows(PlayerSortie, changed, ["rounds_fired", "gun_hits_air", "gun_hits_ground"])
    return True


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


def _check_assist_split() -> bool:
    """A database from before assists were split has `assists` but no `assists_air` / `assists_ground`. The timeline's
    `assist` entries name the victim (a player sortie, or the object type), so air and ground are derived from them with
    the replay's own rule (a victim is air when it is a player's aircraft or its catalog class is an air class); the
    remainder of `assists` (a timeline that lost entries) counts as ground. Returns whether level 2 must be rebuilt,
    which also rescores (ground assists no longer score). `il2ks reprocess` gives the same."""
    from il2ks.core.catalog.loader import AIR_CLASSES
    from il2ks.db.models import GameObject, PlayerSortie
    from il2ks.ingest.dbutil import update_partial_rows

    sorties = PlayerSortie.objects.filter(assists__gt=0)
    if (
        not sorties.exists()
        or sorties.filter(assists_air__gt=0).exists()
        or sorties.filter(assists_ground__gt=0).exists()
    ):
        return False
    log.info("splitting assists into air and ground")
    classes = dict(GameObject.objects.values_list("log_name", "cls"))
    changed: list[PlayerSortie] = []
    # Only the needed columns are loaded, streamed in chunks (timelines are big), and only pk + the two columns written.
    for pk, assists, timeline in sorties.values_list("pk", "assists", "timeline").iterator(chunk_size=500):
        air = 0
        for entry in timeline:
            other = entry.get("counterpart")
            if entry.get("kind") != "assist" or not isinstance(other, dict):
                continue
            victim = cast("dict[str, object]", other)
            air += victim.get("sortie_id") is not None or classes.get(str(victim.get("object_type"))) in AIR_CLASSES
        air = min(air, assists)
        changed.append(PlayerSortie(pk=pk, assists_air=air, assists_ground=assists - air))
    update_partial_rows(PlayerSortie, changed, ["assists_air", "assists_ground"])
    return True


def _check_interception() -> bool:
    """A database from before the interception board has no `PlayerSortie.kills_air_intercept`. The sortie timelines
    name every kill's victim type (and the victim's sortie), so the column is derived from them (the same rule as the
    replay, `is_interception_victim`); level 2 must then be rebuilt (FR-WEB-7). `il2ks reprocess` gives the same."""
    from il2ks.core.replay.attack import is_interception_victim
    from il2ks.db.models import GameObject, PlayerSortie
    from il2ks.ingest.dbutil import update_partial_rows

    sorties = PlayerSortie.objects.filter(kills_air__gt=0)
    if not PlayerSortie.objects.exists() or sorties.filter(kills_air_intercept__gt=0).exists():
        return False
    log.info("counting kills of bombers, attackers and transports")
    classes = dict(GameObject.objects.values_list("log_name", "cls"))
    roles = dict(PlayerSortie.objects.exclude(combat_role=None).values_list("pk", "combat_role"))
    changed: list[PlayerSortie] = []
    for pk, timeline in sorties.values_list("pk", "timeline").iterator(chunk_size=500):
        count = 0
        for entry in timeline:
            other = entry.get("counterpart")
            if entry.get("kind") != "kill" or not isinstance(other, dict):
                continue
            victim = cast("dict[str, object]", other)
            victim_id = victim.get("sortie_id")
            victim_role = roles.get(victim_id) if isinstance(victim_id, int) else None
            count += is_interception_victim(classes.get(str(victim.get("object_type"))), victim_role)
        if count:
            changed.append(PlayerSortie(pk=pk, kills_air_intercept=count))
    update_partial_rows(PlayerSortie, changed, ["kills_air_intercept"])
    return True


def _check_type_killboard() -> bool:
    """A database from before the killboard by aircraft type and the per-tour / intercept matchups has matchup rows
    (all-time only) but no `PlayerTypeKillboard` row (FR-WEB-8, FR-WEB-9): level 2 must be rebuilt."""
    from il2ks.db.models import AircraftMatchup, PlayerTypeKillboard

    return AircraftMatchup.objects.exists() and not PlayerTypeKillboard.objects.exists()


def _check_type_ratings() -> bool:
    """A database from before the per-type Elo (OQ-49) and the prop / jet pools has no rated games in any
    `PlayerAircraft` row and no `PlayerPool` rows: level 2 must be rebuilt, which also applies the current `[score]`
    rules (the penalty rules changed in the same release)."""
    from il2ks.db.models import PlayerAircraft, PlayerPool

    rows = PlayerAircraft.objects.all()
    return rows.exists() and not (rows.filter(elo_games__gt=0).exists() or PlayerPool.objects.exists())


def _check_tour_elo() -> bool:
    """A database from before the Elo per tour (OQ-128) has rated games on `Player` but none on the per-tour pool rows:
    level 2 must be rebuilt, which replays every tour alone and re-derives the all-time Elo from them."""
    from django.db.models import Q

    from il2ks.db.models import Player, PlayerTourPool

    rated = Q(elo_prop_games__gt=0) | Q(elo_jet_games__gt=0)
    return Player.objects.filter(rated).exists() and not PlayerTourPool.objects.filter(elo_games__gt=0).exists()


def _check_scores() -> bool:
    """Sorties saved before the score existed have none: when there are pilot sorties and none has a score, level 2
    must be rebuilt, which computes every score from the stored columns (FR-WEB-7). Rule changes: `il2ks
    rebuild-aggregates`."""
    from il2ks.db.models import PlayerSortie, Role

    pilots = PlayerSortie.objects.filter(role=Role.PILOT)
    return pilots.exists() and not pilots.exclude(air_points=0, ground_points=0).exists()


def _check_tours() -> bool:
    """Missions saved before tours existed get their tour, and the per-tour rows are built (FR-WEB-10, TD-26), by the
    rebuild. Also a database from before the per-tour killboard and the best streaks: streaks exist, their best rows
    don't."""
    from il2ks.db.models import Mission, PlayerBestStreak, PlayerStreak

    old_streaks = PlayerStreak.objects.exists() and not PlayerBestStreak.objects.exists()
    return Mission.objects.filter(tour__isnull=True).exists() or old_streaks


def _backfill_thresholds(cfg: Config) -> None:
    """A database from before stat marks (FR-WEB-22), or before the score and Elo marks (the newest metric is the
    marker): build the thresholds once, without waiting for a mission. Backfills only run when migrations are pending
    (`migrate_if_needed`), so a site too small to fill the thresholds does not recompute them at each start."""
    from django.db import transaction

    from il2ks.db.models import Player, StatThreshold
    from il2ks.ingest.rule_store import effective_config
    from il2ks.ingest.stat_marks import recompute_thresholds

    if not StatThreshold.objects.filter(metric="air_score").exists() and Player.objects.exists():
        log.info("computing stat thresholds")
        with transaction.atomic():
            recompute_thresholds(effective_config(cfg).marks)


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


def _backfill_tour_clean_slate(*, rebuilt: bool = False) -> None:
    """Streaks and medals used to run over a player's whole history for the all-time rows; since a new tour is a clean
    slate the all-time rows are rolled up from the tour rows (doc 17). Recompute them once for every player, unless this
    upgrade just rebuilt level 2 (`rebuilt`): the rebuild replays each tour's Elo first and then computes the streaks
    and medals per tour, the same result."""
    from django.db import transaction

    from il2ks.db.models import Player, PlayerSortie, Role
    from il2ks.ingest.achievements import rebuild_achievements
    from il2ks.ingest.aggregates import CHUNK
    from il2ks.ingest.streaks import recompute_streaks

    if _already_done(BACKFILL_TOUR_CLEAN_SLATE):
        return
    with transaction.atomic():
        if not rebuilt and PlayerSortie.objects.filter(role=Role.PILOT).exists():
            log.info("recomputing streaks and medals per tour")
            ids = sorted(Player.objects.values_list("pk", flat=True))
            for start in range(0, len(ids), CHUNK):
                recompute_streaks(ids[start : start + CHUNK])
            rebuild_achievements()
        _mark_done(BACKFILL_TOUR_CLEAN_SLATE)


def _backfill_achievement_tours() -> None:
    """A database from before per-tour medals (doc 17) has all-time medal rows only (and holder rows without the rarity
    denominator): run the medal pass again, which adds the tour rows and rewrites the holder counts. Skipped when tour
    rows exist already (the plain backfill above just computed everything)."""
    from django.db import transaction

    from il2ks.db.models import PlayerAchievement, PlayerSortie, Role
    from il2ks.ingest.achievements import rebuild_achievements

    if _already_done(BACKFILL_ACHIEVEMENT_TOURS):
        return
    with transaction.atomic():
        if (
            PlayerSortie.objects.filter(role=Role.PILOT).exists()
            and not PlayerAchievement.objects.filter(tour__isnull=False).exists()
        ):
            log.info("computing medals per tour")
            rebuild_achievements()
        _mark_done(BACKFILL_ACHIEVEMENT_TOURS)


CATALOG_FILES = ("weapon_mods.csv", "payloads.csv", "payload_aliases.csv", "object_aliases.csv")
"""The catalog files whose content is copied into stored rows: significant mods (the filter scopes of level 2), loadout
names (`PlayerSortie.payload_name`), and the log-name aliases (merged `GameObject` rows)."""

CATALOG_MARKER = "catalog:"
"""Prefix of the entry in `SiteSettings.backfills_done` that holds the fingerprint of `CATALOG_FILES` last applied."""


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


def _stored_catalog_fingerprint() -> str | None:
    from il2ks.db.site import get_site_settings

    return next(
        (m.removeprefix(CATALOG_MARKER) for m in get_site_settings().backfills_done if m.startswith(CATALOG_MARKER)),
        None,
    )


def _record_catalog_fingerprint(fingerprint: str) -> None:
    from il2ks.db.models import SiteSettings
    from il2ks.db.site import get_site_settings

    done = [m for m in get_site_settings().backfills_done if not m.startswith(CATALOG_MARKER)]
    SiteSettings.objects.filter(pk=1).update(backfills_done=[*done, CATALOG_MARKER + fingerprint])


def catalog_changed() -> bool:
    """Whether the shipped catalog files differ from what the stored rows were last brought in line with. A read of
    one row and four small files: cheap enough for every start."""
    return _stored_catalog_fingerprint() != catalog_fingerprint()


def refresh_for_catalog_change(cfg: Config, *, backup: bool = False) -> bool:
    """Apply a changed `weapon_mods.csv` / `payloads.csv` / `payload_aliases.csv` / `object_aliases.csv` to the stored
    rows: loadout names are looked up again, new alias duplicates merged, and level 2 rebuilt once (the significant
    mods and so the filter scopes come from the catalog). The one-time backfills never ran again after a catalog
    update. Nothing happens when the fingerprint is unchanged; an empty database only records it. With `backup`, a
    database that holds sorties is backed up first (the refresh merges and deletes level 1 rows). Returns whether
    anything was refreshed. The caller holds the writer lock."""
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
