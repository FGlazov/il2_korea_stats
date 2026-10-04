"""The one definition of the counters shared by PlayerMission, Player and PlayerAircraft (TD-08, TD-16, doc 06).

Every counter is an ORM aggregate over `PlayerSortie` rows. PlayerMission (level 1) and PlayerAircraft (level 2) are
built by grouping counted sorties with these aggregates; Player totals are sums of PlayerMission rows. Saving a mission
and `rebuild-aggregates` both recompute through this registry (`ingest.aggregates`), so the three tables can't drift.
`tests/integration/test_aggregates.py` checks that the registry covers exactly the fields of `db.models.Counters`.
"""

from collections.abc import Mapping
from types import MappingProxyType

from django.db.models import Aggregate, Count, Q, QuerySet, Sum

from il2ks.core.replay.result import LOSS_CLASSES
from il2ks.db.models import CombatRole, PilotFate, PlayerSortie, Role

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
        "taxi_accidents": Count("pk", filter=Q(taxi_accident=True)),
        "strafed_on_ground": Count("pk", filter=Q(strafed_on_ground=True)),
        "attack_sorties": Count("pk", filter=Q(combat_role=CombatRole.ATTACK)),
        "time_on_target_s": Sum("time_on_target_s"),
        "kills_ground_tank": Sum("kills_ground_tank"),
        "kills_ground_vehicle": Sum("kills_ground_vehicle"),
        "kills_ground_artillery": Sum("kills_ground_artillery"),
        "kills_ground_aaa": Sum("kills_ground_aaa"),
        "kills_ground_ship": Sum("kills_ground_ship"),
        "kills_ground_train": Sum("kills_ground_train"),
        "kills_ground_building": Sum("kills_ground_building"),
        "kills_ground_parked_aircraft": Sum("kills_ground_parked_aircraft"),
        "kills_ground_other": Sum("kills_ground_other"),
        "kills_ground_static": Sum("kills_ground_static"),
        "kills_air_pvp": Sum("kills_air_pvp"),
        "kills_air_ai": Sum("kills_air_ai"),
        **{f"deaths_by_{c}": Count("pk", filter=Q(is_death=True, loss_class=c)) for c in LOSS_CLASSES},
        **{f"planes_lost_by_{c}": Count("pk", filter=Q(is_plane_lost=True, loss_class=c)) for c in LOSS_CLASSES},
        "score_air": Sum("air_points"),
        "score_ground": Sum("ground_points"),
        "score_ground_attack": Sum("ground_points", filter=Q(combat_role=CombatRole.ATTACK)),
    }
)

COUNTER_FIELDS: tuple[str, ...] = tuple(SORTIE_COUNTERS)
FLOAT_COUNTERS: frozenset[str] = frozenset(
    {"flight_time_s", "friendly_damage", "time_on_target_s", "score_air", "score_ground", "score_ground_attack"}
)

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
            values[name] = _clean_float(name, value) if name in FLOAT_COUNTERS else int(value)
    return values


SCORE_COUNTERS: frozenset[str] = frozenset({"score_air", "score_ground", "score_ground_attack"})
SCORE_DECIMALS = 4


def _clean_float(name: str, value: float) -> float:
    """Scores are rounded so that float summation order can't make a rebuild differ from an incremental update."""
    return round(float(value), SCORE_DECIMALS) if name in SCORE_COUNTERS else float(value)
