"""Killboard rows (`PlayerKillboard`, `PlayerTourKillboard`, FR-WEB-9): level 2, recomputed per affected player from the
`Kill` rows.

A pair's row for player A reads only kills between A and the opponent, but its mirror row belongs to the opponent. So
`recompute_killboard(players)` recomputes *every pair that touches these players*, both mirror rows, from all their
kills: the rows come out the same whether a mission's players were recomputed in one batch or one by one, and
the same as a rebuild of everybody (`il2ks rebuild-aggregates` reaches it through `aggregates.recompute_players`).

What counts: a `Kill` row with credit `kill` (not friendly fire) whose killer and victim are pilot sorties of two
different accounts. With `[killboard] assists` on, a row with credit `assist` counts too, in its own `assists` column
(the killer sortie's player assisted on the victim); `assists_received` is the mirror count (the opponent's assists
on the player's sorties, OQ-81). Hidden players and missions count too (FR-ADM-3: hiding is
presentation only).

The setting that decides about assists is the one the aggregates were last rebuilt with, stored in
`SiteSettings.killboard_assists` (`aggregates.rebuild_aggregates` writes it): a config change takes effect with
`il2ks rebuild-aggregates`, and the incremental updates between rebuilds follow the last rebuild, never the file.

Per tour (`PlayerTourKillboard`): the same pairs counted over the kills of that tour's missions. One pass over the
kills fills both; `tour_ids` limits which tours' rows are rewritten (None = all), the all-time rows always are.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from django.db.models import F, Q
from django.db.models.manager import BaseManager

from il2ks.db.models import Kill, KillCredit, PlayerKillboard, PlayerTourKillboard, Role
from il2ks.db.site import get_site_settings
from il2ks.ingest.dbutil import update_rows


@dataclass(slots=True)
class _Pair:
    kills: int = 0  # by the lower-id player on the higher-id one
    deaths: int = 0
    assists_low: int = 0  # the lower-id player's assists on the higher-id one
    assists_high: int = 0
    last_at: datetime | None = None
    last_mission_id: int = 0


type _Values = tuple[int, int, int, int, datetime, int]  # kills, deaths, assists, assists received, last_at, mission
type _Key = tuple[int, int, int | None]  # player, opponent, tour (None = all time)


def recompute_killboard(chunk: list[int], tour_ids: Iterable[int] | None = None) -> None:
    """Make the killboard rows of every pair touching a player in `chunk` equal what the kills say."""
    with_assists = get_site_settings().killboard_assists
    credits = [KillCredit.KILL, KillCredit.ASSIST] if with_assists else [KillCredit.KILL]
    tours = None if tour_ids is None else set(tour_ids)
    kills = (
        Kill.objects.filter(
            Q(killer_sortie__player_id__in=chunk) | Q(victim_sortie__player_id__in=chunk),
            credit__in=credits,
            is_friendly=False,
            killer_sortie__role=Role.PILOT,
            victim_sortie__role=Role.PILOT,
        )
        .exclude(killer_sortie__player_id=F("victim_sortie__player_id"))
        .order_by("time", "pk")
        .values_list(
            "killer_sortie__player_id", "victim_sortie__player_id", "credit", "time", "mission_id", "mission__tour_id"
        )
    )
    pairs: dict[tuple[int, int, int | None], _Pair] = {}
    for killer, victim, credit, time, mission_id, tour_id in kills.iterator():
        low, high = sorted((killer, victim))
        scopes: tuple[int | None, ...] = (
            (None,) if tour_id is None or (tours is not None and tour_id not in tours) else (None, tour_id)
        )
        for scope in scopes:
            pair = pairs.setdefault((low, high, scope), _Pair())
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
    for (low, high, scope), pair in pairs.items():
        assert pair.last_at is not None
        wanted[(low, high, scope)] = (
            pair.kills,
            pair.deaths,
            pair.assists_low,
            pair.assists_high,
            pair.last_at,
            pair.last_mission_id,
        )
        wanted[(high, low, scope)] = (
            pair.deaths,
            pair.kills,
            pair.assists_high,
            pair.assists_low,
            pair.last_at,
            pair.last_mission_id,
        )

    touching = Q(player_id__in=chunk) | Q(opponent_id__in=chunk)
    all_time = {key: values for key, values in wanted.items() if key[2] is None}
    _sync(
        PlayerKillboard.objects,
        {(p, o): v for (p, o, _), v in all_time.items()},
        {(r.player_id, r.opponent_id): r for r in PlayerKillboard.objects.filter(touching)},
        lambda key: {"player_id": key[0], "opponent_id": key[1]},
    )
    per_tour = PlayerTourKillboard.objects.filter(touching)
    if tours is not None:
        per_tour = per_tour.filter(tour_id__in=tours)
    _sync(
        PlayerTourKillboard.objects,
        {key: v for key, v in wanted.items() if key[2] is not None},
        {(r.player_id, r.opponent_id, r.tour_id): r for r in per_tour},
        lambda key: {"player_id": key[0], "opponent_id": key[1], "tour_id": key[2]},
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
    manager.filter(pk__in=[r.pk for r in existing.values()]).delete()  # nothing left to count
    update_rows(manager.model, changed, ["kills", "deaths", "assists", "assists_received", "last_at", "last_mission"])
    manager.bulk_create(new)
