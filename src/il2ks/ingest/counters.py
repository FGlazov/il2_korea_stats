"""The one definition of the counters shared by PlayerMission, Player and PlayerAircraft (TD-08, TD-16, doc 06).

Every counter is an ORM aggregate over `PlayerSortie` rows. PlayerMission (level 1) and PlayerAircraft (level 2) are
built by grouping counted sorties with these aggregates; Player totals are sums of PlayerMission rows. Saving a mission
and `rebuild-aggregates` both recompute through this registry (`ingest.aggregates`), so the three tables can't drift.
`tests/integration/test_aggregates.py` checks that the registry covers exactly the fields of `db.models.Counters`.
"""

from collections.abc import Mapping
from types import MappingProxyType

from django.db.models import Aggregate, Count, F, Q, QuerySet, Sum

from il2ks.core.replay.result import LOSS_CLASSES
from il2ks.db.models import CombatRole, PilotFate, PlayerSortie, Role

COUNTED_ROLES: tuple[str, ...] = (Role.PILOT,)
"""Only pilot sorties feed the counters. Gunner sorties are stored on level 1; gunner stats come later (FR-WEB-14)."""

SORTIE_COUNTERS: Mapping[str, Aggregate] = MappingProxyType(
    {
        "sorties": Count("pk"),
        # Before the counters of the same name as the sortie fields they read: an annotation shadows the field.
        "flight_time_air_s": Sum("flight_time_s", filter=Q(combat_role=CombatRole.AIR_SUPERIORITY)),
        "flight_time_s": Sum("flight_time_s"),
        "kills_air": Sum("kills_air"),
        "kills_ground": Sum("kills_ground"),
        "assists": Sum("assists"),
        "assists_air": Sum("assists_air"),
        "assists_ground": Sum("assists_ground"),
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
        "kills_tank_attack": Sum("kills_ground_tank", filter=Q(combat_role=CombatRole.ATTACK)),
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
        "air_superiority_sorties": Count("pk", filter=Q(combat_role=CombatRole.AIR_SUPERIORITY)),
        "kills_intercept": Sum("kills_air_intercept", filter=Q(combat_role=CombatRole.AIR_SUPERIORITY)),
        # Accuracy (doc 13): rounds and hits of the same sorties only (those with a known number of rounds fired).
        # Before "gun_hits_*": the annotation of that name would shadow the sortie field these read.
        "accuracy_rounds": Sum("rounds_fired", filter=Q(rounds_fired__isnull=False)),
        "accuracy_hits": Sum(F("gun_hits_air") + F("gun_hits_ground"), filter=Q(rounds_fired__isnull=False)),
        "accuracy_air_rounds": Sum("rounds_fired", filter=Q(combat_role=CombatRole.AIR_SUPERIORITY)),
        "accuracy_air_hits": Sum(
            "gun_hits_air", filter=Q(combat_role=CombatRole.AIR_SUPERIORITY, rounds_fired__isnull=False)
        ),
        "accuracy_ground_rounds": Sum("rounds_fired", filter=Q(combat_role=CombatRole.ATTACK)),
        "accuracy_ground_hits": Sum(
            "gun_hits_ground", filter=Q(combat_role=CombatRole.ATTACK, rounds_fired__isnull=False)
        ),
        "gun_hits_air": Sum("gun_hits_air"),
        "gun_hits_ground": Sum("gun_hits_ground"),
    }
)

COUNTER_FIELDS: tuple[str, ...] = tuple(SORTIE_COUNTERS)
FLOAT_COUNTERS: frozenset[str] = frozenset(
    {
        "flight_time_s",
        "friendly_damage",
        "time_on_target_s",
        "score_air",
        "score_ground",
        "score_ground_attack",
        "flight_time_air_s",
    }
)

type CounterValues = dict[str, int | float]


def counted_sorties() -> QuerySet[PlayerSortie]:
    """The sorties that feed the counters."""
    return PlayerSortie.objects.filter(role__in=COUNTED_ROLES)


def clean_counters(row: Mapping[str, object]) -> CounterValues:
    """Pick the counter values out of an aggregate row, turning NULL (empty Sum) into 0. Called once per counter row of
    a refresh (tens of thousands), so the per-field decisions (`_INT_NAMES`, `_FLOAT_NAMES`) are made once, not per
    call. A value is an int, a float or None, as the database returns a `Sum` or a `Count`."""
    values: CounterValues = {}
    for name in _INT_NAMES:
        value = row.get(name)
        values[name] = int(value) if value else 0  # pyright: ignore[reportArgumentType]
    for name in _FLOAT_NAMES:
        value = row.get(name)
        values[name] = _clean_float(name, value) if value else 0.0  # pyright: ignore[reportArgumentType]
    return values


SCORE_COUNTERS: frozenset[str] = frozenset({"score_air", "score_ground", "score_ground_attack"})
SCORE_DECIMALS = 4


_FLOAT_NAMES = tuple(name for name in COUNTER_FIELDS if name in FLOAT_COUNTERS)
_INT_NAMES = tuple(name for name in COUNTER_FIELDS if name not in FLOAT_COUNTERS)


def _clean_float(name: str, value: float) -> float:
    """Scores are rounded so that float summation order can't make a rebuild differ from an incremental update."""
    return round(float(value), SCORE_DECIMALS) if name in SCORE_COUNTERS else float(value)
