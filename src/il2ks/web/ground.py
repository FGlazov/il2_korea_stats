"""Ground-kill categories for display: labels and the breakdown rows (FR-WEB-4, OQ-33).

Shared by every page that shows a ground-kill breakdown (player profile, sortie page). The category keys are
`il2ks.core.catalog.loader.GROUND_CATEGORIES`; icons are `static/il2ks/img/ground/<name>.svg` (names in `GROUND_ICONS`).
"""

from collections.abc import Mapping
from dataclasses import dataclass

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy

from il2ks.core.catalog.loader import GROUND_CATEGORIES, GroundCategory

GROUND_CATEGORY_LABELS: Mapping[GroundCategory, Promise] = {
    "tank": gettext_lazy("Tanks"),
    "vehicle": gettext_lazy("Vehicles"),
    "artillery": gettext_lazy("Artillery"),
    "aaa": gettext_lazy("Anti-aircraft"),
    "ship": gettext_lazy("Ships"),
    "train": gettext_lazy("Trains"),
    "building": gettext_lazy("Buildings"),
    "parked_aircraft": gettext_lazy("Parked aircraft"),
    "other": gettext_lazy("Other objects"),
}

GROUND_ICONS: Mapping[str, str] = {
    "tank": "ground/tank",
    "vehicle": "ground/vehicle",
    "artillery": "ground/artillery",
    "aaa": "ground/aaa",
    "ship": "ground/ship",
    "train": "ground/train",
    "building": "ground/building",
    "parked_aircraft": "ground/parked-aircraft",
    "other": "ground/other-static",
}


@dataclass(frozen=True, slots=True)
class GroundRow:
    """One line of the breakdown: `icon` is the icon name ('ground/tank'), `label` the translated category."""

    key: GroundCategory
    icon: str
    label: str
    count: int


def ground_breakdown(counters: object) -> list[GroundRow]:
    """The nine categories of anything with `kills_ground_<category>` attributes (Player, PlayerSortie, ...), in the
    catalog's order. They add up to `kills_ground`; `kills_ground_static` is a separate count of the same kills."""
    return [
        GroundRow(
            key, GROUND_ICONS[key], str(GROUND_CATEGORY_LABELS[key]), int(getattr(counters, f"kills_ground_{key}"))
        )
        for key in GROUND_CATEGORIES
    ]
