"""Level-2 aggregates: Player totals, PlayerName, PlayerAircraft (TD-08). All aggregation happens in `ingest`.

CONTRACT STUB: the signatures are fixed, the bodies are iteration 1 work.
"""

from il2ks.db.models import Mission


def add_mission(mission: Mission) -> None:
    """Add one mission's level-1 rows (PlayerMission / PlayerSortie) to the level-2 totals."""
    raise NotImplementedError


def subtract_mission(mission: Mission) -> None:
    """Remove one mission's level-1 contribution from the level-2 totals (before a re-ingest)."""
    raise NotImplementedError


def rebuild_aggregates() -> None:
    """Recompute every level-2 table from level 1 (`il2ks rebuild-aggregates`). Must equal the incremental result."""
    raise NotImplementedError
