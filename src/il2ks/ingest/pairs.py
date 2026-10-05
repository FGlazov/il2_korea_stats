"""Killboard rows (`PlayerKillboard`, `PlayerTourKillboard`, FR-WEB-9): level 2, recomputed per affected player from the
`Kill` rows.

A pair's row for player A reads only kills between A and the opponent, but its mirror row belongs to the opponent. So
`recompute_killboard(players, tours)` recomputes the per-tour rows of *every pair that touches these players*, both
mirror rows, from the kills of those tours only: the rows come out the same whether a mission's players were recomputed
in one batch or one by one, and the same as a rebuild of everybody (`il2ks rebuild-aggregates` reaches it through
`aggregates.recompute_players`). The all-time rows (`PlayerKillboard`) are the roll-up of the tour rows
(`rollup_killboard`, `ingest.rollup`): counts SUM, `last_at` / `last_mission` from the newest row. They never read
`Kill`.

What counts: a `Kill` row with credit `kill` (not friendly fire) whose killer and victim are pilot sorties of two
different accounts. With `[killboard] assists` on, a row with credit `assist` counts too, in its own `assists` column
(the killer sortie's player assisted on the victim); `assists_received` is the mirror count (the opponent's assists
on the player's sorties, OQ-81). Hidden players and missions count too (FR-ADM-3: hiding is
presentation only).

The setting that decides about assists is the one the aggregates were last rebuilt with, stored in
`SiteSettings.killboard_assists` (`aggregates.rebuild_aggregates` writes it): a config change takes effect with
`il2ks rebuild-aggregates`, and the incremental updates between rebuilds follow the last rebuild, never the file.

Per tour (`PlayerTourKillboard`): the same pairs counted over the kills of that tour's missions; `tour_ids` limits
which tours' rows are rewritten (None = all).
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from django.db.models import F, Q, QuerySet
from django.db.models.manager import BaseManager

from il2ks.db.models import Kill, KillCredit, PlayerKillboard, PlayerTourKillboard, Role
from il2ks.db.site import get_site_settings
from il2ks.ingest.dbutil import delete_pks, update_rows
from il2ks.ingest.rollup import Row, rollup


@dataclass(slots=True)
class _Pair:
    kills: int = 0  # by the lower-id player on the higher-id one
    deaths: int = 0
    assists_low: int = 0  # the lower-id player's assists on the higher-id one
    assists_high: int = 0
    last_at: datetime | None = None
    last_mission_id: int = 0


type _Values = tuple[int, int, int, int, datetime, int]  # kills, deaths, assists, assists received, last_at, mission
type _Key = tuple[int, int, int]  # player, opponent, tour


def recompute_killboard(chunk: list[int], tour_ids: Iterable[int] | None = None) -> None:
    """Make the per-tour killboard rows (`PlayerTourKillboard`) of every pair touching a player in `chunk` equal what
    the kills of `tour_ids` (None = every tour) say. Reads only those tours' kills; `rollup_killboard` builds
    the all-time rows from the tour rows."""
    with_assists = get_site_settings().killboard_assists
    credits = [KillCredit.KILL, KillCredit.ASSIST] if with_assists else [KillCredit.KILL]
    tours = None if tour_ids is None else set(tour_ids)
    rows = (
        pair_kills(chunk, credits, tours)
        .order_by("time", "pk")
        .values_list(
            "killer_sortie__player_id", "victim_sortie__player_id", "credit", "time", "mission_id", "mission__tour_id"
        )
    )
    pairs: dict[tuple[int, int, int], _Pair] = {}
    for killer, victim, credit, time, mission_id, tour_id in rows.iterator():
        low, high = sorted((killer, victim))
        pair = pairs.setdefault((low, high, tour_id), _Pair())
        if credit == KillCredit.ASSIST:
            if killer == low:
                pair.assists_low += 1
            else:
                pair.assists_high += 1
        elif killer == low:
            pair.kills += 1
        else:
            pair.deaths += 1
        pair.last_at, pair.last_mission_id = time, mission_id  # ordered by time: the last one wins

    wanted: dict[_Key, _Values] = {}
    for (low, high, tour_id), pair in pairs.items():
        assert pair.last_at is not None
        wanted[(low, high, tour_id)] = (
            pair.kills,
            pair.deaths,
            pair.assists_low,
            pair.assists_high,
            pair.last_at,
            pair.last_mission_id,
        )
        wanted[(high, low, tour_id)] = (
            pair.deaths,
            pair.kills,
            pair.assists_high,
            pair.assists_low,
            pair.last_at,
            pair.last_mission_id,
        )

    per_tour = PlayerTourKillboard.objects.filter(_touching(chunk))
    if tours is not None:
        per_tour = per_tour.filter(tour_id__in=tours)
    _sync(
        PlayerTourKillboard.objects,
        wanted,
        {(r.player_id, r.opponent_id, r.tour_id): r for r in per_tour},
        lambda key: {"player_id": key[0], "opponent_id": key[1], "tour_id": key[2]},
    )


def pair_kills(chunk: list[int], credits: list[KillCredit], tours: set[int] | None) -> QuerySet[Kill]:
    """The kill rows the per-tour pairs of these players are counted from: of the missions of `tours` (None = every
    tour), between pilot sorties of two accounts, one of them in `chunk`."""
    kills = Kill.objects.filter(
        Q(killer_sortie__player_id__in=chunk) | Q(victim_sortie__player_id__in=chunk),
        credit__in=credits,
        is_friendly=False,
        killer_sortie__role=Role.PILOT,
        victim_sortie__role=Role.PILOT,
        mission__tour_id__isnull=False,
    )
    if tours is not None:
        kills = kills.filter(mission__tour_id__in=tours)
    return kills.exclude(killer_sortie__player_id=F("victim_sortie__player_id"))


def _touching(chunk: list[int]) -> Q:
    return Q(player_id__in=chunk) | Q(opponent_id__in=chunk)


def rollup_killboard(chunk: list[int]) -> None:
    """`PlayerKillboard` of every pair touching a player in `chunk` = the per-tour rows added up: counts SUM, `last_at`
    MAX, `last_mission` that of the newest row. Reads the tour rows only (never `Kill`)."""

    def newest(rows: Sequence[Row]) -> object:
        return max(enumerate(rows), key=lambda item: (cast(datetime, item[1]["last_at"]), item[0]))[1][
            "last_mission_id"
        ]

    rollup(
        PlayerKillboard,
        PlayerKillboard.objects.filter(_touching(chunk)),
        PlayerTourKillboard.objects.filter(_touching(chunk)),
        key=("player_id", "opponent_id"),
        sums=("kills", "deaths", "assists", "assists_received"),
        maxes=("last_at",),
        derived={"last_mission_id": newest},
        source_fields=("last_mission_id",),
    )


def _sync[M: PlayerKillboard | PlayerTourKillboard, K](
    manager: BaseManager[M],
    wanted: dict[K, _Values],
    existing: dict[K, M],
    identity: Callable[[K], dict[str, int | None]],
) -> None:
    """Make the rows in `existing` (by key) equal `wanted`: insert, update changed values only, delete the rest."""
    changed: list[M] = []
    new: list[M] = []
    for key, (kills, deaths, assists, received, last_at, last_mission_id) in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(
                manager.model(
                    **identity(key),
                    kills=kills,
                    deaths=deaths,
                    assists=assists,
                    assists_received=received,
                    last_at=last_at,
                    last_mission_id=last_mission_id,
                )
            )
        elif (row.kills, row.deaths, row.assists, row.assists_received, row.last_at, row.last_mission_id) != (
            kills,
            deaths,
            assists,
            received,
            last_at,
            last_mission_id,
        ):
            row.kills, row.deaths, row.assists, row.assists_received = kills, deaths, assists, received
            row.last_at, row.last_mission_id = last_at, last_mission_id
            changed.append(row)
    delete_pks(manager, [r.pk for r in existing.values()])  # nothing left to count
    update_rows(manager.model, changed, ["kills", "deaths", "assists", "assists_received", "last_at", "last_mission"])
    manager.bulk_create(new)
