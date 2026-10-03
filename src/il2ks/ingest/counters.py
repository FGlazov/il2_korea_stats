"""The one definition of the counters shared by PlayerMission, Player and PlayerAircraft (TD-08, TD-16, doc 06).

Every counter is an ORM aggregate over `PlayerSortie` rows. PlayerMission (level 1) and PlayerAircraft (level 2) are
built by grouping counted sorties with these aggregates; Player totals are sums of PlayerMission rows. Saving a mission
and `rebuild-aggregates` both recompute through this registry (`ingest.aggregates`), so the three tables can't drift.
`tests/integration/test_aggregates.py` checks that the registry covers exactly the fields of `db.models.Counters`.
"""

from collections.abc import Mapping
from types import MappingProxyType

from django.db.models import Aggregate, Count, Q, QuerySet, Sum

from il2ks.db.models import PilotFate, PlayerSortie, Role

COUNTED_ROLES: tuple[str, ...] = (Role.PILOT,)
"""Only pilot sorties feed the counters. Gunner sorties are stored on level 1; gunner stats come later (FR-WEB-14)."""

SORTIE_COUNTERS: Mapping[str, Aggregate] = MappingProxyType(
    {
        "sorties": Count("pk"),
        "flight_time_s": Sum("flight_time_s"),
        "kills_air": Sum("kills_air"),
        "kills_ground": Sum("kills_ground"),
        "assists": Sum("assists"),
        "deaths": Count("pk", filter=Q(is_death=True)),
        "planes_lost": Count("pk", filter=Q(is_plane_lost=True)),
        "bailouts": Count("pk", filter=Q(pilot_fate=PilotFate.BAILED_OUT)),
        "suspected_early_bailouts": Count("pk", filter=Q(suspected_early_bailout=True)),
        "captures": Count("pk", filter=Q(is_captured=True)),
        "takeoffs": Sum("takeoffs"),
        "landings": Sum("landings"),
        "friendly_kills": Sum("friendly_kills"),
        "friendly_hits": Sum("friendly_hits"),
        "friendly_damage": Sum("friendly_damage"),
    }
)

COUNTER_FIELDS: tuple[str, ...] = tuple(SORTIE_COUNTERS)
FLOAT_COUNTERS: frozenset[str] = frozenset({"flight_time_s", "friendly_damage"})

type CounterValues = dict[str, int | float]


def counted_sorties() -> QuerySet[PlayerSortie]:
    """The sorties that feed the counters."""
    return PlayerSortie.objects.filter(role__in=COUNTED_ROLES)


def zero_counters() -> CounterValues:
    return {name: 0.0 if name in FLOAT_COUNTERS else 0 for name in COUNTER_FIELDS}


def clean_counters(row: Mapping[str, object]) -> CounterValues:
    """Pick the counter values out of an aggregate row, turning NULL (empty Sum) into 0."""
    values = zero_counters()
    for name in COUNTER_FIELDS:
        value = row.get(name)
        if isinstance(value, int | float):
            values[name] = float(value) if name in FLOAT_COUNTERS else int(value)
    return values
