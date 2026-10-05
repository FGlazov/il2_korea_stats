"""The killboard by aircraft type (`PlayerTypeKillboard`, FR-WEB-9): level 2, recomputed per affected player from the
`Kill` rows, in the same pass as the player-versus-player rows (`ingest.pairs`, called from `recompute_players`).

For a player: every enemy aircraft type they shot down (`kills`) and every type that shot them down (`deaths`), with
their own type most used in each direction. The kills are the same as the player killboard's: a `Kill` with credit
`kill`, not friendly fire, between pilot sorties of two different accounts; assists are never counted here. Hidden
players and missions count (FR-ADM-3). Per tour: the same counted over that tour's missions only (`tour_ids` limits
which tours' rows are rewritten, None = all); the all-time rows are the roll-up of the tour rows (`ingest.rollup`),
never a read of `Kill`.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from django.db.models import F, Q, QuerySet

from il2ks.db.models import Kill, KillCredit, PlayerTypeKillboard, Role
from il2ks.ingest.dbutil import delete_pks, update_rows
from il2ks.ingest.rollup import Row, rollup

type _Key = tuple[int, int, int]  # player, tour, enemy aircraft


@dataclass(slots=True)
class _Cell:
    kills: int = 0
    deaths: int = 0
    kills_with: Counter[int] = field(default_factory=Counter[int])
    deaths_in: Counter[int] = field(default_factory=Counter[int])


def _most_used(counts: Mapping[int, int]) -> int | None:
    """The aircraft id with the highest count, the lowest id on a tie; None for no entries."""
    return min(counts, key=lambda aircraft: (-counts[aircraft], aircraft)) if counts else None


def _stored(counts: Mapping[int, int]) -> dict[str, int]:
    """The JSON form of a per-type count: string keys, sorted so equal counts compare equal."""
    return {str(aircraft): counts[aircraft] for aircraft in sorted(counts)}


def type_kills(chunk: list[int], tours: set[int] | None) -> QuerySet[Kill]:
    """The kill rows the per-tour type rows of these players are counted from: of the missions of `tours` (None = every
    tour), kill credits between pilot sorties of two accounts, one of them in `chunk`."""
    kills = Kill.objects.filter(
        Q(killer_sortie__player_id__in=chunk) | Q(victim_sortie__player_id__in=chunk),
        credit=KillCredit.KILL,
        is_friendly=False,
        killer_sortie__role=Role.PILOT,
        victim_sortie__role=Role.PILOT,
        mission__tour_id__isnull=False,
    )
    if tours is not None:
        kills = kills.filter(mission__tour_id__in=tours)
    return kills.exclude(killer_sortie__player_id=F("victim_sortie__player_id"))


def recompute_type_killboard(chunk: list[int], tour_ids: Iterable[int] | None = None) -> None:
    """Make the per-tour type-killboard rows of every player in `chunk` equal what the kills of `tour_ids` (None = every
    tour) say. Each row also keeps the per-own-type counts (`kills_with_counts`, `deaths_in_counts`), which the all-time
    most-used types are derived from (`rollup_type_killboard`)."""
    players = set(chunk)
    tours = None if tour_ids is None else set(tour_ids)
    rows = type_kills(chunk, tours).values_list(
        "killer_sortie__player_id",
        "victim_sortie__player_id",
        "killer_sortie__aircraft_id",
        "victim_sortie__aircraft_id",
        "mission__tour_id",
    )
    cells: dict[_Key, _Cell] = {}
    for killer, victim, killer_aircraft, victim_aircraft, tour_id in rows.iterator():
        if killer in players:
            cell = cells.setdefault((killer, tour_id, victim_aircraft), _Cell())
            cell.kills += 1
            cell.kills_with[killer_aircraft] += 1
        if victim in players:
            cell = cells.setdefault((victim, tour_id, killer_aircraft), _Cell())
            cell.deaths += 1
            cell.deaths_in[victim_aircraft] += 1

    existing_rows = PlayerTypeKillboard.objects.filter(player_id__in=chunk, tour_id__isnull=False)
    if tours is not None:
        existing_rows = existing_rows.filter(tour_id__in=tours)
    existing = {(r.player_id, r.tour_id, r.enemy_aircraft_id): r for r in existing_rows}
    changed: list[PlayerTypeKillboard] = []
    new: list[PlayerTypeKillboard] = []
    for key, cell in cells.items():
        values = (
            cell.kills,
            cell.deaths,
            _most_used(cell.kills_with),
            _most_used(cell.deaths_in),
            _stored(cell.kills_with),
            _stored(cell.deaths_in),
        )
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
                    kills_with_counts=values[4],
                    deaths_in_counts=values[5],
                )
            )
        elif (
            row.kills,
            row.deaths,
            row.kills_with_id,
            row.deaths_in_id,
            row.kills_with_counts,
            row.deaths_in_counts,
        ) != values:
            row.kills, row.deaths, row.kills_with_id, row.deaths_in_id = values[:4]
            row.kills_with_counts, row.deaths_in_counts = values[4], values[5]
            changed.append(row)
    delete_pks(PlayerTypeKillboard.objects, [r.pk for r in existing.values()])  # nothing left to count
    update_rows(
        PlayerTypeKillboard,
        changed,
        ["kills", "deaths", "kills_with", "deaths_in", "kills_with_counts", "deaths_in_counts"],
    )
    PlayerTypeKillboard.objects.bulk_create(new)


def rollup_type_killboard(chunk: list[int]) -> None:
    """The all-time rows (`tour` NULL) of these players from their per-tour rows: kills and deaths SUM, `kills_with` /
    `deaths_in` the most used own type of the counts added up over the tours (not the tours' winners: those can't be
    combined; ties go to the lowest id, as in a tour)."""

    def merged(field_name: str) -> Callable[[Sequence[Row]], object]:
        def most_used(rows: Sequence[Row]) -> object:
            total: Counter[int] = Counter()
            for row in rows:
                for aircraft, n in cast(Mapping[str, int], row[field_name]).items():
                    total[int(aircraft)] += n
            return _most_used(total)

        return most_used

    rollup(
        PlayerTypeKillboard,
        PlayerTypeKillboard.objects.filter(player_id__in=chunk, tour_id__isnull=True),
        PlayerTypeKillboard.objects.filter(player_id__in=chunk, tour_id__isnull=False),
        key=("player_id", "enemy_aircraft_id"),
        sums=("kills", "deaths"),
        derived={"kills_with_id": merged("kills_with_counts"), "deaths_in_id": merged("deaths_in_counts")},
        source_fields=("kills_with_counts", "deaths_in_counts"),
        fixed={"tour_id": None},
    )
