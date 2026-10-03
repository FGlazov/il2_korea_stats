"""Filters of the sortie pages: `{% load il2ks_sorties %}`.

Filters (formatting only, TD-22): mission_name, utc_short.
"""

from datetime import datetime

from django import template

from il2ks.web import display

register = template.Library()


@register.filter
def mission_name(mission_file: object) -> str:
    """{{ mission.mission_file|mission_name }} -> 'The Sinuiju Bridges 1951' (the scenario's file name, readable)."""
    return display.mission_name(mission_file)


@register.filter
def utc_short(value: datetime | None) -> str:
    """{{ sortie.spawned_at|utc_short }} -> '2026-09-19 22:34' (UTC, for columns whose header says so)."""
    return display.utc(value).removesuffix(" UTC")
