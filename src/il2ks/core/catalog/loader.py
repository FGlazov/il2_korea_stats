"""Object, payload and coalition reference data (TD-24, FR-ING-7).

CONTRACT STUB: signatures fixed, bodies are iteration 1 work.

Lookups never raise: an object type that isn't in the shipped data comes back with `is_known=False` and class `unknown`
(TD-20: "the replay never indexes a catalog dict directly").
"""

from dataclasses import dataclass
from typing import Literal

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

AIR_CLASSES: frozenset[ObjectClass] = frozenset({"fighter", "attacker", "bomber", "transport", "gunner"})


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


class Catalog:
    """Read-only reference data. Build it once per process and pass it in (TD-11: no import-time globals)."""

    def lookup(self, object_type: str) -> ObjectInfo:
        """Case-insensitive, alias-aware. Never raises."""
        raise NotImplementedError

    def payload(self, aircraft_type: str, payload_id: int) -> PayloadInfo | None:
        """Resolve AType 10 `PAYLOAD` through the alias list (log `F-86A-5` -> CSV `f-86a`, doc 12)."""
        raise NotImplementedError

    def coalition_name(self, coalition: int) -> str:
        """1 -> "REDFOR", 2 -> "BLUFOR", others -> "Neutral" (doc 06)."""
        raise NotImplementedError


def load_default_catalog() -> Catalog:
    """Load the data shipped in `core/catalog/data/`."""
    raise NotImplementedError
