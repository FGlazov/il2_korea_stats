"""Sortie scores on level 1 and their rebuild (FR-WEB-7, FR-ADM-7). The rules are in `il2ks.core.ratings.score`.

`air_points` and `ground_points` are stored on each pilot `PlayerSortie`, computed when the mission is saved from the
sortie's other stored columns (`apply_score`). Because the score reads only those columns, a changed `[score]` section
is applied without reprocessing a mission: `rebuild_sortie_scores` recomputes every pilot sortie, then
`aggregates.refresh_player_missions` rewrites the `PlayerMission` counters that sum them (level 2 follows through
`aggregates.recompute_players`). `aggregates.rebuild_aggregates` runs all of it.
"""

from collections.abc import Mapping

from il2ks.core.catalog.loader import GROUND_CATEGORIES
from il2ks.core.ratings.score import ScoreRules, SortieFacts, score_sortie
from il2ks.db.models import CombatRole, PlayerSortie, Role
from il2ks.ingest.counters import counted_sorties

FACT_COLUMNS: tuple[str, ...] = (
    "combat_role",
    "kills_air_pvp",
    "kills_air_ai",
    "assists",
    *(f"kills_ground_{c}" for c in GROUND_CATEGORIES),
    "is_death",
    "is_plane_lost",
    "is_captured",
    "suspected_early_bailout",
    "friendly_kills",
)
"""The `PlayerSortie` columns the score reads: nothing else may influence it (a test checks that)."""


def _count(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def facts_of(values: Mapping[str, object]) -> SortieFacts:
    """`SortieFacts` from `FACT_COLUMNS` values (a `.values()` row, or the same names read off a model)."""
    return SortieFacts(
        attack=values["combat_role"] == CombatRole.ATTACK,
        kills_air_pvp=_count(values["kills_air_pvp"]),
        kills_air_ai=_count(values["kills_air_ai"]),
        assists=_count(values["assists"]),
        kills_ground={c: _count(values[f"kills_ground_{c}"]) for c in GROUND_CATEGORIES},
        is_death=bool(values["is_death"]),
        is_plane_lost=bool(values["is_plane_lost"]),
        is_captured=bool(values["is_captured"]),
        suspected_early_bailout=bool(values["suspected_early_bailout"]),
        friendly_kills=_count(values["friendly_kills"]),
    )


def score_values(values: Mapping[str, object], rules: ScoreRules) -> tuple[float, float]:
    """`(air, ground)` score of a pilot sortie from its `FACT_COLUMNS` values."""
    result = score_sortie(facts_of(values), rules)
    return result.air, result.ground


def apply_score(sortie: PlayerSortie, rules: ScoreRules) -> None:
    """Set `air_points` / `ground_points` on a sortie row whose other fields are filled in. Gunners score nothing."""
    if sortie.role != Role.PILOT:
        sortie.air_points = sortie.ground_points = 0.0
        return
    sortie.air_points, sortie.ground_points = score_values({c: getattr(sortie, c) for c in FACT_COLUMNS}, rules)


def rebuild_sortie_scores(rules: ScoreRules) -> int:
    """Recompute the score of every pilot sortie under `rules`; returns how many changed. Reads only the score columns
    (not the JSON blobs) and writes only sorties whose score differs."""
    changed: list[PlayerSortie] = []
    rows = counted_sorties().values("pk", "air_points", "ground_points", *FACT_COLUMNS)
    for row in rows.iterator():
        air, ground = score_values(row, rules)
        if (air, ground) != (row["air_points"], row["ground_points"]):
            changed.append(PlayerSortie(pk=row["pk"], air_points=air, ground_points=ground))
    PlayerSortie.objects.bulk_update(changed, ["air_points", "ground_points"], batch_size=500)
    return len(changed)
