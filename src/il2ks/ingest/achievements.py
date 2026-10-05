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
from il2ks.core.streaks import Track, track_of
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
from il2ks.ingest.dbutil import delete_pks, sync_rows, update_rows
from il2ks.ingest.lock import LockBusyError, WriterLock

log = logging.getLogger(__name__)

CHUNK = 2000  # players per batch: far below the bound-parameter limit (32766 on SQLite 3.32+); fewer, bigger queries
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
        kills = kills.filter(killer_sortie__tour_id__in=tours)
    return Counter(kills.iterator())


def _load(chunk: list[int], tours: set[int] | None) -> dict[tuple[int, int], list[AchievementSortie]]:
    """The counted sorties of each player in `chunk` per tour (of their mission; sorties outside every tour are left
    out), in chronological order (spawn time, then id). `tours` None = every tour."""
    strike = _strike_kills(chunk, tours)
    rows = (
        counted_sorties()
        .filter(player_id__in=chunk, tour_id__isnull=False)
        .order_by("player_id", "spawned_at", "pk")
        .values(
            "player_id",
            "pk",
            "mission_id",
            "tour_id",
            "spawned_at",
            "ended_at",
            "aircraft_id",
            "flight_time_s",
            "kills_air",
            "kills_ground",
            "kills_ground_tank",
            "landing_damage",
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
            "combat_role",
        )
    )
    if tours is not None:
        rows = rows.filter(tour_id__in=tours)
    found: dict[tuple[int, int], list[AchievementSortie]] = {}
    for r in rows.iterator():
        outcome = r["outcome"]
        found.setdefault((r["player_id"], r["tour_id"]), []).append(
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
                landing_damage=r["landing_damage"],
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
                is_attack=track_of(r["combat_role"]) is Track.GROUND,
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


def rollup_achievements(
    chunk: list[int], achievements: Sequence[Achievement] | None = None, tour_ids: Iterable[int] | None = None
) -> None:
    """Make the all-time medal rows of these players follow their per-tour rows (see the module docstring).
    `tour_ids`: the tours whose rows were just refreshed (None = all). A tier crossed in a tour older than the oldest
    of them cannot change (the crossing depends on that tour and the earlier ones only): its stored row is kept, and
    only the (player, tour) pairs from the oldest touched tour on are replayed."""
    active = {a.key: a for a in (applied_rules().active() if achievements is None else achievements)}
    rows = PlayerAchievement.objects.filter(player_id__in=chunk, tour__isnull=False, key__in=list(active))
    wanted: dict[_Key, _Value] = {}
    for player_id, medal, tier, earned_at, sortie_id, mission_id in rows.order_by("earned_at", "sortie_id").values_list(
        "player_id", "key", "tier", "earned_at", "sortie_id", "mission_id"
    ):
        key = (player_id, None, medal, tier)
        if key not in wanted and not active[medal].cumulative:  # the earliest of the tours holding the tier
            wanted[key] = (earned_at, sortie_id, mission_id)
    order = _tour_order()
    first = 0 if tour_ids is None else min((order.index(t) for t in set(tour_ids) if t in order), default=len(order))
    cumulative = [a for a in active.values() if a.cumulative]
    runs = [a for a in active.values() if a.all_time_only]
    stored = _stored_all_time(chunk, {a.key for a in (*cumulative, *runs)}) if first > 0 else {}
    wanted.update(_cumulative_rows(chunk, cumulative, order, first, stored))
    wanted.update(_tour_run_rows(chunk, runs, order, first, stored))
    _sync(chunk, None, wanted, all_time=True)


def _tour_order() -> list[int]:
    """Every tour id, oldest first (by start, then id)."""
    return list(Tour.objects.order_by("started_at", "pk").values_list("pk", flat=True))


def _stored_all_time(chunk: list[int], keys: set[str]) -> dict[tuple[int, str, int], _Value]:
    """The all-time rows now stored for these medals: (player, key, tier) -> value."""
    rows = PlayerAchievement.objects.filter(player_id__in=chunk, tour__isnull=True, key__in=keys)
    return {(r.player_id, r.key, r.tier): (r.earned_at, r.sortie_id, r.mission_id) for r in rows}


def _load_pairs(pairs: Iterable[tuple[int, int]]) -> dict[tuple[int, int], list[AchievementSortie]]:
    """`_load` for exactly these (player, tour) pairs: one query per tour, for the players with a pair in it."""
    players: dict[int, set[int]] = {}
    for pid, tour_id in pairs:
        players.setdefault(tour_id, set()).add(pid)
    found: dict[tuple[int, int], list[AchievementSortie]] = {}
    for tour_id, pids in sorted(players.items()):
        found.update(_load(sorted(pids), {tour_id}))
    return found


def _cumulative_rows(
    chunk: list[int],
    cumulative: list[Achievement],
    order: list[int],
    first: int,
    stored: dict[tuple[int, str, int], _Value],
) -> dict[_Key, _Value]:
    """The all-time rows of the cumulative medals: per player and medal the running sum of the tours' totals (the
    `PlayerTour` counters, in tour order) against the all-time thresholds (`ALL_TIME_FACTOR` times the per-tour ones);
    a tier is earned in the first tour whose sum reaches the threshold, at the sortie where the carried-in total plus
    the tour's own running total does (the tour's sorties are replayed: the per-tour rows have other thresholds).
    A tier crossed in a tour before position `first` of `order` (older than every touched tour) keeps its `stored`
    row; the other crossings are replayed, those (player, tour) pairs only."""
    if not cumulative:
        return {}
    position = {pk: n for n, pk in enumerate(order)}
    fields = sorted({a.counter for a in cumulative})
    totals: dict[int, list[tuple[int, dict[str, float]]]] = {}
    for row in PlayerTour.objects.filter(player_id__in=chunk).values("player_id", "tour_id", *fields):
        totals.setdefault(row["player_id"], []).append((row["tour_id"], {f: float(row[f]) for f in fields}))
    # (player, tour) -> [(achievement, carried-in total, tiers the running sum reaches in this tour)]
    wanted: dict[_Key, _Value] = {}
    replay: dict[tuple[int, int], list[tuple[Achievement, float, list[int]]]] = {}
    for pid, per_tour in totals.items():
        per_tour.sort(key=lambda t: position.get(t[0], 0))
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
                    if position.get(tour_id, 0) < first:  # cannot have changed: keep what is stored
                        for n in [n for n in reached if (pid, a.key, n) in stored]:
                            wanted[(pid, None, a.key, n)] = stored[(pid, a.key, n)]
                        reached = [n for n in reached if (pid, a.key, n) not in stored]
                    if reached:
                        replay.setdefault((pid, tour_id), []).append((a, carried, reached))
                carried += total
    if replay:
        loaded = _load_pairs(replay)
        for (pid, tour_id), todo in replay.items():
            sorties = loaded.get((pid, tour_id), [])
            for a, carried, reached in todo:
                found = {e.tier: e.index for e in earn(a, sorties, carried, all_time=True)}
                for n in reached:
                    if sorties:  # a tier the replay misses by a rounding hair: the tour's last sortie
                        s = sorties[found.get(n, len(sorties) - 1)]
                        wanted[(pid, None, a.key, n)] = (s.ended_at, s.sortie_id, s.mission_id)
    return wanted


def _tour_run_rows(
    chunk: list[int],
    runs: list[Achievement],
    order: list[int],
    first: int,
    stored: dict[tuple[int, str, int], _Value],
) -> dict[_Key, _Value]:
    """The all-time rows of the tours-in-a-row medals: a tour counts when the pilot flew in it (a `PlayerTour` row with
    `takeoffs > 0`: a sortie that never took off is not a sortie flown); a tour in between with none breaks the run.
    A tier is earned at the pilot's first sortie of the tour whose run reached it (the first that took off). As in
    `_cumulative_rows`, a tier completed in a tour before position `first` of `order` keeps its `stored` row."""
    if not runs:
        return {}
    flown: dict[int, set[int]] = {}
    played_rows = PlayerTour.objects.filter(player_id__in=chunk, takeoffs__gt=0).values_list("player_id", "tour_id")
    for pid, tour_id in played_rows:
        flown.setdefault(pid, set()).add(tour_id)
    wanted: dict[_Key, _Value] = {}
    completing: dict[tuple[int, int], list[tuple[str, int]]] = {}  # (player, tour) -> [(key, tier)]
    for pid, tour_ids in flown.items():
        played = [t in tour_ids for t in order]
        for a in runs:
            for e in earn_tours(a, played):
                if e.index < first and (pid, a.key, e.tier) in stored:  # cannot have changed: keep what is stored
                    wanted[(pid, None, a.key, e.tier)] = stored[(pid, a.key, e.tier)]
                else:
                    completing.setdefault((pid, order[e.index]), []).append((a.key, e.tier))
    if not completing:
        return wanted
    earliest: dict[tuple[int, int], _Value] = {}
    fallback: dict[tuple[int, int], _Value] = {}
    by_tour: dict[int, set[int]] = {}
    for pid, tour_id in completing:
        by_tour.setdefault(tour_id, set()).add(pid)
    for tour_id, pids in sorted(by_tour.items()):  # one query per tour, for the players completing a run in it
        sorties = (
            counted_sorties()
            .filter(player_id__in=sorted(pids), tour_id=tour_id)
            .order_by("spawned_at", "pk")
            .values_list("player_id", "ended_at", "pk", "mission_id", "outcome")
        )
        for pid, ended_at, sortie_id, mission_id, outcome in sorties.iterator():
            pair = (pid, tour_id)
            value = (ended_at, sortie_id, mission_id)
            fallback.setdefault(pair, value)
            if outcome != Outcome.NOT_TAKEN_OFF:
                earliest.setdefault(pair, value)
    for pair, earned in completing.items():
        value = earliest.get(pair) or fallback.get(pair)
        if value is None:  # the tour's sorties are gone (cannot happen: the counters are summed from them)
            continue
        for key, tier in earned:
            wanted[(pair[0], None, key, tier)] = value
    return wanted


def refresh_tour_runs(chunk: list[int]) -> None:
    """Roll up only the tours-in-a-row (Old Hand) all-time rows of these players, from their `PlayerTour` rows: for the
    players whose run crosses a tour that was inserted or removed (the set of tours changed, no row of theirs did)."""
    runs = [a for a in applied_rules().active() if a.all_time_only]
    if runs:
        # the set of tours changed: every run is recomputed from the first tour (nothing kept from the store)
        wanted = _tour_run_rows(chunk, runs, _tour_order(), 0, {})
        _sync(chunk, None, wanted, all_time=True, keys={a.key for a in runs})


def _sync(
    chunk: list[int],
    tours: set[int] | None,
    wanted: dict[_Key, _Value],
    *,
    all_time: bool,
    keys: set[str] | None = None,
) -> None:
    """Make the chunk's rows of one scope equal `wanted`: the all-time rows (`all_time`), else those of `tours` (None =
    every tour). `keys`: only the rows of these medals (the rest of the scope is left alone)."""
    rows = PlayerAchievement.objects.filter(player_id__in=chunk)
    if keys is not None:
        rows = rows.filter(key__in=keys)
    if all_time:
        rows = rows.filter(tour__isnull=True)
    else:
        rows = rows.filter(tour__isnull=False)
        if tours is not None:
            rows = rows.filter(tour_id__in=tours)
    sync_rows(
        PlayerAchievement,
        rows,
        ("player_id", "tour_id", "key", "tier"),
        _FIELDS,
        {key: dict(zip(_FIELDS, values, strict=True)) for key, values in wanted.items()},
    )


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
    delete_pks(AchievementHolders.objects, [r.pk for r in existing.values()])
    update_rows(AchievementHolders, changed, ["holders", "pilots"])
    AchievementHolders.objects.bulk_create(new)


def rebuild_achievements(rules: Rules | None = None) -> None:
    """Recompute every player's medals and the holder counts (`rebuild_aggregates` does the same through
    `recompute_players`), with `rules` (default: the applied ones)."""
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
