"""Object, payload and coalition reference data (TD-24, FR-ING-7).

Lookups never raise: an object type that isn't in the shipped data comes back with `is_known=False` and class `unknown`
(TD-20: "the replay never indexes a catalog dict directly").

Shipped data (`core/catalog/data/`):
- `objects.csv`: `log_name, display_name, cls, is_playable`, one row per object type seen in the logs (doc 12).
- `payloads.csv`: `vehicle, payload_id, editor_name, readable_name` (doc 12 "Payloads").
- `payload_aliases.csv`: `log_name, vehicle`, log aircraft name -> `payloads.csv` vehicle key (`F-86A-5` -> `f-86a`).
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
    "unknown",
]
"""Same values as `il2ks.db.models.ObjectClass`."""

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
        "unknown",
    }
)
AIR_CLASSES: frozenset[ObjectClass] = frozenset({"fighter", "attacker", "bomber", "transport", "gunner"})

COALITION_NAMES: Mapping[int, str] = {1: "REDFOR", 2: "BLUFOR"}
"""Doc 06: every country of a coalition displays as the plain coalition name."""
NEUTRAL = "Neutral"

# Static block objects carry their block group and index: `GAZ_63[64606,0]`, `Industrial storage tank D[-1,-1]`.
_BLOCK_SUFFIX = re.compile(r"\[-?\d+,-?\d+\]$")
# Types that carry a per-instance number: `CParachute_2361344`.
_NUMBERED_TYPE = re.compile(r"(CParachute)_\d+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    log_name: str  # as written in the log (TYPE), original case
    display_name: str  # English default (TD-24)
    cls: ObjectClass
    is_playable: bool
    is_known: bool

    @property
    def is_air(self) -> bool:
        return self.cls in AIR_CLASSES


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


def is_object_class(value: str) -> TypeIs[ObjectClass]:
    return value in OBJECT_CLASSES


class Catalog:
    """Read-only reference data. Build it once per process and pass it in (TD-11: no import-time globals).

    `Catalog()` with no arguments is an empty catalog: every object is unknown and no payload resolves.
    """

    def __init__(
        self,
        objects: Iterable[ObjectInfo] = (),
        payloads: Iterable[PayloadInfo] = (),
        payload_aliases: Mapping[str, str] | None = None,
    ) -> None:
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

    def payload(self, aircraft_type: str, payload_id: int) -> PayloadInfo | None:
        """Resolve AType 10 `PAYLOAD` through the alias list (log `F-86A-5` -> CSV `f-86a`, doc 12).

        Aircraft without an alias are tried under their own (case-insensitive) name. None if nothing matches.
        """
        key = _key(aircraft_type)
        vehicle = self._aliases.get(key, key)
        return self._payloads.get((vehicle, payload_id))

    def coalition_name(self, coalition: int) -> str:
        """1 -> "REDFOR", 2 -> "BLUFOR", others -> "Neutral" (doc 06)."""
        return COALITION_NAMES.get(coalition, NEUTRAL)

    def objects(self) -> tuple[ObjectInfo, ...]:
        """Every known object type, in data order (for seeding `GameObject` defaults, TD-24)."""
        return tuple(self._objects.values())


def _rows(text: str, columns: list[str], source: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != columns:
        raise ValueError(f"{source}: expected columns {columns}, got {reader.fieldnames}")
    return list(reader)


def parse_objects(text: str, source: str = "objects.csv") -> list[ObjectInfo]:
    result: list[ObjectInfo] = []
    for n, row in enumerate(_rows(text, ["log_name", "display_name", "cls", "is_playable"], source), start=2):
        cls, playable = row["cls"], row["is_playable"]
        if not row["log_name"] or not row["display_name"]:
            raise ValueError(f"{source}:{n}: empty log_name or display_name")
        if not is_object_class(cls):
            raise ValueError(f"{source}:{n}: unknown class {cls!r}")
        if playable not in ("true", "false"):
            raise ValueError(f"{source}:{n}: is_playable must be true or false, got {playable!r}")
        result.append(ObjectInfo(row["log_name"], row["display_name"], cls, playable == "true", is_known=True))
    return result


def parse_payloads(text: str, source: str = "payloads.csv") -> list[PayloadInfo]:
    columns = ["vehicle", "payload_id", "editor_name", "readable_name"]
    return [
        PayloadInfo(row["vehicle"], int(row["payload_id"]), row["editor_name"], row["readable_name"])
        for row in _rows(text, columns, source)
    ]


def parse_payload_aliases(text: str, source: str = "payload_aliases.csv") -> dict[str, str]:
    return {row["log_name"]: row["vehicle"] for row in _rows(text, ["log_name", "vehicle"], source)}


def load_default_catalog() -> Catalog:
    """Load the data shipped in `core/catalog/data/`."""
    data = files("il2ks.core.catalog").joinpath("data")

    def read(name: str) -> str:
        return data.joinpath(name).read_text(encoding="utf-8")

    return Catalog(
        objects=parse_objects(read("objects.csv")),
        payloads=parse_payloads(read("payloads.csv")),
        payload_aliases=parse_payload_aliases(read("payload_aliases.csv")),
    )
