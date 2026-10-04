"""Achievements / medals (`PlayerAchievement`, FR-WEB-26): level 2, recomputed per player from their counted (pilot)
sorties, in the same pass as the ironman streaks (`recompute_players`).

The rules are `il2ks.core.achievements`; this module reads the sorties in chronological order (spawn time, then id),
adds the one fact the sortie row does not hold (bombers and attackers shot down, from the `Kill` rows) and writes the
rows that changed. The same definitions run twice per player: over all sorties (`tour` null, all time) and over each
tour's sorties alone (a life, a streak or a run of weeks starts fresh in a tour). A player with no tier has no row.
`recompute_holders` then rewrites the per-scope, per-tier holder counts and the pilot count (the rarity denominator)
that the overview page and every medal's hover text show (visible players only).
"""

from collections import Counter
from collections.abc import Iterable

from django.db.models import Count, F, Q

from il2ks.core.achievements import AchievementSortie, EarnedTier, earn_all
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
)
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import update_rows

CHUNK = 400  # players per batch: stays far below SQLite's bound-parameter limit
STRIKE_CLASSES = (ObjectClass.BOMBER, ObjectClass.ATTACKER)
_FIELDS = ("earned_at", "sortie_id", "mission_id")

type _Key = tuple[int, int | None, str, int]  # player, tour (None = all time), achievement key, tier
type _Value = tuple[object, int, int]  # earned_at, sortie, mission


def _strike_kills(chunk: list[int]) -> Counter[int]:
    """Per killer sortie id: credited kills of other pilots' bombers and attackers (not friendly, not own aircraft)."""
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
    return Counter(kills.iterator())


def _load(chunk: list[int]) -> tuple[dict[int, list[AchievementSortie]], dict[int, int | None]]:
    """The counted sorties of each player in `chunk`, in chronological order (spawn time, then id), and each sortie's
    tour (its mission's; None outside every tour)."""
    strike = _strike_kills(chunk)
    rows = (
        counted_sorties()
        .filter(player_id__in=chunk)
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
    by_player: dict[int, list[AchievementSortie]] = {}
    tour_of: dict[int, int | None] = {}  # sortie -> its mission's tour
    for r in rows.iterator():
        tour_of[r["pk"]] = r["mission__tour_id"]
        outcome = r["outcome"]
        by_player.setdefault(r["player_id"], []).append(
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
    return by_player, tour_of


def recompute_achievements(chunk: list[int], tour_ids: Iterable[int] | None = None) -> None:
    """Make the achievement rows of every player in `chunk` equal what their sorties say: all time, and per tour for
    `tour_ids` (None = every tour of these players, as a rebuild does)."""
    tours = None if tour_ids is None else set(tour_ids)
    by_player, tour_of = _load(chunk)
    wanted: dict[_Key, _Value] = {}
    for pid, all_sorties in by_player.items():
        scopes: dict[int | None, list[AchievementSortie]] = {None: all_sorties}
        for sortie in all_sorties:
            tour_id = tour_of[sortie.sortie_id]
            if tour_id is not None and (tours is None or tour_id in tours):
                scopes.setdefault(tour_id, []).append(sortie)
        for tour_id, sorties in scopes.items():
            earned: list[EarnedTier] = earn_all(sorties, all_time=tour_id is None)
            for e in earned:
                s = sorties[e.index]
                wanted[(pid, tour_id, e.key, e.tier)] = (s.ended_at, s.sortie_id, s.mission_id)
    _sync(chunk, tours, wanted)


def _sync(chunk: list[int], tours: set[int] | None, wanted: dict[_Key, _Value]) -> None:
    """Make the chunk's rows (all-time, and those of the touched tours) equal `wanted`."""
    rows = PlayerAchievement.objects.filter(player_id__in=chunk)
    if tours is not None:
        rows = rows.filter(Q(tour_id__isnull=True) | Q(tour_id__in=tours))
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


def rebuild_achievements() -> None:
    """Recompute every player's medals and the holder counts (the upgrade backfill; `rebuild_aggregates` does the same
    through `recompute_players`)."""
    ids = sorted(Player.objects.values_list("pk", flat=True))
    for start in range(0, len(ids), CHUNK):
        recompute_achievements(ids[start : start + CHUNK])
    recompute_holders()
