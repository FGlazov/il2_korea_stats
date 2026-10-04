"""The killboard by aircraft type (`PlayerTypeKillboard`, FR-WEB-9): level 2, recomputed per affected player from the
`Kill` rows, in the same pass as the player-versus-player rows (`ingest.pairs`, called from `recompute_players`).

For a player: every enemy aircraft type they shot down (`kills`) and every type that shot them down (`deaths`), with
their own type most used in each direction. The kills are the same as the player killboard's: a `Kill` with credit
`kill`, not friendly fire, between pilot sorties of two different accounts; assists are never counted here. Hidden
players and missions count (FR-ADM-3). Per tour: the same counted over that tour's missions; `tour_ids` limits which
tours' rows are rewritten (None = all), the all-time rows always are.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from django.db.models import F, Q

from il2ks.db.models import Kill, KillCredit, PlayerTypeKillboard, Role
from il2ks.ingest.dbutil import update_rows

type _Key = tuple[int, int | None, int]  # player, tour (None = all time), enemy aircraft


@dataclass(slots=True)
class _Cell:
    kills: int = 0
    deaths: int = 0
    kills_with: Counter[int] = field(default_factory=Counter[int])
    deaths_in: Counter[int] = field(default_factory=Counter[int])


def _most_used(counts: Counter[int]) -> int | None:
    """The aircraft id with the highest count, the lowest id on a tie; None for no entries."""
    return min(counts, key=lambda aircraft: (-counts[aircraft], aircraft)) if counts else None


def recompute_type_killboard(chunk: list[int], tour_ids: Iterable[int] | None = None) -> None:
    """Make the type-killboard rows of every player in `chunk` equal what the kills say."""
    players = set(chunk)
    tours = None if tour_ids is None else set(tour_ids)
    kills = (
        Kill.objects.filter(
            Q(killer_sortie__player_id__in=chunk) | Q(victim_sortie__player_id__in=chunk),
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
            "mission__tour_id",
        )
    )
    cells: dict[_Key, _Cell] = {}
    for killer, victim, killer_aircraft, victim_aircraft, tour_id in kills.iterator():
        scopes: tuple[int | None, ...] = (
            (None,) if tour_id is None or (tours is not None and tour_id not in tours) else (None, tour_id)
        )
        for scope in scopes:
            if killer in players:
                cell = cells.setdefault((killer, scope, victim_aircraft), _Cell())
                cell.kills += 1
                cell.kills_with[killer_aircraft] += 1
            if victim in players:
                cell = cells.setdefault((victim, scope, killer_aircraft), _Cell())
                cell.deaths += 1
                cell.deaths_in[victim_aircraft] += 1

    existing_rows = PlayerTypeKillboard.objects.filter(player_id__in=chunk)
    if tours is not None:
        existing_rows = existing_rows.filter(Q(tour__isnull=True) | Q(tour_id__in=tours))
    existing = {(r.player_id, r.tour_id, r.enemy_aircraft_id): r for r in existing_rows}
    changed: list[PlayerTypeKillboard] = []
    new: list[PlayerTypeKillboard] = []
    for key, cell in cells.items():
        values = (cell.kills, cell.deaths, _most_used(cell.kills_with), _most_used(cell.deaths_in))
        row = existing.pop(key, None)
        if row is None:
            new.append(
                PlayerTypeKillboard(
                    player_id=key[0],
                    tour_id=key[1],
                    enemy_aircraft_id=key[2],
                    kills=values[0],
                    deaths=values[1],
                    kills_with_id=values[2],
                    deaths_in_id=values[3],
                )
            )
        elif (row.kills, row.deaths, row.kills_with_id, row.deaths_in_id) != values:
            row.kills, row.deaths, row.kills_with_id, row.deaths_in_id = values
            changed.append(row)
    PlayerTypeKillboard.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()  # nothing left to count
    update_rows(PlayerTypeKillboard, changed, ["kills", "deaths", "kills_with", "deaths_in"])
    PlayerTypeKillboard.objects.bulk_create(new)
