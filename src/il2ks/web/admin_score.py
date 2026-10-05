"""The admin's Scoring page: the optional score for flight time (maintainer request 2026-10-05; doc 13 "Score", doc 16).

Form fields: `enabled` (checkbox) and `per_hour` (points per hour of flight). Nothing is saved when anything is
invalid; `parse_form` returns the problems so the page can show the form again as typed."""

import math
from collections.abc import Mapping

from django.utils.translation import gettext as _

from il2ks.core.ratings.score import MAX_FLIGHT_POINTS_PER_HOUR, FlightScore


def parse_form(post: Mapping[str, str]) -> tuple[FlightScore, list[str]]:
    """The settings the posted form describes and the problems that stop it from being saved (the rate falls back to
    the default in the returned settings when it is invalid)."""
    enabled = bool(post.get("enabled"))
    raw = post.get("per_hour", "").strip().replace(",", ".")
    try:
        rate = float(raw)
    except ValueError:
        rate = math.nan
    if not math.isfinite(rate) or rate < 0 or rate > MAX_FLIGHT_POINTS_PER_HOUR:
        message = _("The points per hour must be a number from 0 to %(max)s.") % {
            "max": f"{MAX_FLIGHT_POINTS_PER_HOUR:g}"
        }
        return FlightScore(enabled), [message]
    return FlightScore(enabled, rate), []
