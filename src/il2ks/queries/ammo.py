"""Reads for the ammo breakdown pages (FR-WEB-18): one sortie's ammo, and hits to destroy per aircraft type.

Simple reads only (TD-22): the numbers were aggregated at ingest, here they are looked up and divided.

    sortie_ammo(sortie)          -> SortieAmmo         # a `PlayerSortie` row (its `ammo` JSON, shape in ingest.persist)
    sortie_ammo_by_pk(pk)        -> SortieAmmo | None  # one query
    aircraft_ammo(aircraft)      -> AircraftAmmo       # a `GameObject`; one query
    all_aircraft_ammo()          -> list[AircraftAmmo] # every aircraft type that was destroyed by gun hits; one query

`SortieAmmo.guns` and `.ordnance` are what the page lists: gun rows have hits and the damage attributed to them,
ordnance rows have released / detonations / targets damaged / kills (an explosion is never shown as a "hit", and the
ordnance is always named: `ordnance_name`). Damage the closest-hit rule couldn't attribute is `unattributed_*`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import cast

from il2ks.core.catalog.loader import GENERIC_ORDNANCE, Catalog, load_default_catalog
from il2ks.core.replay.result import UNATTRIBUTED_ORDNANCE
from il2ks.db.models import TOTAL_AMMO, AircraftAmmoStats, GameObject, PlayerSortie

UNATTRIBUTED_NAME = "Unattributed"
"""English default shown for `UNATTRIBUTED_ORDNANCE` (explosions no labelling rule could name). Translate it."""


@dataclass(frozen=True, slots=True)
class GunAmmoRow:
    """A bullet or shell type: hit lines given and received, and the damage attributed to it (closest-hit rule)."""

    ammo: str  # log name, e.g. "BULLET_12-7_USA_API"
    hits_given: int
    hits_received: int
    damage_dealt: float  # fractions of an object, summed (1.0 = one object destroyed)
    damage_taken: float


@dataclass(frozen=True, slots=True)
class OrdnanceRow:
    """A bomb, rocket or napalm type (or a generic or unattributed one, see `ordnance_name`)."""

    ordnance: str  # key: "M65", "HVAR", "NAPALM", "bombs_mixed", "rockets_mixed" or "unattributed"
    name: str  # English display name (TD-24)
    released: int  # stores (AType 25) or rocket salvos (AType 26); drop tanks excluded
    detonations: int
    targets_damaged: int  # one detonation x one target that took damage
    kills: int
    direct_hits: int  # named hit lines given (a direct impact)
    damage_dealt: float
    damage_taken: float


@dataclass(frozen=True, slots=True)
class SortieAmmo:
    guns: tuple[GunAmmoRow, ...]
    ordnance: tuple[OrdnanceRow, ...]
    other_hit_lines: tuple[GunAmmoRow, ...]  # named non-gun hit lines as logged (bombs, rockets, napalm, flares)
    unattributed_dealt: float
    unattributed_taken: float

    @property
    def has_ordnance(self) -> bool:
        return any(o.released or o.detonations or o.direct_hits for o in self.ordnance)


@dataclass(frozen=True, slots=True)
class AmmoToDestroy:
    """Hits of one gun ammo (or `ammo == TOTAL_AMMO`: all gun ammo) it took to destroy this aircraft type."""

    ammo: str
    kills: int  # counted kills in which this ammo hit at least once (all counted kills for the total)
    hits: int

    @property
    def average_hits(self) -> float:
        return self.hits / self.kills if self.kills else 0.0


@dataclass(frozen=True, slots=True)
class AircraftAmmo:
    aircraft_id: int
    aircraft: GameObject  # for `{{ aircraft|object_name }}` (TD-24) and the icon
    total: AmmoToDestroy | None  # all gun ammo together; None if no kill was counted
    by_ammo: tuple[AmmoToDestroy, ...]  # most used first


@cache
def _catalog() -> Catalog:
    return load_default_catalog()


@cache
def _ordnance_names() -> Mapping[str, str]:
    return {o.key: o.display_name for o in _catalog().all_ordnance()} | dict(GENERIC_ORDNANCE)


def ordnance_name(key: str) -> str:
    """English display name of an ordnance key (`M65` -> "M65 1000 lb General Purpose bomb"); the key if unknown."""
    if key == UNATTRIBUTED_ORDNANCE:
        return UNATTRIBUTED_NAME
    return _ordnance_names().get(key, key)


def _number(row: Mapping[str, object], key: str) -> float:
    value = row.get(key)
    return float(value) if isinstance(value, int | float) else 0.0


def _as_mapping(value: object) -> Mapping[str, object]:
    """A JSON object as a typed mapping; anything else (missing key, old row shape) as an empty one."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def _rows(data: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    raw = data.get(key)
    if not isinstance(raw, list):
        return []
    return [_as_mapping(item) for item in cast(list[object], raw)]


def parse_sortie_ammo(ammo: Mapping[str, object]) -> SortieAmmo:
    """The ammo breakdown from a `PlayerSortie.ammo` value. Rows written before FR-WEB-18 (hit counts only) parse too:
    the damage is 0 and there is no ordnance."""
    catalog = _catalog()
    guns: list[GunAmmoRow] = []
    other: list[GunAmmoRow] = []
    for row in _rows(ammo, "hits"):
        name = str(row.get("ammo", ""))
        parsed = GunAmmoRow(
            name,
            int(_number(row, "hits_given")),
            int(_number(row, "hits_received")),
            _number(row, "damage_dealt"),
            _number(row, "damage_taken"),
        )
        (other if catalog.ordnance_for_ammo(name) is not None else guns).append(parsed)
    ordnance = tuple(
        OrdnanceRow(
            key := str(row.get("ordnance", UNATTRIBUTED_ORDNANCE)),
            ordnance_name(key),
            int(_number(row, "released")),
            int(_number(row, "detonations")),
            int(_number(row, "targets_damaged")),
            int(_number(row, "kills")),
            int(_number(row, "direct_hits")),
            _number(row, "damage_dealt"),
            _number(row, "damage_taken"),
        )
        for row in _rows(ammo, "ordnance")
    )
    lost = _as_mapping(ammo.get("unattributed"))
    return SortieAmmo(tuple(guns), ordnance, tuple(other), _number(lost, "dealt"), _number(lost, "taken"))


def sortie_ammo(sortie: PlayerSortie) -> SortieAmmo:
    return parse_sortie_ammo(sortie.ammo)


def sortie_ammo_by_pk(pk: int) -> SortieAmmo | None:
    ammo = PlayerSortie.objects.filter(pk=pk).values_list("ammo", flat=True).first()
    return None if ammo is None else parse_sortie_ammo(ammo)


def _group(rows: Sequence[AircraftAmmoStats]) -> list[AircraftAmmo]:
    by_aircraft: dict[int, list[AircraftAmmoStats]] = {}
    for row in rows:
        by_aircraft.setdefault(row.aircraft_id, []).append(row)
    out: list[AircraftAmmo] = []
    for aircraft_id, group in by_aircraft.items():
        aircraft = group[0].aircraft
        total = next((AmmoToDestroy(r.ammo, r.kills, r.hits) for r in group if r.ammo == TOTAL_AMMO), None)
        guns = sorted((r for r in group if r.ammo != TOTAL_AMMO), key=lambda r: (-r.kills, r.ammo))
        out.append(
            AircraftAmmo(
                aircraft_id,
                aircraft,
                total,
                tuple(AmmoToDestroy(r.ammo, r.kills, r.hits) for r in guns),
            )
        )
    return sorted(out, key=lambda a: -(a.total.kills if a.total else 0))


def aircraft_ammo(aircraft: GameObject) -> AircraftAmmo:
    """Hits to destroy this aircraft type; empty (`total` None) if no kill was counted."""
    rows = list(AircraftAmmoStats.objects.filter(aircraft=aircraft).select_related("aircraft"))
    found = _group(rows)
    return found[0] if found else AircraftAmmo(aircraft.pk, aircraft, None, ())


def all_aircraft_ammo() -> list[AircraftAmmo]:
    """Every aircraft type with counted kills, the most-killed first."""
    return _group(list(AircraftAmmoStats.objects.select_related("aircraft")))
