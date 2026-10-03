"""PvE breakdown for display (FR-WEB-21, doc 13 "PvE breakdown"): kills and losses by counterpart class.

Shared by the player profile and the sortie page. Pure reshaping of counters that were aggregated at ingest (TD-22): the
profile reads them from its counters row (`Player`, or a tour row), the sortie page from the `PlayerSortie`.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy

from il2ks.db.models import Counters, LossClass
from il2ks.web import display

# Who is behind a loss, in the order the table lists them. `environment` is "no attacker": the pilot's own doing or
# an abandoned aircraft nobody hit (doc 13), so it is labelled with what players recognise.
LOSS_LABELS: Mapping[str, Promise] = {
    LossClass.ENVIRONMENT.value: gettext_lazy("No attacker (crash, terrain, accident)"),
    LossClass.PLAYER.value: gettext_lazy("Player"),
    LossClass.AAA.value: gettext_lazy("Anti-aircraft"),
    LossClass.GROUND.value: gettext_lazy("Ground vehicle or object"),
    LossClass.AI_AIRCRAFT.value: gettext_lazy("AI aircraft"),
    LossClass.AI_GUNNER.value: gettext_lazy("AI gunner"),
    LossClass.FRIENDLY.value: gettext_lazy("Friendly fire"),
    LossClass.UNKNOWN.value: gettext_lazy("Unknown"),
}


@dataclass(frozen=True, slots=True)
class LossRow:
    """Deaths and lost aircraft caused by one class of counterpart."""

    key: str
    label: str
    deaths: int
    share: str  # of all deaths, '87%'
    planes_lost: int


@dataclass(frozen=True, slots=True)
class KillClassRow:
    """Kills of one class of victim."""

    key: str
    label: str
    count: int
    share: str  # of all kills, '87%'


def loss_label(loss_class: str) -> str:
    """The label of a sortie's `loss_class` ('' when nothing was lost)."""
    label = LOSS_LABELS.get(loss_class)
    return str(label) if label is not None else ""


def loss_breakdown(counters: Counters) -> list[LossRow]:
    """Deaths and lost aircraft per class of anything with `deaths_by_<class>` and `planes_lost_by_<class>` attributes.
    Each column sums to `deaths` / `planes_lost` by construction (doc 13)."""
    return [
        LossRow(
            key,
            str(label),
            int(getattr(counters, f"deaths_by_{key}")),
            display.percent(getattr(counters, f"deaths_by_{key}"), counters.deaths),
            int(getattr(counters, f"planes_lost_by_{key}")),
        )
        for key, label in LOSS_LABELS.items()
    ]


def kill_breakdown(counters: Counters) -> list[KillClassRow]:
    """Kills by victim class: player aircraft, AI aircraft (their gunners included), anti-aircraft, other ground
    objects. They add up to `kills_air + kills_ground`; friendly kills are never counted (FR-ING-23)."""
    air_pvp, air_ai = counters.kills_air_pvp, counters.kills_air_ai
    ground, aaa = counters.kills_ground, counters.kills_ground_aaa
    counts = (
        ("player", gettext_lazy("Player aircraft"), air_pvp),
        ("ai_aircraft", gettext_lazy("AI aircraft"), air_ai),
        ("aaa", gettext_lazy("Anti-aircraft"), aaa),
        ("ground", gettext_lazy("Other ground objects"), max(ground - aaa, 0)),
    )
    total = sum(count for _key, _label, count in counts)
    return [KillClassRow(key, str(label), count, display.percent(count, total)) for key, label, count in counts]
