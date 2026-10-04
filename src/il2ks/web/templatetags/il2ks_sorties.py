"""Filters of the sortie pages: `{% load il2ks_sorties %}`.

Filters (formatting only, TD-22): mission_name.
"""

from datetime import datetime

from django import template
from django.utils.safestring import SafeString

from il2ks.web import display

register = template.Library()


@register.filter
def mission_name(mission_file: object) -> str:
    """{{ mission.mission_file|mission_name }} -> 'The Sinuiju Bridges 1951' (the scenario's file name, readable)."""
    return display.mission_name(mission_file)


@register.filter
def utc_short(value: datetime | None) -> SafeString | str:
    """Deprecated alias of `local_short` (il2ks tag library); keeps old `custom/` overrides rendering (TD-25)."""
    return display.time_element(value, "datetime")
