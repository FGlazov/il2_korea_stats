"""Reads for "what do they fly with" on the player profile (FR-WEB-4, doc 16): the `PlayerAircraftBuild` rows.

    player_builds(player, tour) -> dict[aircraft id, AircraftBuild]    # ONE query for all the player's types

Plain SELECT of level-2 rows plus shares computed from them (TD-22). Scoping follows the profile's per-aircraft table:
all time, or the rows of `tour` (the player's `PlayerTourAircraft` scope).
"""

from collections import defaultdict
from dataclasses import dataclass

from il2ks.db.models import BuildKind, Player, PlayerAircraftBuild, Tour
from il2ks.queries.ammo import ammo_info


@dataclass(frozen=True, slots=True)
class LoadoutShare:
    """One loadout of an aircraft type and the share of the player's sorties in it. `name` is '' for a payload the
    shipped file does not know (the page shows the raw `payload_id`)."""

    payload_id: int
    name: str
    sorties: int
    percent: int


@dataclass(frozen=True, slots=True)
class ModShare:
    """One `WM` value (weapon-modification bitmask) and its share of the sorties."""

    mods: int
    sorties: int
    percent: int


@dataclass(frozen=True, slots=True)
class AmmoShare:
    """A gun ammo (plain display name, `ammo_info`) and its share of the player's gun hits in this type."""

    name: str
    designation: str
    hits: int
    percent: int


@dataclass(frozen=True, slots=True)
class AircraftBuild:
    aircraft_id: int
    sorties: int
    loadouts: tuple[LoadoutShare, ...]  # most flown first, the favourite first
    mods: tuple[ModShare, ...]
    ammo: tuple[AmmoShare, ...]  # most hits first; hits by ammo, not the belt the player picked


def _percent(part: int, whole: int) -> int:
    return max(round(100 * part / whole), 1) if part and whole else 0


def player_builds(player: Player, tour: Tour | None = None) -> dict[int, AircraftBuild]:
    """The builds of the player's aircraft types, all time or within `tour`; empty for a type without rows."""
    rows = PlayerAircraftBuild.objects.filter(player=player)
    rows = rows.filter(tour_id__isnull=True) if tour is None else rows.filter(tour=tour)
    by_type: dict[int, list[PlayerAircraftBuild]] = defaultdict(list)
    for row in rows.order_by("aircraft_id", "-sorties", "-hits", "value", "label"):
        by_type[row.aircraft_id].append(row)
    return {aircraft_id: _build(aircraft_id, group) for aircraft_id, group in by_type.items()}


def _build(aircraft_id: int, rows: list[PlayerAircraftBuild]) -> AircraftBuild:
    payloads = [r for r in rows if r.kind == BuildKind.PAYLOAD.value]
    mods = [r for r in rows if r.kind == BuildKind.MODS.value]
    total = sum(r.sorties for r in payloads)
    merged: dict[str, tuple[str, int]] = {}  # two log names with one plain name (a tracer variant) add up
    for r in rows:
        if r.kind == BuildKind.AMMO.value:
            info = ammo_info(r.label)
            _, hits = merged.get(info.name, (info.designation, 0))
            merged[info.name] = (info.designation, hits + r.hits)
    hits_total = sum(hits for _, hits in merged.values())
    ammo = sorted(merged.items(), key=lambda item: (-item[1][1], item[0]))
    return AircraftBuild(
        aircraft_id,
        total,
        tuple(LoadoutShare(r.value, r.label, r.sorties, _percent(r.sorties, total)) for r in payloads),
        tuple(ModShare(r.value, r.sorties, _percent(r.sorties, total)) for r in mods),
        tuple(AmmoShare(name, designation, hits, _percent(hits, hits_total)) for name, (designation, hits) in ammo),
    )
