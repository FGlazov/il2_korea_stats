"""Reads for "what do they fly with" on the player profile (FR-WEB-4, doc 16): the `PlayerAircraftBuild` rows.

    player_builds(player, tour) -> dict[aircraft id, AircraftBuild]    # ONE query for all the player's types

Plain SELECT of level-2 rows plus shares computed from them (TD-22). Scoping follows the profile's per-aircraft table:
all time, or the rows of `tour` (the player's `PlayerTourAircraft` scope).
"""

from collections import defaultdict
from dataclasses import dataclass

from il2ks.db.models import BuildKind, Player, PlayerAircraftBuild, Tour


@dataclass(frozen=True, slots=True)
class LoadoutShare:
    """One loadout of an aircraft type and the share of the player's sorties in it. `name` is '' for a payload the
    shipped file does not know (the page shows the raw `payload_id`)."""

    payload_id: int
    name: str
    sorties: int
    percent: int


@dataclass(frozen=True, slots=True)
class AircraftBuild:
    aircraft_id: int
    sorties: int
    loadouts: tuple[LoadoutShare, ...]  # most flown first, the favourite first


def _percent(part: int, whole: int) -> int:
    return max(round(100 * part / whole), 1) if part and whole else 0


def player_builds(player: Player, tour: Tour | None = None) -> dict[int, AircraftBuild]:
    """The builds of the player's aircraft types, all time or within `tour`; empty for a type without rows."""
    rows = PlayerAircraftBuild.objects.filter(player=player)
    rows = rows.filter(tour_id__isnull=True) if tour is None else rows.filter(tour=tour)
    by_type: dict[int, list[PlayerAircraftBuild]] = defaultdict(list)
    for row in rows.order_by("aircraft_id", "-sorties", "value", "label"):
        by_type[row.aircraft_id].append(row)
    return {aircraft_id: _build(aircraft_id, group) for aircraft_id, group in by_type.items()}


def _build(aircraft_id: int, rows: list[PlayerAircraftBuild]) -> AircraftBuild:
    payloads = [r for r in rows if r.kind == BuildKind.PAYLOAD.value]
    total = sum(r.sorties for r in payloads)
    return AircraftBuild(
        aircraft_id,
        total,
        tuple(LoadoutShare(r.value, r.label, r.sorties, _percent(r.sorties, total)) for r in payloads),
    )
