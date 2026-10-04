"""Killboard rows (`PlayerKillboard`, FR-WEB-9): level 2, recomputed per affected player from the `Kill` rows.

A pair's row for player A reads only kills between A and the opponent, but its mirror row belongs to the opponent. So
`recompute_killboard(players)` recomputes *every pair that touches these players*, both mirror rows, from all their
kills: the rows come out the same whether a mission's players were recomputed in one batch or one by one, and
the same as a rebuild of everybody (`il2ks rebuild-aggregates` reaches it through `aggregates.recompute_players`).

What counts: a `Kill` row with credit `kill` (not an assist, not friendly fire) whose killer and victim are pilot
sorties of two different accounts. Hidden players and missions count too (FR-ADM-3: hiding is presentation only).
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import F, Q

from il2ks.db.models import Kill, KillCredit, PlayerKillboard, Role
from il2ks.ingest.dbutil import update_rows


@dataclass(slots=True)
class _Pair:
    kills: int = 0  # by the lower-id player on the higher-id one
    deaths: int = 0
    last_at: datetime | None = None
    last_mission_id: int = 0


def recompute_killboard(chunk: list[int]) -> None:
    """Make the killboard rows of every pair touching a player in `chunk` equal what the kills say."""
    kills = (
        Kill.objects.filter(
            Q(killer_sortie__player_id__in=chunk) | Q(victim_sortie__player_id__in=chunk),
            credit=KillCredit.KILL,
            is_friendly=False,
            killer_sortie__role=Role.PILOT,
            victim_sortie__role=Role.PILOT,
        )
        .exclude(killer_sortie__player_id=F("victim_sortie__player_id"))
        .order_by("time", "pk")
        .values_list("killer_sortie__player_id", "victim_sortie__player_id", "time", "mission_id")
    )
    pairs: dict[tuple[int, int], _Pair] = {}
    for killer, victim, time, mission_id in kills.iterator():
        low, high = sorted((killer, victim))
        pair = pairs.setdefault((low, high), _Pair())
        if killer == low:
            pair.kills += 1
        else:
            pair.deaths += 1
        pair.last_at, pair.last_mission_id = time, mission_id  # ordered by time: the last one wins

    wanted: dict[tuple[int, int], tuple[int, int, datetime, int]] = {}
    for (low, high), pair in pairs.items():
        assert pair.last_at is not None
        wanted[(low, high)] = (pair.kills, pair.deaths, pair.last_at, pair.last_mission_id)
        wanted[(high, low)] = (pair.deaths, pair.kills, pair.last_at, pair.last_mission_id)

    existing = {
        (r.player_id, r.opponent_id): r
        for r in PlayerKillboard.objects.filter(Q(player_id__in=chunk) | Q(opponent_id__in=chunk))
    }
    changed: list[PlayerKillboard] = []
    new: list[PlayerKillboard] = []
    for key, (kills_n, deaths_n, last_at, last_mission_id) in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(
                PlayerKillboard(
                    player_id=key[0],
                    opponent_id=key[1],
                    kills=kills_n,
                    deaths=deaths_n,
                    last_at=last_at,
                    last_mission_id=last_mission_id,
                )
            )
        elif (row.kills, row.deaths, row.last_at, row.last_mission_id) != (kills_n, deaths_n, last_at, last_mission_id):
            row.kills, row.deaths, row.last_at, row.last_mission_id = kills_n, deaths_n, last_at, last_mission_id
            changed.append(row)
    PlayerKillboard.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()  # no kills left
    update_rows(PlayerKillboard, changed, ["kills", "deaths", "last_at", "last_mission"])
    PlayerKillboard.objects.bulk_create(new)
