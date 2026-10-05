"""The all-time player-side level-2 numbers computed straight from level 1 (the old way, before the roll-up), as a test
oracle: `ingest.aggregates` now builds all-time rows from the per-tour rows, and these numbers must not change.

Everything here reads `PlayerSortie`, `PlayerMission` and `Kill` only, never a level-2 row, and is deliberately simple
and slow. `diff_all_time()` compares the oracle with the stored all-time rows and returns a list of differences.
"""

from collections import Counter
from collections.abc import Mapping
from datetime import datetime

from django.db.models import Count, F, Max, Min, Sum

from il2ks.db.models import (
    Kill,
    KillCredit,
    Player,
    PlayerAircraft,
    PlayerAircraftBuild,
    PlayerKillboard,
    PlayerMission,
    PlayerName,
    PlayerPool,
    PlayerSortie,
    PlayerTypeKillboard,
    Propulsion,
    Role,
)
from il2ks.ingest.counters import COUNTER_FIELDS, SORTIE_COUNTERS, CounterValues, clean_counters, counted_sorties

DIGITS = 3  # floats are compared rounded: the roll-up sums the tour rows, the oracle sums the sorties


def _rounded(values: CounterValues) -> tuple[float, ...]:
    return tuple(round(float(values[name]), DIGITS) for name in COUNTER_FIELDS)


def _row_counters(row: object) -> tuple[float, ...]:
    return tuple(round(float(getattr(row, name)), DIGITS) for name in COUNTER_FIELDS)


def counters() -> dict[tuple[object, ...], tuple[float, ...]]:
    """Player, PlayerAircraft and PlayerPool counters."""
    out: dict[tuple[object, ...], tuple[float, ...]] = {}
    sums = {n: Sum(n) for n in COUNTER_FIELDS}
    totals = {r["player_id"]: r for r in PlayerMission.objects.values("player_id").annotate(**sums)}
    for player_id in Player.objects.values_list("pk", flat=True):
        out[("player", player_id)] = _rounded(clean_counters(totals.get(player_id, {})))
    for row in counted_sorties().values("player_id", "aircraft_id").annotate(**SORTIE_COUNTERS):
        out[("aircraft", row["player_id"], row["aircraft_id"])] = _rounded(clean_counters(row))
    pools = (
        counted_sorties()
        .filter(aircraft__propulsion__in=Propulsion.values)
        .values("player_id", "aircraft__propulsion")
        .annotate(**SORTIE_COUNTERS)
    )
    for row in pools:
        out[("pool", row["player_id"], row["aircraft__propulsion"])] = _rounded(clean_counters(row))
    return out


def stored_counters() -> dict[tuple[object, ...], tuple[float, ...]]:
    out: dict[tuple[object, ...], tuple[float, ...]] = {}
    for player in Player.objects.all():
        out[("player", player.pk)] = _row_counters(player)
    for aircraft in PlayerAircraft.objects.all():
        out[("aircraft", aircraft.player_id, aircraft.aircraft_id)] = _row_counters(aircraft)
    for pool in PlayerPool.objects.all():
        out[("pool", pool.player_id, pool.propulsion)] = _row_counters(pool)
    return out


def builds() -> dict[tuple[int, int, int, str], int]:
    rows = counted_sorties().values("player_id", "aircraft_id", "payload_id", "payload_name").annotate(n=Count("pk"))
    return {(r["player_id"], r["aircraft_id"], r["payload_id"], r["payload_name"]): r["n"] for r in rows}


def stored_builds() -> dict[tuple[int, int, int, str], int]:
    return {
        (r.player_id, r.aircraft_id, r.value, r.label): r.sorties
        for r in PlayerAircraftBuild.objects.filter(tour__isnull=True)
    }


type Board = dict[tuple[int, int], tuple[int, int, int, int, datetime, int]]


def killboard() -> Board:
    """(player, opponent) -> kills, deaths, assists, assists received, last_at, last mission (assists off)."""
    kills = (
        Kill.objects.filter(
            credit=KillCredit.KILL,
            is_friendly=False,
            killer_sortie__role=Role.PILOT,
            victim_sortie__role=Role.PILOT,
        )
        .exclude(killer_sortie__player_id=F("victim_sortie__player_id"))
        .order_by("time", "pk")
        .values_list("killer_sortie__player_id", "victim_sortie__player_id", "time", "mission_id")
    )
    counts: dict[tuple[int, int], list[int]] = {}
    last: dict[tuple[int, int], tuple[datetime, int]] = {}
    for killer, victim, time, mission_id in kills:
        low, high = sorted((killer, victim))
        pair = counts.setdefault((low, high), [0, 0])
        pair[0 if killer == low else 1] += 1
        last[(low, high)] = (time, mission_id)
    out: Board = {}
    for (low, high), (k, d) in counts.items():
        out[(low, high)] = (k, d, 0, 0, *last[(low, high)])
        out[(high, low)] = (d, k, 0, 0, *last[(low, high)])
    return out


def stored_killboard() -> Board:
    return {
        (r.player_id, r.opponent_id): (r.kills, r.deaths, r.assists, r.assists_received, r.last_at, r.last_mission_id)
        for r in PlayerKillboard.objects.all()
    }


type TypeBoard = dict[tuple[int, int], tuple[int, int, int | None, int | None]]


def type_killboard() -> TypeBoard:
    """(player, enemy type) -> kills, deaths, own type most used in the kills, own type most used in the deaths."""
    kills = (
        Kill.objects.filter(
            credit=KillCredit.KILL,
            is_friendly=False,
            killer_sortie__role=Role.PILOT,
            victim_sortie__role=Role.PILOT,
        )
        .exclude(killer_sortie__player_id=F("victim_sortie__player_id"))
        .values_list(
            "killer_sortie__player_id",
            "victim_sortie__player_id",
            "killer_sortie__aircraft_id",
            "victim_sortie__aircraft_id",
        )
    )
    n_kills: Counter[tuple[int, int]] = Counter()
    n_deaths: Counter[tuple[int, int]] = Counter()
    with_: dict[tuple[int, int], Counter[int]] = {}
    in_: dict[tuple[int, int], Counter[int]] = {}
    for killer, victim, killer_aircraft, victim_aircraft in kills:
        n_kills[(killer, victim_aircraft)] += 1
        with_.setdefault((killer, victim_aircraft), Counter())[killer_aircraft] += 1
        n_deaths[(victim, killer_aircraft)] += 1
        in_.setdefault((victim, killer_aircraft), Counter())[victim_aircraft] += 1

    def most(counts: Counter[int] | None) -> int | None:
        return min(counts, key=lambda a: (-counts[a], a)) if counts else None

    keys = set(n_kills) | set(n_deaths)
    return {k: (n_kills[k], n_deaths[k], most(with_.get(k)), most(in_.get(k))) for k in keys}


def stored_type_killboard() -> TypeBoard:
    return {
        (r.player_id, r.enemy_aircraft_id): (r.kills, r.deaths, r.kills_with_id, r.deaths_in_id)
        for r in PlayerTypeKillboard.objects.filter(tour__isnull=True)
    }


type Identity = dict[int, tuple[str, str, datetime, datetime]]


def identity() -> tuple[Identity, dict[tuple[int, str], tuple[datetime, datetime]]]:
    """Player -> (current name, lower name, first seen, last seen); (player, name) -> (first, last) of every name."""
    groups = PlayerSortie.objects.values("player_id", "name_at_time").annotate(
        first=Min("spawned_at"), last=Max("ended_at"), spawned=Max("spawned_at")
    )
    names: dict[tuple[int, str], tuple[datetime, datetime]] = {}
    latest: dict[int, tuple[datetime, str]] = {}
    first: dict[int, datetime] = {}
    last: dict[int, datetime] = {}
    for g in groups:
        pid, name = g["player_id"], g["name_at_time"]
        names[(pid, name)] = (g["first"], g["last"])
        if pid not in latest or g["spawned"] > latest[pid][0]:
            latest[pid] = (g["spawned"], name)
        first[pid] = min(first.get(pid, g["first"]), g["first"])
        last[pid] = max(last.get(pid, g["last"]), g["last"])
    return {pid: (latest[pid][1], latest[pid][1].lower(), first[pid], last[pid]) for pid in latest}, names


def stored_identity() -> tuple[Identity, dict[tuple[int, str], tuple[datetime, datetime]]]:
    oracle, _ = identity()
    players = {
        p.pk: (p.current_name, p.name_lower, p.first_seen, p.last_seen) for p in Player.objects.filter(pk__in=oracle)
    }
    names = {
        (n.player_id, n.name): (n.first_seen, n.last_seen) for n in PlayerName.objects.filter(player_id__in=oracle)
    }
    return players, names


def _compare[K](label: str, wanted: Mapping[K, object], stored: Mapping[K, object]) -> list[str]:
    return [
        f"{label} {key}: level 1 says {wanted.get(key)}, stored {stored.get(key)}"
        for key in sorted(set(wanted) | set(stored), key=repr)
        if wanted.get(key) != stored.get(key)
    ]


def diff_all_time() -> list[str]:
    """Differences between the stored all-time rows and what level 1 says (empty when they agree)."""
    wanted_identity, wanted_names = identity()
    stored_players, stored_names = stored_identity()
    return [
        *_compare("counters", counters(), stored_counters()),
        *_compare("builds", builds(), stored_builds()),
        *_compare("killboard", killboard(), stored_killboard()),
        *_compare("type killboard", type_killboard(), stored_type_killboard()),
        *_compare("identity", wanted_identity, stored_players),
        *_compare("names", wanted_names, stored_names),
    ]
