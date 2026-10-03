"""Influence areas (AType 13/14) and airfields (AType 9): "on enemy territory" and "back at base" checks (TD-21).

Korea area boundaries are 2D `(x, z)` polygons (doc 12).
"""

import math
from dataclasses import dataclass

from il2ks.core.logparse.events import Pos

AIRFIELD_RADIUS_M = 4000.0  # Derived from il2_stats (MIT), see NOTICE: Airfield.on_airfield


@dataclass(slots=True)
class Area:
    coalition: int | None
    enabled: bool
    boundary: tuple[tuple[float, float], ...] = ()


@dataclass(slots=True)
class Airfield:
    coalition: int | None
    pos: Pos


def point_in_polygon(x: float, z: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting on the (x, z) plane.

    # Derived from il2_stats (MIT), see NOTICE: mission_report/helpers.py point_in_polygon
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    p1x, p1z = polygon[0]
    for i in range(1, n + 1):
        p2x, p2z = polygon[i % n]
        if min(p1z, p2z) < z <= max(p1z, p2z) and x <= max(p1x, p2x):
            # p1z != p2z here: the strict bounds above can't hold for a horizontal edge
            crosses = p1x == p2x or x <= (z - p1z) * (p2x - p1x) / (p2z - p1z) + p1x
            if crosses:
                inside = not inside
        p1x, p1z = p2x, p2z
    return inside


def on_enemy_territory(pos: Pos, coalition: int, areas: list[Area]) -> bool:
    """Inside an enabled area of another (non-neutral) coalition.

    # Derived from il2_stats (MIT), see NOTICE: Object.is_on_enemy_territory
    """
    for area in areas:
        if not area.enabled or area.coalition in (None, 0, coalition):
            continue
        if point_in_polygon(pos.x, pos.z, area.boundary):
            return True
    return False


def at_friendly_airfield(pos: Pos, coalition: int, airfields: list[Airfield]) -> bool:
    """Within 4 km (horizontal) of an airfield of the same coalition. No airfields logged = can't tell = True."""
    friendly = [a for a in airfields if a.coalition == coalition]
    if not airfields:
        return True
    return any(math.hypot(a.pos.x - pos.x, a.pos.z - pos.z) <= AIRFIELD_RADIUS_M for a in friendly)
