"""Object, payload and coalition reference data (TD-24, FR-ING-7).

Lookups never raise: an object type that isn't in the shipped data comes back with `is_known=False` and class `unknown`
(TD-20: "the replay never indexes a catalog dict directly").

Shipped data (`core/catalog/data/`):
- `objects.csv`: `log_name, display_name, cls, is_playable, propulsion, ground_category`, one row per object type seen
  in the logs (doc 12). `propulsion` is `prop` or `jet` for aircraft and empty for everything else. `ground_category`
  (what a ground kill is shown as, doc 13) is set for every ground class (`GROUND_CLASSES`) and empty for the rest.
- `object_names_<language>.csv` (one per translated language, `pt-br` style codes):
  `log_name, english, display_name, review`. Only objects whose name differs from English have a row (an aircraft
  type name usually stays as it is). `english` is the `objects.csv` name, repeated so a reviewer sees both without a
  lookup (a test keeps it in step). `review` is `llm-draft` for a machine translation nobody has checked; a reviewer
  empties it. Names fall back from the viewer's language to English (`Catalog.translated_name`, TD-24).
- `payloads.csv`: `vehicle, payload_id, editor_name, readable_name` (doc 12 "Payloads").
- `ammo.csv`: `log_name, name, calibre, round_type, designation`, the plain-text name of every ammo (bullet, shell,
  rocket, bomb, flare) the logs name (FR-WEB-18): `.50 BMG API`, `23x115 mm HEI-T` (a multiplication sign in the data),
  `HVAR 5 in`. `designation` is the
  real-world one for a tooltip (`M8 API`). The names are proper technical designations, so they are not translated.
  `_HIT` and `_xN` sub-objects belong to their parent (`Catalog.ammo`); an ammo missing here gets a cleaned-up log name.
- `weapon_mods.csv`: `vehicle, mod_id, name, significant`, the modifications a pilot can pick for a type;
  `significant` (`true`/`false`) marks the ones that change performance a lot: the aircraft page offers a with/without
  filter for each (a change applies with `il2ks rebuild-aggregates`). The spawn line's `WM` is a bitmask:
  bit 0 is always set, mod `k` is bit `k` (`weapon_mod_ids`).
- `weapon_mod_names_<language>.csv` (like `object_names_<language>.csv`): `english, display_name, review`. Mod names are
  translated (a dimension, unlike mission names, which are facts); keyed by the English `weapon_mods.csv` name because
  one name is shared by several types. Every catalog name has a row in every language (a test), `review` as above.
  A name with no row (or an id the catalog lacks) shows as it is (`Catalog.translated_mod_name`).
- `object_aliases.csv`: `log_name, same_as`, a second spelling of a catalog object the logs write (`B 29` -> `B-29`,
  OQ-120): `lookup` answers with the object itself, so both spellings are one `GameObject`.
- `payload_aliases.csv`: `log_name, vehicle`, log aircraft name -> `payloads.csv` / `weapon_mods.csv` vehicle key
  (`F-86A-5` -> `f-86a-5`).
- `ordnance.csv`: `key, kind, display_name, payload_tokens, hit_ammo`, what a payload carries and which logged ammo
  names are that ordnance (FR-WEB-18). `payload_tokens` are the `payloads.csv` editor-name tokens (`M64`, `HVAR`),
  `hit_ammo` the named hit-line ammo (prefix match, so `_HIT` and `_x8` variants belong to the same ordnance).
"""

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from itertools import product
from typing import Literal, TypeIs

type ObjectClass = Literal[
    "fighter",
    "attacker",
    "bomber",
    "transport",
    "gunner",
    "vehicle",
    "tank",
    "aaa",
    "ship",
    "static",
    "ordnance",
    "crew",
    "equipment",
    "unknown",
]
"""Same values as `il2ks.db.models.ObjectClass`. `crew` = bots and crew groups, `equipment` = parachutes, ejection
seats, spotters and vehicle sub-objects (turrets, rangefinders)."""

OBJECT_CLASSES: frozenset[ObjectClass] = frozenset(
    {
        "fighter",
        "attacker",
        "bomber",
        "transport",
        "gunner",
        "vehicle",
        "tank",
        "aaa",
        "ship",
        "static",
        "ordnance",
        "crew",
        "equipment",
        "unknown",
    }
)
AIR_CLASSES: frozenset[ObjectClass] = frozenset({"fighter", "attacker", "bomber", "transport", "gunner"})

type Propulsion = Literal["prop", "jet"]
"""Piston-engined or jet. Same values as `il2ks.db.models.Propulsion`. Used to pool air-to-air ratings."""

PROPULSIONS: frozenset[Propulsion] = frozenset({"prop", "jet"})

type GroundCategory = Literal[
    "tank", "vehicle", "artillery", "aaa", "ship", "train", "building", "parked_aircraft", "other"
]
"""What a ground kill is shown as (OQ-33, doc 13). Independent of static vs dynamic: a static truck is a `vehicle`.
Same values as `il2ks.db.models.GroundCategory`."""

GROUND_CATEGORIES: tuple[GroundCategory, ...] = (
    "tank",
    "vehicle",
    "artillery",
    "aaa",
    "ship",
    "train",
    "building",
    "parked_aircraft",
    "other",
)
"""In display order."""
GROUND_CLASSES: frozenset[ObjectClass] = frozenset({"tank", "vehicle", "aaa", "ship", "static"})
"""Object classes that count as ground kills when destroyed; each needs a ground category in `objects.csv`."""

COALITION_NAMES: Mapping[int, str] = {1: "REDFOR", 2: "BLUFOR"}
"""Doc 06: every country of a coalition displays as the plain coalition name."""
NEUTRAL = "Neutral"

type Side = Literal["redfor", "blufor"]
"""The side a country is shown as: from the country code, not from the coalition number (doc 06)."""

SIDE_NAMES: Mapping[Side, str] = {"redfor": "REDFOR", "blufor": "BLUFOR"}
_SIDE_BY_HUNDREDS: Mapping[int, Side] = {5: "redfor", 6: "blufor"}
_CONVENTIONAL_SIDE_OF_COALITION: Mapping[int, Side] = {1: "redfor", 2: "blufor"}

# Static block objects carry their block group and index: `GAZ_63[64606,0]`, `Industrial storage tank D[-1,-1]`.
_BLOCK_SUFFIX = re.compile(r"\[-?\d+,-?\d+\]$")
# Types that carry a per-instance number: `CParachute_2361344`.
_NUMBERED_TYPE = re.compile(r"(CParachute)_\d+", re.IGNORECASE)


def language_chain(language: str) -> tuple[str, ...]:
    """The languages to try for a viewer language, most specific first: `pt_BR` -> ("pt-br", "pt"), `de` -> ("de",).

    English is not in the chain: it is the default name itself."""
    code = language.strip().lower().replace("_", "-")
    if not code or code == "en" or code.startswith("en-"):
        return ()
    base = code.split("-", 1)[0]
    return (code,) if base == code else (code, base)


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    log_name: str  # as written in the log (TYPE), original case
    display_name: str  # English default (TD-24)
    cls: ObjectClass
    is_playable: bool
    is_known: bool
    propulsion: Propulsion | None = None  # aircraft only (fighter, attacker, bomber, transport); None otherwise
    ground_category: GroundCategory | None = None  # ground classes only (`GROUND_CLASSES`); None otherwise

    @property
    def is_air(self) -> bool:
        return self.cls in AIR_CLASSES

    @property
    def is_static(self) -> bool:
        return self.cls == "static"


type OrdnanceKind = Literal["bomb", "rocket", "napalm", "flare", "tank", "inert"]
"""`bomb` includes cluster bombs. `flare` bombs never damage anything. `tank` (drop tanks) and `inert` (empty, pylons)
aren't ordnance: they are known so that every payload token is accounted for."""

ORDNANCE_KINDS: frozenset[OrdnanceKind] = frozenset({"bomb", "rocket", "napalm", "flare", "tank", "inert"})
DAMAGING_KINDS: frozenset[OrdnanceKind] = frozenset({"bomb", "rocket", "napalm"})
"""The kinds whose detonations can damage targets (what explosion hits are labelled with)."""

GENERIC_BOMBS = "bombs_mixed"
GENERIC_ROCKETS = "rockets_mixed"
GENERIC_ORDNANCE: Mapping[str, str] = {
    GENERIC_BOMBS: "Bombs (mixed loadout)",
    GENERIC_ROCKETS: "Rockets (mixed loadout)",
}
"""Stand-in ordnance keys for a release whose type the log can't tell apart: the loadout holds several bomb (or rocket)
types and AType 25/26 doesn't name the store (doc 12). Display names; the page translates them like other names."""


@dataclass(frozen=True, slots=True)
class OrdnanceInfo:
    key: str  # short stable id, e.g. "M65", "HVAR", "NAPALM"
    kind: OrdnanceKind
    display_name: str  # English default (TD-24)


@dataclass(frozen=True, slots=True)
class AmmoInfo:
    """The plain-text name of one logged ammo. `is_known` is False for an unlisted name (its `name` is then
    a cleaned-up log name and the other fields are empty)."""

    log_name: str
    name: str  # `.50 BMG API`
    calibre: str  # `.50 BMG`, `23x115 mm`, `5 in`; empty for bombs and flares
    round_type: str  # `API`, `HEI-T`, `ball`; empty when there is none to tell apart
    designation: str  # real-world designation, for a tooltip (`M8 API`)
    is_known: bool = True


@dataclass(frozen=True, slots=True)
class LoadoutItem:
    """One line of a payload: `count` pieces of one ordnance type (a `tank` or `inert` item is kept too)."""

    ordnance: OrdnanceInfo
    count: int


@dataclass(frozen=True, slots=True)
class OrdnanceRow:
    """One `ordnance.csv` row: the type, the payload tokens that carry it, the hit-line ammo names that are it."""

    info: OrdnanceInfo
    payload_tokens: tuple[str, ...]
    hit_ammo: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PayloadInfo:
    aircraft: str  # catalog vehicle key, e.g. "f-86a"
    payload_id: int
    editor_name: str
    readable_name: str


@dataclass(frozen=True, slots=True)
class WeaponModInfo:
    aircraft: str  # catalog vehicle key, e.g. "mig-15bis"
    mod_id: int
    name: str
    significant: bool = False  # changes performance a lot: the aircraft page can filter by it


def weapon_mod_ids(weapon_mods: int) -> tuple[int, ...]:
    """The modification ids a spawn line's `WM` bitmask selects, ascending (doc 12).

    Mapping, verified on ~30,700 spawns of 8 types: bit 0 is always set and means nothing (the base aircraft), mod `k`
    is bit `k` (value `2**k`). The ONE place that knows this: to correct the mapping, change this function (and
    `weapon_mod_mask`, its inverse for database filters)."""
    return tuple(bit for bit in range(1, max(weapon_mods, 0).bit_length()) if weapon_mods >> bit & 1)


def weapon_mod_mask(mod_ids: Iterable[int]) -> int:
    """The `WM` bits of the given modification ids (the inverse of `weapon_mod_ids`, without the base bit): filter
    sorties that have all of them with `weapon_mods & mask == mask`."""
    mask = 0
    for mod_id in mod_ids:
        mask |= 1 << mod_id
    return mask


MOD_ANY, MOD_WITH, MOD_WITHOUT = "*", "+", "-"
"""One character of a filter pattern: any / with / without the significant modification at that position."""


def mod_filter_patterns(weapon_mods: int, significant: Sequence[int]) -> tuple[str, ...]:
    """The stored filter patterns a sortie with this `WM` belongs to: one character per significant modification of its
    type (`significant`, ascending ids), each `*` (any) or the sortie's own state (`+` has it, `-` has not), every
    combination except all-`*` (that is the unfiltered scope, pattern ''). A type without significant mods has none:
    2**n - 1 patterns of the 3**n - 1 filters, ascending. The ONE place that knows the pattern format."""
    own = [MOD_WITH if weapon_mods >> mod_id & 1 else MOD_WITHOUT for mod_id in significant]
    combos = product(*[(MOD_ANY, state) for state in own])
    return tuple(sorted(pattern for pattern in ("".join(c) for c in combos) if set(pattern) != {MOD_ANY} and pattern))


def mod_filter_pattern(states: Sequence[str]) -> str:
    """The pattern of per-mod states (`*`, `+`, `-`); '' (unfiltered) when all are any or there are none."""
    return "" if all(s == MOD_ANY for s in states) else "".join(states)


def canonical_type_name(object_type: str) -> str:
    """The catalog name for a log `TYPE`: block suffix and per-instance numbers removed, case kept.

    `GAZ_63[64606,0]` -> `GAZ_63`, `CParachute_2361344` -> `CParachute`, `MiG-15bis` -> `MiG-15bis`.
    """
    name = object_type.strip()
    stripped = _BLOCK_SUFFIX.sub("", name).rstrip()
    if stripped:
        name = stripped
    m = _NUMBERED_TYPE.fullmatch(name)
    return m.group(1) if m else name


def _key(name: str) -> str:
    return canonical_type_name(name).casefold()


def side_of_country(code: int) -> Side | None:
    """REDFOR/BLUFOR from the hundreds digit of a country code: 5xx = REDFOR, 6xx = BLUFOR, anything else none (doc 06).

    The one place that knows this mapping. Coalition numbers (CNTRS) still decide friend or foe, not this."""
    return _SIDE_BY_HUNDREDS.get(code // 100)


def country_side_warnings(countries: Mapping[int, int]) -> list[str]:
    """Where a mission's CNTRS (country -> coalition) disagrees with `side_of_country` in a way that matters.

    Warns when one coalition holds both 5xx and 6xx countries (friend/foe and the shown side would contradict), when a
    coalition's side isn't the conventional one (coalition 1 = REDFOR, 2 = BLUFOR: winner and side shown would swap),
    and when a country in coalition 1 or 2 has no side (its sorties would count for neither). Coalition 0 is fine.
    """
    warnings: list[str] = []
    by_coalition: dict[int, list[int]] = {}
    for code, coalition in sorted(countries.items()):
        by_coalition.setdefault(coalition, []).append(code)
    for coalition, codes in sorted(by_coalition.items()):
        if coalition == 0:
            continue
        sides = {side_of_country(c) for c in codes}
        named = sorted(s for s in sides if s is not None)
        if len(named) > 1:
            warnings.append(f"coalition {coalition} mixes REDFOR and BLUFOR countries: {codes}")
        elif (
            named
            and coalition in _CONVENTIONAL_SIDE_OF_COALITION
            and named[0] != _CONVENTIONAL_SIDE_OF_COALITION[coalition]
        ):
            warnings.append(
                f"coalition {coalition} holds {named[0].upper()} countries {codes}, expected "
                f"{_CONVENTIONAL_SIDE_OF_COALITION[coalition].upper()}"
            )
        if None in sides:
            nameless = [c for c in codes if side_of_country(c) is None]
            warnings.append(f"countries {nameless} in coalition {coalition} are neither 5xx nor 6xx: no side")
    return warnings


def is_object_class(value: str) -> TypeIs[ObjectClass]:
    return value in OBJECT_CLASSES


def is_ground_category(value: str) -> TypeIs[GroundCategory]:
    return value in GROUND_CATEGORIES


def is_ordnance_kind(value: str) -> TypeIs[OrdnanceKind]:
    return value in ORDNANCE_KINDS


def is_propulsion(value: str) -> TypeIs[Propulsion]:
    return value in PROPULSIONS


class Catalog:
    """Read-only reference data. Build it once per process and pass it in (TD-11: no import-time globals).

    `Catalog()` with no arguments is an empty catalog: every object is unknown and no payload resolves.
    """

    def __init__(
        self,
        objects: Iterable[ObjectInfo] = (),
        payloads: Iterable[PayloadInfo] = (),
        payload_aliases: Mapping[str, str] | None = None,
        weapon_mods: Iterable[WeaponModInfo] = (),
        object_names: Mapping[str, Mapping[str, str]] | None = None,
        ordnance: Iterable[OrdnanceRow] = (),
        ammo: Iterable[AmmoInfo] = (),
        object_aliases: Mapping[str, str] | None = None,
        weapon_mod_names: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        """`object_names`: language code (`de`, `pt-br`) -> {log name -> translated display name}.
        `weapon_mod_names`: language code -> {English mod name from `weapon_mods.csv` -> translated name}."""
        self._names: dict[str, dict[str, str]] = {
            language.lower(): {_key(log_name): name for log_name, name in names.items()}
            for language, names in (object_names or {}).items()
        }
        self._mod_names: dict[str, dict[str, str]] = {
            language.lower(): {_key(english): name for english, name in names.items()}
            for language, names in (weapon_mod_names or {}).items()
        }
        self._objects: dict[str, ObjectInfo] = {}
        for obj in objects:
            key = _key(obj.log_name)
            if key in self._objects:
                raise ValueError(f"duplicate object type {obj.log_name!r} in the catalog")
            self._objects[key] = obj
        for alias, same_as in (object_aliases or {}).items():
            target = self._objects.get(_key(same_as))
            if target is None or _key(alias) in self._objects:
                raise ValueError(
                    f"object alias {alias!r} -> {same_as!r}: unknown target or the alias is an object itself"
                )
            self._objects[_key(alias)] = target
        self._payloads: dict[tuple[str, int], PayloadInfo] = {}
        for p in payloads:
            pkey = (p.aircraft.casefold(), p.payload_id)
            if pkey in self._payloads:
                raise ValueError(f"duplicate payload {p.aircraft!r} #{p.payload_id} in the catalog")
            self._payloads[pkey] = p
        self._weapon_mods: dict[tuple[str, int], WeaponModInfo] = {}
        for mod in weapon_mods:
            mkey = (mod.aircraft.casefold(), mod.mod_id)
            if mkey in self._weapon_mods:
                raise ValueError(f"duplicate weapon mod {mod.aircraft!r} #{mod.mod_id} in the catalog")
            self._weapon_mods[mkey] = mod
        self._aliases: dict[str, str] = {_key(k): v.casefold() for k, v in (payload_aliases or {}).items()}
        self._ordnance: dict[str, OrdnanceInfo] = {}
        self._tokens: dict[str, OrdnanceInfo] = {}
        self._ammo_prefixes: list[tuple[str, OrdnanceInfo]] = []
        for row in ordnance:
            if row.info.key in self._ordnance:
                raise ValueError(f"duplicate ordnance {row.info.key!r} in the catalog")
            self._ordnance[row.info.key] = row.info
            for token in row.payload_tokens:
                if token.casefold() in self._tokens:
                    raise ValueError(f"payload token {token!r} belongs to two ordnance types")
                self._tokens[token.casefold()] = row.info
            self._ammo_prefixes += [(a, row.info) for a in row.hit_ammo]
        self._ammo_prefixes.sort(key=lambda pair: -len(pair[0]))  # longest first: HVAR_SAP before HVAR
        self._ammo_cache: dict[str, OrdnanceInfo | None] = {}
        self._loadouts: dict[tuple[str, int], tuple[LoadoutItem, ...] | None] = {}
        self._ammo: dict[str, AmmoInfo] = {}
        for item in ammo:
            if item.log_name in self._ammo:
                raise ValueError(f"duplicate ammo {item.log_name!r} in the catalog")
            self._ammo[item.log_name] = item

    def lookup(self, object_type: str) -> ObjectInfo:
        """Case-insensitive, alias-aware. Never raises.

        Block suffixes and per-instance numbers are ignored (`canonical_type_name`). Unknown types come back with
        `is_known=False`, class `unknown`, and the canonical log name as display name (FR-ING-7).
        """
        found = self._objects.get(_key(object_type))
        if found is not None:
            return found
        name = canonical_type_name(object_type)
        return ObjectInfo(log_name=name, display_name=name, cls="unknown", is_playable=False, is_known=False)

    def translated_name(self, object_type: str, language: str) -> str | None:
        """The shipped translation of an object's name for `language` (TD-24), or None: English, a language without
        translations, or an object nobody translated. The caller falls back to `lookup(...).display_name` (English),
        then the log name; admin overrides go on top of all this (`il2ks.web.object_names`)."""
        key = _key(object_type)
        for code in language_chain(language):
            translated = self._names.get(code, {}).get(key)
            if translated:
                return translated
        return None

    def payload(self, aircraft_type: str, payload_id: int) -> PayloadInfo | None:
        """Resolve AType 10 `PAYLOAD` through the alias list (log `F-86A-5` -> CSV `f-86a`, doc 12).

        Aircraft without an alias are tried under their own (case-insensitive) name. None if nothing matches.
        """
        key = _key(aircraft_type)
        vehicle = self._aliases.get(key, key)
        return self._payloads.get((vehicle, payload_id))

    def translated_mod_name(self, name: str, language: str) -> str:
        """A weapon modification's name (the English one of `weapon_mods.csv`) in `language` (TD-24); the English name
        itself for English, a language without translations, or a name nobody translated."""
        key = _key(name)
        for code in language_chain(language):
            translated = self._mod_names.get(code, {}).get(key)
            if translated:
                return translated
        return name

    def weapon_mods(
        self, aircraft_type: str, weapon_mods: int, language: str = "en"
    ) -> tuple[tuple[int, str | None], ...]:
        """The modifications a `WM` bitmask selects as (id, name) pairs, the names in `language` (TD-24);
        the name is None for an id the catalog doesn't list (OQ-25: the caller shows the raw id). Same alias handling
        as `payload`."""
        key = _key(aircraft_type)
        vehicle = self._aliases.get(key, key)
        result: list[tuple[int, str | None]] = []
        for mod_id in weapon_mod_ids(weapon_mods):
            found = self._weapon_mods.get((vehicle, mod_id))
            result.append((mod_id, self.translated_mod_name(found.name, language) if found is not None else None))
        return tuple(result)

    def significant_mods(self, aircraft_type: str) -> tuple[WeaponModInfo, ...]:
        """The significant modifications of the type (`weapon_mods.csv`) by ascending id; none for most types. Same
        alias handling as `payload`."""
        key = _key(aircraft_type)
        vehicle = self._aliases.get(key, key)
        found = [m for (v, _), m in self._weapon_mods.items() if v == vehicle and m.significant]
        return tuple(sorted(found, key=lambda m: m.mod_id))

    def ordnance(self, key: str) -> OrdnanceInfo | None:
        """The ordnance type with this key (`M65`, `HVAR`, ...); None for unknown and for the generic stand-in keys."""
        return self._ordnance.get(key)

    def all_ordnance(self) -> tuple[OrdnanceInfo, ...]:
        """Every ordnance type, in data order (display names for pages)."""
        return tuple(self._ordnance.values())

    def ordnance_for_ammo(self, ammo: str) -> OrdnanceInfo | None:
        """The ordnance a logged hit-line ammo name belongs to (`BOMB_449kg_USA_M65` -> M65, `RKT_127mm_USA_HVAR_HIT`
        -> HVAR, `NapalmBullet` -> NAPALM), or None for guns and anything else. Prefix match on the longest name."""
        if ammo in self._ammo_cache:
            return self._ammo_cache[ammo]
        found = next((info for base, info in self._ammo_prefixes if ammo == base or ammo.startswith(base + "_")), None)
        self._ammo_cache[ammo] = found
        return found

    def ammo(self, log_name: str) -> AmmoInfo:
        """The plain name of a logged ammo (`BULLET_12-7_USA_API` -> `.50 BMG API`). Never raises: `_HIT` and `_xN`
        sub-objects take their parent's entry (`RKT_127mm_USA_HVAR_HIT`), an unlisted name comes back cleaned up
        (`clean_ammo_name`) with `is_known=False`."""
        name = log_name
        while True:
            found = self._ammo.get(name)
            if found is not None:
                return found
            stripped = _SUB_OBJECT.sub("", name)
            if stripped == name or not stripped:
                break
            name = stripped
        return AmmoInfo(log_name, clean_ammo_name(log_name), "", "", "", is_known=False)

    def loadout(self, aircraft_type: str, payload_id: int) -> tuple[LoadoutItem, ...] | None:
        """What a payload carries, from its `payloads.csv` editor name (`M64-2 + HVAR-4` -> M64 x2, HVAR x4).

        None when the payload isn't in the catalog (or a token has no ordnance row): the caller must then treat the
        loadout as unknown, not as empty."""
        info = self.payload(aircraft_type, payload_id)
        if info is None:
            return None
        cache_key = (info.aircraft, info.payload_id)
        if cache_key not in self._loadouts:
            self._loadouts[cache_key] = self._parse_loadout(info.editor_name)
        return self._loadouts[cache_key]

    def _parse_loadout(self, editor_name: str) -> tuple[LoadoutItem, ...] | None:
        items: list[LoadoutItem] = []
        for token in editor_name.split("+"):
            name, count = _split_count(token.strip())
            found = self._tokens.get(name.casefold())
            if found is None:
                return None
            items.append(LoadoutItem(found, count))
        return tuple(items)

    def coalition_name(self, coalition: int) -> str:
        """1 -> "REDFOR", 2 -> "BLUFOR", others -> "Neutral" (doc 06)."""
        return COALITION_NAMES.get(coalition, NEUTRAL)

    def country_name(self, code: int, coalition: int) -> str:
        """Display name of a country: its side's plain name (REDFOR/BLUFOR), else the coalition's (doc 06)."""
        side = side_of_country(code)
        return SIDE_NAMES[side] if side is not None else self.coalition_name(coalition)

    def objects(self) -> tuple[ObjectInfo, ...]:
        """Every known object type, in data order (for seeding `GameObject` defaults, TD-24); aliases are left out."""
        return tuple(obj for key, obj in self._objects.items() if key == _key(obj.log_name))


_TOKEN_COUNT = re.compile(r"(.+?)-(\d+)")


_SUB_OBJECT = re.compile(r"_(?:HIT|x\d+)$")
"""The part of a log ammo name that marks a sub-object of the same weapon (`..._HIT`, bomblets `..._x8`)."""


def clean_ammo_name(raw: str) -> str:
    """Readable stand-in for an ammo `ammo.csv` doesn't know: 'BULLET_12-7_USA_API' -> '12.7 USA API' (the generic
    prefix dropped, the calibre's dash is a decimal point). Never empty: the raw name if nothing is left."""
    parts = raw.split("_")
    if parts and parts[0].upper() in ("BULLET", "SHELL", "BOMB", "RKT", "ROCKET"):
        parts = parts[1:]
    text = " ".join(parts)
    out: list[str] = []
    for index, char in enumerate(text):
        is_decimal = (
            char == "-" and 0 < index < len(text) - 1 and text[index - 1].isdigit() and text[index + 1].isdigit()
        )
        out.append("." if is_decimal else char)
    return "".join(out) or raw


def _split_count(token: str) -> tuple[str, int]:
    """`M64-2` -> (`M64`, 2); a token without a count (`Empty`, `PYLONS`) counts 1."""
    m = _TOKEN_COUNT.fullmatch(token)
    return (m.group(1), int(m.group(2))) if m else (token, 1)


def _rows(text: str, columns: list[str], source: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != columns:
        raise ValueError(f"{source}: expected columns {columns}, got {reader.fieldnames}")
    return list(reader)


def parse_objects(text: str, source: str = "objects.csv") -> list[ObjectInfo]:
    result: list[ObjectInfo] = []
    columns = ["log_name", "display_name", "cls", "is_playable", "propulsion", "ground_category"]
    for n, row in enumerate(_rows(text, columns, source), start=2):
        cls, playable, propulsion = row["cls"], row["is_playable"], row["propulsion"]
        category = row["ground_category"]
        if not row["log_name"] or not row["display_name"]:
            raise ValueError(f"{source}:{n}: empty log_name or display_name")
        if not is_object_class(cls):
            raise ValueError(f"{source}:{n}: unknown class {cls!r}")
        if playable not in ("true", "false"):
            raise ValueError(f"{source}:{n}: is_playable must be true or false, got {playable!r}")
        if propulsion and not is_propulsion(propulsion):
            raise ValueError(f"{source}:{n}: propulsion must be prop, jet or empty, got {propulsion!r}")
        if category and not is_ground_category(category):
            raise ValueError(f"{source}:{n}: unknown ground category {category!r}")
        if bool(category) != (cls in GROUND_CLASSES):
            raise ValueError(
                f"{source}:{n}: ground_category is for ground classes only and required there: {category!r}"
            )
        result.append(
            ObjectInfo(
                row["log_name"],
                row["display_name"],
                cls,
                playable == "true",
                is_known=True,
                propulsion=propulsion if is_propulsion(propulsion) else None,
                ground_category=category if is_ground_category(category) else None,
            )
        )
    return result


def parse_object_names(text: str, source: str = "object_names.csv") -> dict[str, str]:
    """`log_name -> translated name` from an `object_names_<language>.csv`; the `english` column is a reviewer aid."""
    names: dict[str, str] = {}
    seen: set[str] = set()
    for n, row in enumerate(_rows(text, ["log_name", "english", "display_name", "review"], source), start=2):
        log_name, name = row["log_name"], row["display_name"]
        if row["review"] not in ("", "llm-draft"):
            raise ValueError(f"{source}:{n}: review must be empty or llm-draft, got {row['review']!r}")
        if not log_name or not name.strip():
            raise ValueError(f"{source}:{n}: empty log_name or display_name")
        if _key(log_name) in seen:
            raise ValueError(f"{source}:{n}: duplicate object type {log_name!r}")
        seen.add(_key(log_name))
        names[log_name] = name
    return names


def parse_weapon_mod_names(text: str, source: str = "weapon_mod_names.csv") -> dict[str, str]:
    """`English mod name -> translated name` from a `weapon_mod_names_<language>.csv`."""
    names: dict[str, str] = {}
    for n, row in enumerate(_rows(text, ["english", "display_name", "review"], source), start=2):
        english, name = row["english"], row["display_name"]
        if row["review"] not in ("", "llm-draft"):
            raise ValueError(f"{source}:{n}: review must be empty or llm-draft, got {row['review']!r}")
        if not english or not name.strip():
            raise ValueError(f"{source}:{n}: empty english or display_name")
        if english in names:
            raise ValueError(f"{source}:{n}: duplicate weapon mod name {english!r}")
        names[english] = name
    return names


def parse_payloads(text: str, source: str = "payloads.csv") -> list[PayloadInfo]:
    columns = ["vehicle", "payload_id", "editor_name", "readable_name"]
    return [
        PayloadInfo(row["vehicle"], int(row["payload_id"]), row["editor_name"], row["readable_name"])
        for row in _rows(text, columns, source)
    ]


def parse_weapon_mods(text: str, source: str = "weapon_mods.csv") -> list[WeaponModInfo]:
    result: list[WeaponModInfo] = []
    for n, row in enumerate(_rows(text, ["vehicle", "mod_id", "name", "significant"], source), start=2):
        mod_id = int(row["mod_id"])
        if mod_id < 1 or not row["name"].strip():
            raise ValueError(f"{source}:{n}: mod_id must be 1 or more and the name filled")
        if row["significant"] not in ("true", "false"):
            raise ValueError(f"{source}:{n}: significant must be true or false, got {row['significant']!r}")
        result.append(WeaponModInfo(row["vehicle"], mod_id, row["name"], row["significant"] == "true"))
    return result


def parse_ordnance(text: str, source: str = "ordnance.csv") -> list[OrdnanceRow]:
    columns = ["key", "kind", "display_name", "payload_tokens", "hit_ammo"]
    result: list[OrdnanceRow] = []
    for n, row in enumerate(_rows(text, columns, source), start=2):
        kind = row["kind"]
        if not row["key"] or not row["display_name"]:
            raise ValueError(f"{source}:{n}: empty key or display_name")
        if not is_ordnance_kind(kind):
            raise ValueError(f"{source}:{n}: unknown kind {kind!r}")
        tokens = tuple(t for t in row["payload_tokens"].split(";") if t)
        ammo = tuple(a for a in row["hit_ammo"].split(";") if a)
        result.append(OrdnanceRow(OrdnanceInfo(row["key"], kind, row["display_name"]), tokens, ammo))
    return result


def parse_ammo(text: str, source: str = "ammo.csv") -> list[AmmoInfo]:
    columns = ["log_name", "name", "calibre", "round_type", "designation"]
    result: list[AmmoInfo] = []
    for n, row in enumerate(_rows(text, columns, source), start=2):
        if not row["log_name"] or not row["name"].strip():
            raise ValueError(f"{source}:{n}: empty log_name or name")
        result.append(AmmoInfo(row["log_name"], row["name"], row["calibre"], row["round_type"], row["designation"]))
    return result


def parse_object_aliases(text: str, source: str = "object_aliases.csv") -> dict[str, str]:
    return {row["log_name"]: row["same_as"] for row in _rows(text, ["log_name", "same_as"], source)}


def parse_payload_aliases(text: str, source: str = "payload_aliases.csv") -> dict[str, str]:
    return {row["log_name"]: row["vehicle"] for row in _rows(text, ["log_name", "vehicle"], source)}


NAMES_PREFIX = "object_names_"
MOD_NAMES_PREFIX = "weapon_mod_names_"


def load_default_catalog() -> Catalog:
    """Load the data shipped in `core/catalog/data/`."""
    data = files("il2ks.core.catalog").joinpath("data")

    def read(name: str) -> str:
        return data.joinpath(name).read_text(encoding="utf-8")

    entries = sorted(data.iterdir(), key=lambda e: e.name)
    names = {
        entry.name.removeprefix(NAMES_PREFIX).removesuffix(".csv"): parse_object_names(read(entry.name), entry.name)
        for entry in entries
        if entry.name.startswith(NAMES_PREFIX) and entry.name.endswith(".csv")
    }
    mod_names = {
        entry.name.removeprefix(MOD_NAMES_PREFIX).removesuffix(".csv"): parse_weapon_mod_names(
            read(entry.name), entry.name
        )
        for entry in entries
        if entry.name.startswith(MOD_NAMES_PREFIX) and entry.name.endswith(".csv")
    }
    return Catalog(
        objects=parse_objects(read("objects.csv")),
        payloads=parse_payloads(read("payloads.csv")),
        payload_aliases=parse_payload_aliases(read("payload_aliases.csv")),
        weapon_mods=parse_weapon_mods(read("weapon_mods.csv")),
        object_names=names,
        ordnance=parse_ordnance(read("ordnance.csv")),
        ammo=parse_ammo(read("ammo.csv")),
        object_aliases=parse_object_aliases(read("object_aliases.csv")),
        weapon_mod_names=mod_names,
    )
