"""The invariant of `PlayerSortie.tour` (design_doc/06 "PlayerSortie", doc 14 "The sortie's tour"): a sortie's tour is
always its mission's tour. Called after every path that writes `Mission.tour`."""

from django.db.models import F

from il2ks.db.models import PlayerSortie


def assert_sortie_tours_consistent() -> None:
    """No sortie has another tour than its mission (a missing tour on both sides counts as equal)."""
    wrong = PlayerSortie.objects.exclude(tour_id=F("mission__tour_id"))  # NULL on either side is not "equal" in SQL
    stale = [
        (pk, tour, mission_tour)
        for pk, tour, mission_tour in wrong.values_list("pk", "tour_id", "mission__tour_id")
        if tour != mission_tour
    ]
    assert not stale, f"sorties whose tour differs from their mission's (pk, sortie tour, mission tour): {stale[:10]}"
