"""Filters of the sortie pages: `{% load il2ks_sorties %}`.

Filters (formatting only, TD-22): mission_name.
"""

from django import template

from il2ks.web import display

register = template.Library()


@register.filter
def mission_name(mission_file: object) -> str:
    """{{ mission.mission_file|mission_name }} -> 'The Sinuiju Bridges 1951' (the scenario's file name, readable)."""
    return display.mission_name(mission_file)
