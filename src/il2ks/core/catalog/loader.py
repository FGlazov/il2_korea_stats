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
- `payload_aliases.csv`: `log_name, vehicle`, log aircraft name -> `payloads.csv` vehicle key (`F-86A-5` -> `f-86a`).
- `ordnance.csv`: `key, kind, display_name, payload_tokens, hit_ammo`, what a payload carries and which logged ammo
  names are that ordnance (FR-WEB-18). `payload_tokens` are the `payloads.csv` editor-name tokens (`M64`, `HVAR`),
  `hit_ammo` the named hit-line ammo (prefix match, so `_HIT` and `_x8` variants belong to the same ordnance).
"""

import csv
import io
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.resources import files
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
        object_names: Mapping[str, Mapping[str, str]] | None = None,
        ordnance: Iterable[OrdnanceRow] = (),
    ) -> None:
        """`object_names`: language code (`de`, `pt-br`) -> {log name -> translated display name}."""
        self._names: dict[str, dict[str, str]] = {
            language.lower(): {_key(log_name): name for log_name, name in names.items()}
            for language, names in (object_names or {}).items()
        }
        self._objects: dict[str, ObjectInfo] = {}
        for obj in objects:
            key = _key(obj.log_name)
            if key in self._objects:
                raise ValueError(f"duplicate object type {obj.log_name!r} in the catalog")
            self._objects[key] = obj
        self._payloads: dict[tuple[str, int], PayloadInfo] = {}
        for p in payloads:
            pkey = (p.aircraft.casefold(), p.payload_id)
            if pkey in self._payloads:
                raise ValueError(f"duplicate payload {p.aircraft!r} #{p.payload_id} in the catalog")
            self._payloads[pkey] = p
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
        """Every known object type, in data order (for seeding `GameObject` defaults, TD-24)."""
        return tuple(self._objects.values())


_TOKEN_COUNT = re.compile(r"(.+?)-(\d+)")


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


def parse_payloads(text: str, source: str = "payloads.csv") -> list[PayloadInfo]:
    columns = ["vehicle", "payload_id", "editor_name", "readable_name"]
    return [
        PayloadInfo(row["vehicle"], int(row["payload_id"]), row["editor_name"], row["readable_name"])
        for row in _rows(text, columns, source)
    ]


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


def parse_payload_aliases(text: str, source: str = "payload_aliases.csv") -> dict[str, str]:
    return {row["log_name"]: row["vehicle"] for row in _rows(text, ["log_name", "vehicle"], source)}


NAMES_PREFIX = "object_names_"


def load_default_catalog() -> Catalog:
    """Load the data shipped in `core/catalog/data/`."""
    data = files("il2ks.core.catalog").joinpath("data")

    def read(name: str) -> str:
        return data.joinpath(name).read_text(encoding="utf-8")

    names = {
        entry.name.removeprefix(NAMES_PREFIX).removesuffix(".csv"): parse_object_names(read(entry.name), entry.name)
        for entry in sorted(data.iterdir(), key=lambda e: e.name)
        if entry.name.startswith(NAMES_PREFIX) and entry.name.endswith(".csv")
    }
    return Catalog(
        objects=parse_objects(read("objects.csv")),
        payloads=parse_payloads(read("payloads.csv")),
        payload_aliases=parse_payload_aliases(read("payload_aliases.csv")),
        object_names=names,
        ordnance=parse_ordnance(read("ordnance.csv")),
    )
