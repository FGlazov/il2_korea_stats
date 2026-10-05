"""Achievements / medals (`PlayerAchievement`, FR-WEB-26): level 2, per player, in the same pass as the ironman streaks
(`recompute_players`).

A new tour is a clean slate (maintainer, 2026-10-05): every medal starts over in each tour. The rules are
`il2ks.core.achievements`; two steps, both per chunk of players:

1. `refresh_achievement_tours(chunk, tour_ids, rules=)`: run the definitions over each tour's counted sorties alone
   (chronological: spawn time, then id; plus the one fact the sortie row does not hold, bombers, attackers and
   transports shot down, from the `Kill` rows) and rewrite the per-tour rows of `tour_ids` (None = every tour).
2. `rollup_achievements(chunk, rules=)`: the all-time rows (`tour` null) come from the tour rows (`[PROPOSED]`):
   - a medal that is not `cumulative`: the all-time tier is the **max over the tours**; the row of each tier is the
     earliest one earned in any tour (`earned_at`, sortie and mission are those of that tour's row);
   - a `cumulative` medal (a pure running total: career kills, tank kills, flight hours, the shame counters): the
     all-time total is the **sum of the tours' totals** (`PlayerTour` counters, which `recompute_players` refreshes
     first), so career tiers stay reachable. A tier is earned in the first tour whose running sum crosses the
     threshold, at the sortie where it does: that one tour's sorties are replayed with the earlier tours' total carried
     in (`earn(carried_in=, all_time=True)`). The tiers are `ALL_TIME_FACTOR` (5) times the per-tour thresholds
     (`Achievement.all_time_thresholds`, OQ-128). Setting `cumulative=False` in the registry turns a medal into a plain
     max over tours;
   - an `all_time_only` medal (tours in a row) has no tour rows: the tours the pilot flew in (a `PlayerTour` row with
     sorties; the tours table gives the order, so a tour nobody flew in is a gap) give the longest run, and a tier is
     earned at the first sortie of the tour whose run reached it. Only `PlayerTour` and the completing tours' sorties
     are read, never the whole history.

A player with no tier has no row. `recompute_holders` then rewrites the per-scope, per-tier holder counts and the pilot
count (the rarity denominator) that the overview page and every medal's hover text show (visible players only);
population-level, unaffected by all this.
"""

import logging
from collections import Counter
from collections.abc import Iterable, Sequence

from django.db import transaction
from django.db.models import Count, F

from il2ks.config import Config
from il2ks.core.achievement_rules import Rules
from il2ks.core.achievements import Achievement, AchievementSortie, earn, earn_all, earn_tours
from il2ks.db.models import (
    AchievementHolders,
    Kill,
    KillCredit,
    ObjectClass,
    Outcome,
    Player,
    PlayerAchievement,
    PlayerTour,
    Role,
    SiteSettings,
    Tour,
)
from il2ks.db.site import bump_data_version
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import update_rows
from il2ks.ingest.lock import LockBusyError, WriterLock

log = logging.getLogger(__name__)

CHUNK = 400  # players per batch: stays far below SQLite's bound-parameter limit
STRIKE_CLASSES = (ObjectClass.BOMBER, ObjectClass.ATTACKER, ObjectClass.TRANSPORT)
_FIELDS = ("earned_at", "sortie_id", "mission_id")

type _Key = tuple[int, int | None, str, int]  # player, tour (None = all time), achievement key, tier
type _Value = tuple[object, int, int]  # earned_at, sortie, mission


def _strike_kills(chunk: list[int], tours: set[int] | None) -> Counter[int]:
    """Per killer sortie id: credited kills of other pilots' bombers, attackers and transports (not friendly, not own
    aircraft)."""
    kills = (
        Kill.objects.filter(
            killer_sortie__player_id__in=chunk,
            credit=KillCredit.KILL,
            is_friendly=False,
            killer_sortie__role=Role.PILOT,
            victim_sortie__role=Role.PILOT,
            victim_sortie__aircraft__cls__in=STRIKE_CLASSES,
        )
        .exclude(killer_sortie__player_id=F("victim_sortie__player_id"))
        .values_list("killer_sortie_id", flat=True)
    )
    if tours is not None:
        kills = kills.filter(killer_sortie__mission__tour_id__in=tours)
    return Counter(kills.iterator())


def _load(chunk: list[int], tours: set[int] | None) -> dict[tuple[int, int], list[AchievementSortie]]:
    """The counted sorties of each player in `chunk` per tour (of their mission; sorties outside every tour are left
    out), in chronological order (spawn time, then id). `tours` None = every tour."""
    strike = _strike_kills(chunk, tours)
    rows = (
        counted_sorties()
        .filter(player_id__in=chunk, mission__tour_id__isnull=False)
        .order_by("player_id", "spawned_at", "pk")
        .values(
            "player_id",
            "pk",
            "mission_id",
            "mission__tour_id",
            "spawned_at",
            "ended_at",
            "aircraft_id",
            "flight_time_s",
            "kills_air",
            "kills_ground",
            "kills_ground_tank",
            "damage_taken",
            "outcome",
            "is_death",
            "is_captured",
            "ground_points",
            "elo_peak",
            "rams",
            "first_blood",
            "multi_kill",
            "taxi_accident",
            "strafed_on_ground",
            "friendly_kills",
            "ended_by_mission_end",
            "took_off_at",
        )
    )
    if tours is not None:
        rows = rows.filter(mission__tour_id__in=tours)
    found: dict[tuple[int, int], list[AchievementSortie]] = {}
    for r in rows.iterator():
        outcome = r["outcome"]
        found.setdefault((r["player_id"], r["mission__tour_id"]), []).append(
            AchievementSortie(
                sortie_id=r["pk"],
                mission_id=r["mission_id"],
                spawned_at=r["spawned_at"],
                ended_at=r["ended_at"],
                aircraft_id=r["aircraft_id"],
                flight_time_s=r["flight_time_s"],
                kills_air=r["kills_air"],
                kills_ground=r["kills_ground"],
                kills_ground_tank=r["kills_ground_tank"],
                kills_strike_air=strike[r["pk"]],
                damage_taken=r["damage_taken"],
                landed=outcome == Outcome.LANDED,
                is_death=r["is_death"],
                is_captured=r["is_captured"],
                not_taken_off=outcome == Outcome.NOT_TAKEN_OFF,
                ground_points=r["ground_points"],
                elo_peak=r["elo_peak"],
                rams=r["rams"],
                first_blood=r["first_blood"],
                multi_kill=r["multi_kill"],
                taxi_accident=r["taxi_accident"],
                strafed_on_ground=r["strafed_on_ground"],
                friendly_kills=r["friendly_kills"],
                crashed=outcome == Outcome.CRASHED
                and r["took_off_at"] is not None
                and not (r["taxi_accident"] or r["strafed_on_ground"]),
                ended_by_mission_end=r["ended_by_mission_end"],
            )
        )
    return found


def recompute_achievements(
    chunk: list[int], tour_ids: Iterable[int] | None = None, *, rules: Rules | None = None
) -> None:
    """Refresh the per-tour medals of every player in `chunk` for `tour_ids` (None = every tour of these players, as a
    rebuild does), then roll up their all-time medals. `rules`: the admin's thresholds and switches
    (`core.achievement_rules`); the default are the *applied* ones, so an incremental update stays consistent with the
    rows already stored until the pending change is recomputed in full (`recompute_with_wanted_rules`)."""
    active = (applied_rules() if rules is None else rules).active()
    refresh_achievement_tours(chunk, tour_ids, active)
    rollup_achievements(chunk, active)


def refresh_achievement_tours(
    chunk: list[int], tour_ids: Iterable[int] | None = None, achievements: Sequence[Achievement] | None = None
) -> None:
    """Make the per-tour medal rows of these players in `tour_ids` (None = all tours) equal what their sorties of the
    tour say. `achievements`: the switched-on definitions (default: the applied rules')."""
    active = applied_rules().active() if achievements is None else achievements
    tours = None if tour_ids is None else set(tour_ids)
    wanted: dict[_Key, _Value] = {}
    for (pid, tour_id), sorties in _load(chunk, tours).items():
        for e in earn_all(sorties, achievements=active):
            s = sorties[e.index]
            wanted[(pid, tour_id, e.key, e.tier)] = (s.ended_at, s.sortie_id, s.mission_id)
    _sync(chunk, tours, wanted, all_time=False)


def rollup_achievements(chunk: list[int], achievements: Sequence[Achievement] | None = None) -> None:
    """Make the all-time medal rows of these players follow their per-tour rows (see the module docstring)."""
    active = {a.key: a for a in (applied_rules().active() if achievements is None else achievements)}
    rows = PlayerAchievement.objects.filter(player_id__in=chunk, tour__isnull=False, key__in=list(active))
    wanted: dict[_Key, _Value] = {}
    for row in rows.order_by("earned_at", "sortie_id"):
        key = (row.player_id, None, row.key, row.tier)
        if key not in wanted and not active[row.key].cumulative:  # the earliest of the tours holding the tier
            wanted[key] = (row.earned_at, row.sortie_id, row.mission_id)
    wanted.update(_cumulative_rows(chunk, [a for a in active.values() if a.cumulative]))
    wanted.update(_tour_run_rows(chunk, [a for a in active.values() if a.all_time_only]))
    _sync(chunk, None, wanted, all_time=True)


def _tour_order() -> list[int]:
    """Every tour id, oldest first (by start, then id)."""
    return list(Tour.objects.order_by("started_at", "pk").values_list("pk", flat=True))


def _cumulative_rows(chunk: list[int], cumulative: list[Achievement]) -> dict[_Key, _Value]:
    """The all-time rows of the cumulative medals: per player and medal the running sum of the tours' totals (the
    `PlayerTour` counters, in tour order) against the all-time thresholds (`ALL_TIME_FACTOR` times the per-tour ones);
    a tier is earned in the first tour whose sum reaches the threshold, at the sortie where the carried-in total plus
    the tour's own running total does (the tour's sorties are replayed: the per-tour rows have other thresholds)."""
    if not cumulative:
        return {}
    order = {pk: n for n, pk in enumerate(_tour_order())}
    fields = sorted({a.counter for a in cumulative})
    totals: dict[int, list[tuple[int, dict[str, float]]]] = {}
    for row in PlayerTour.objects.filter(player_id__in=chunk).values("player_id", "tour_id", *fields):
        totals.setdefault(row["player_id"], []).append((row["tour_id"], {f: float(row[f]) for f in fields}))
    # (player, tour) -> [(achievement, carried-in total, tiers the running sum reaches in this tour)]
    replay: dict[tuple[int, int], list[tuple[Achievement, float, list[int]]]] = {}
    for pid, per_tour in totals.items():
        per_tour.sort(key=lambda t: order.get(t[0], 0))
        for a in cumulative:
            scale = 3600.0 if a.unit == "hours" else 1.0
            thresholds = a.all_time_thresholds
            carried = 0.0
            tier = 0
            for tour_id, counters in per_tour:
                total = counters[a.counter] / scale
                reached = [n for n in range(tier + 1, a.top_tier + 1) if carried + total >= thresholds[n - 1]]
                if reached:
                    tier = reached[-1]
                    replay.setdefault((pid, tour_id), []).append((a, carried, reached))
                carried += total
    wanted: dict[_Key, _Value] = {}
    if replay:
        loaded = _load(chunk, {t for _, t in replay})
        for (pid, tour_id), todo in replay.items():
            sorties = loaded.get((pid, tour_id), [])
            for a, carried, reached in todo:
                found = {e.tier: e.index for e in earn(a, sorties, carried, all_time=True)}
                for n in reached:
                    if sorties:  # a tier the replay misses by a rounding hair: the tour's last sortie
                        s = sorties[found.get(n, len(sorties) - 1)]
                        wanted[(pid, None, a.key, n)] = (s.ended_at, s.sortie_id, s.mission_id)
    return wanted


def _tour_run_rows(chunk: list[int], runs: list[Achievement]) -> dict[_Key, _Value]:
    """The all-time rows of the tours-in-a-row medals: a tour counts when the pilot flew in it (a `PlayerTour` row with
    `takeoffs > 0`: a sortie that never took off is not a sortie flown); a tour in between with none breaks the run.
    A tier is earned at the pilot's first sortie of the tour whose run reached it (the first that took off)."""
    if not runs:
        return {}
    order = _tour_order()
    flown: dict[int, set[int]] = {}
    played_rows = PlayerTour.objects.filter(player_id__in=chunk, takeoffs__gt=0).values_list("player_id", "tour_id")
    for pid, tour_id in played_rows:
        flown.setdefault(pid, set()).add(tour_id)
    completing: dict[tuple[int, int], list[tuple[str, int]]] = {}  # (player, tour) -> [(key, tier)]
    for pid, tour_ids in flown.items():
        played = [t in tour_ids for t in order]
        for a in runs:
            for e in earn_tours(a, played):
                completing.setdefault((pid, order[e.index]), []).append((a.key, e.tier))
    if not completing:
        return {}
    first: dict[tuple[int, int], _Value] = {}
    fallback: dict[tuple[int, int], _Value] = {}
    sorties = (
        counted_sorties()
        .filter(player_id__in={p for p, _ in completing}, mission__tour_id__in={t for _, t in completing})
        .order_by("spawned_at", "pk")
        .values_list("player_id", "mission__tour_id", "ended_at", "pk", "mission_id", "outcome")
    )
    for pid, tour_id, ended_at, sortie_id, mission_id, outcome in sorties.iterator():
        pair = (pid, tour_id)
        if pair not in completing:
            continue
        value = (ended_at, sortie_id, mission_id)
        fallback.setdefault(pair, value)
        if outcome != Outcome.NOT_TAKEN_OFF:
            first.setdefault(pair, value)
    wanted: dict[_Key, _Value] = {}
    for pair, earned in completing.items():
        value = first.get(pair) or fallback.get(pair)
        if value is None:  # the tour's sorties are gone (cannot happen: the counters are summed from them)
            continue
        for key, tier in earned:
            wanted[(pair[0], None, key, tier)] = value
    return wanted


def _sync(chunk: list[int], tours: set[int] | None, wanted: dict[_Key, _Value], *, all_time: bool) -> None:
    """Make the chunk's rows of one scope equal `wanted`: the all-time rows (`all_time`), else those of `tours` (None =
    every tour)."""
    rows = PlayerAchievement.objects.filter(player_id__in=chunk)
    if all_time:
        rows = rows.filter(tour__isnull=True)
    else:
        rows = rows.filter(tour__isnull=False)
        if tours is not None:
            rows = rows.filter(tour_id__in=tours)
    existing = {(r.player_id, r.tour_id, r.key, r.tier): r for r in rows}
    changed: list[PlayerAchievement] = []
    new: list[PlayerAchievement] = []
    for (pid, tour_id, key, tier), values in wanted.items():
        row = existing.pop((pid, tour_id, key, tier), None)
        if row is None:
            new.append(
                PlayerAchievement(
                    player_id=pid, tour_id=tour_id, key=key, tier=tier, **dict(zip(_FIELDS, values, strict=True))
                )
            )
        elif tuple(getattr(row, f) for f in _FIELDS) != values:
            for field, value in zip(_FIELDS, values, strict=True):
                setattr(row, field, value)
            changed.append(row)
    PlayerAchievement.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(PlayerAchievement, changed, list(_FIELDS))
    PlayerAchievement.objects.bulk_create(new)


def recompute_holders() -> None:
    """Rewrite the holder counts: per scope (all time, each tour) and achievement tier, the visible players who hold it
    (FR-ADM-3), and per scope the number of visible pilots with a sortie in it (the rarity denominator)."""
    pilots: dict[int | None, int] = {None: Player.objects.visible().filter(sorties__gt=0).count()}
    pilots.update(
        {
            row["tour_id"]: row["n"]
            for row in PlayerTour.objects.filter(player__is_hidden=False, sorties__gt=0)
            .values("tour_id")
            .annotate(n=Count("pk"))
            .order_by()
        }
    )
    counts: dict[tuple[int | None, str, int], int] = {
        (row["tour_id"], row["key"], row["tier"]): row["n"]
        for row in PlayerAchievement.objects.filter(player__is_hidden=False)
        .values("tour_id", "key", "tier")
        .annotate(n=Count("pk"))
        .order_by()
    }
    existing = {(r.tour_id, r.key, r.tier): r for r in AchievementHolders.objects.all()}
    changed: list[AchievementHolders] = []
    new: list[AchievementHolders] = []
    for (tour_id, key, tier), holders in counts.items():
        scope_pilots = pilots.get(tour_id, 0)
        row = existing.pop((tour_id, key, tier), None)
        if row is None:
            new.append(AchievementHolders(tour_id=tour_id, key=key, tier=tier, holders=holders, pilots=scope_pilots))
        elif (row.holders, row.pilots) != (holders, scope_pilots):
            row.holders = holders
            row.pilots = scope_pilots
            changed.append(row)
    AchievementHolders.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(AchievementHolders, changed, ["holders", "pilots"])
    AchievementHolders.objects.bulk_create(new)


def rebuild_achievements(rules: Rules | None = None) -> None:
    """Recompute every player's medals and the holder counts (the upgrade backfill; `rebuild_aggregates` does the same
    through `recompute_players`), with `rules` (default: the applied ones)."""
    ids = sorted(Player.objects.values_list("pk", flat=True))
    for start in range(0, len(ids), CHUNK):
        recompute_achievements(ids[start : start + CHUNK], rules=rules)
    recompute_holders()


def applied_rules() -> Rules:
    """The rules the stored rows were computed with (`SiteSettings.achievements_applied`; none = the built-in set)."""
    raw = SiteSettings.objects.filter(pk=1).values_list("achievements_applied", flat=True).first()
    return Rules.from_json(raw)


def wanted_rules() -> Rules:
    """The rules the admin chose (`SiteSettings.achievements`): what the next recompute applies."""
    raw = SiteSettings.objects.filter(pk=1).values_list("achievements", flat=True).first()
    return Rules.from_json(raw)


def recompute_pending() -> bool:
    """The admin changed a threshold or switched an achievement on or off since the last recompute."""
    return wanted_rules() != applied_rules()


def adopt_wanted_rules() -> Rules:
    """Make the wanted rules the applied ones, for a full rebuild that computes every row with them anyway."""
    rules = wanted_rules()
    SiteSettings.objects.filter(pk=1).update(achievements_applied=rules.to_json())
    return rules


def recompute_with_wanted_rules(cfg: Config) -> bool:
    """Work off a pending change of the admin's achievement rules (`watch` calls this every tick): under the writer lock
    recompute every player's rows with the wanted rules and the holder counts, then record them as applied and bump
    the data version. False when nothing was pending or the lock was busy (the next tick tries again). All in one
    transaction (only the changed rows are written, so it stays small; readers keep the old snapshot until the
    commit): a crash midway rolls back, leaves the change pending, and the next tick starts over."""
    if not recompute_pending():
        return False
    try:
        with WriterLock(cfg.data_dir, "achievements"):
            rules = wanted_rules()
            log.info("recomputing achievements with the changed rules")
            with transaction.atomic():  # one commit: pages and a crash see old rows with old rules, or new with new
                rebuild_achievements(rules)
                SiteSettings.objects.filter(pk=1).update(achievements_applied=rules.to_json())
                bump_data_version()
    except LockBusyError as exc:
        log.info("achievement recompute waits: %s", exc)
        return False
    return True
