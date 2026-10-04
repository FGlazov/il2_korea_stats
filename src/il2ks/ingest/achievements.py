"""Achievements / medals (`PlayerAchievement`, FR-WEB-26): level 2, recomputed per player from their counted (pilot)
sorties, in the same pass as the ironman streaks (`recompute_players`).

The rules are `il2ks.core.achievements`; this module reads the sorties in chronological order (spawn time, then id),
adds the one fact the sortie row does not hold (bombers and attackers shot down, from the `Kill` rows) and writes the
rows that changed. A player with no tier has no row. `recompute_holders` then rewrites the per-tier holder counts that
the overview page shows (visible players only).
"""

from collections import Counter

from django.db.models import F

from il2ks.core.achievements import AchievementSortie, EarnedTier, earn_all
from il2ks.db.models import (
    AchievementHolders,
    Kill,
    KillCredit,
    ObjectClass,
    Outcome,
    Player,
    PlayerAchievement,
    Role,
)
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import update_rows

CHUNK = 400  # players per batch: stays far below SQLite's bound-parameter limit
STRIKE_CLASSES = (ObjectClass.BOMBER, ObjectClass.ATTACKER)
_FIELDS = ("earned_at", "sortie_id", "mission_id")

type _Key = tuple[int, str, int]  # player, achievement key, tier
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


def recompute_achievements(chunk: list[int]) -> None:
    """Make the achievement rows of every player in `chunk` equal what their sorties say."""
    strike = _strike_kills(chunk)
    rows = (
        counted_sorties()
        .filter(player_id__in=chunk)
        .order_by("player_id", "spawned_at", "pk")
        .values_list(
            "player_id",
            "pk",
            "mission_id",
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
        )
    )
    by_player: dict[int, list[AchievementSortie]] = {}
    for (
        pid,
        pk,
        mission_id,
        spawned,
        ended,
        aircraft,
        flight,
        air,
        ground,
        tank,
        damage,
        outcome,
        death,
        cap,
    ) in rows.iterator():
        by_player.setdefault(pid, []).append(
            AchievementSortie(
                sortie_id=pk,
                mission_id=mission_id,
                spawned_at=spawned,
                ended_at=ended,
                aircraft_id=aircraft,
                flight_time_s=flight,
                kills_air=air,
                kills_ground=ground,
                kills_ground_tank=tank,
                kills_strike_air=strike[pk],
                damage_taken=damage,
                landed=outcome == Outcome.LANDED,
                is_death=death,
                is_captured=cap,
                not_taken_off=outcome == Outcome.NOT_TAKEN_OFF,
            )
        )
    wanted: dict[_Key, _Value] = {}
    for pid, sorties in by_player.items():
        earned: list[EarnedTier] = earn_all(sorties)
        for e in earned:
            s = sorties[e.index]
            wanted[(pid, e.key, e.tier)] = (s.ended_at, s.sortie_id, s.mission_id)
    _sync(chunk, wanted)


def _sync(chunk: list[int], wanted: dict[_Key, _Value]) -> None:
    existing = {(r.player_id, r.key, r.tier): r for r in PlayerAchievement.objects.filter(player_id__in=chunk)}
    changed: list[PlayerAchievement] = []
    new: list[PlayerAchievement] = []
    for (pid, key, tier), values in wanted.items():
        row = existing.pop((pid, key, tier), None)
        if row is None:
            new.append(PlayerAchievement(player_id=pid, key=key, tier=tier, **dict(zip(_FIELDS, values, strict=True))))
        elif tuple(getattr(row, f) for f in _FIELDS) != values:
            for field, value in zip(_FIELDS, values, strict=True):
                setattr(row, field, value)
            changed.append(row)
    PlayerAchievement.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(PlayerAchievement, changed, list(_FIELDS))
    PlayerAchievement.objects.bulk_create(new)


def recompute_holders() -> None:
    """Rewrite the holder counts: per achievement tier, the visible players who hold it (FR-ADM-3)."""
    counts: Counter[tuple[str, int]] = Counter(
        PlayerAchievement.objects.filter(player__is_hidden=False).values_list("key", "tier").iterator()
    )
    existing = {(r.key, r.tier): r for r in AchievementHolders.objects.all()}
    changed: list[AchievementHolders] = []
    new: list[AchievementHolders] = []
    for (key, tier), holders in counts.items():
        row = existing.pop((key, tier), None)
        if row is None:
            new.append(AchievementHolders(key=key, tier=tier, holders=holders))
        elif row.holders != holders:
            row.holders = holders
            changed.append(row)
    AchievementHolders.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(AchievementHolders, changed, ["holders"])
    AchievementHolders.objects.bulk_create(new)


def rebuild_achievements() -> None:
    """Recompute every player's medals and the holder counts (the upgrade backfill; `rebuild_aggregates` does the same
    through `recompute_players`)."""
    ids = sorted(Player.objects.values_list("pk", flat=True))
    for start in range(0, len(ids), CHUNK):
        recompute_achievements(ids[start : start + CHUNK])
    recompute_holders()
