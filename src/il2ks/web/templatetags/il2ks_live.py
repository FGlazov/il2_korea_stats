"""Template tags of the "online now" component: `{% load il2ks_live %}`.

`{% online_now %}` renders `il2ks/components/online_now.html` with the current snapshot (one read of its own, so the
page needs nothing in its context). `{% live_state_badge row.state %}` is the pill for a player's state.
"""

from collections.abc import Mapping

from django import template
from django.template import Context
from django.utils.translation import gettext_lazy

from il2ks.queries import live
from il2ks.web import display, icons

register = template.Library()

COMPONENTS = "il2ks/components/"

LIVE_STATES: Mapping[str, display.BadgeSpec] = {
    "in_flight": (gettext_lazy("In flight"), "teal", "outcome/in-flight"),
    "on_ground": (gettext_lazy("On the ground"), "green", "outcome/landed"),
    "spawned": (gettext_lazy("Spawned"), "grey", ""),
    "connected": (gettext_lazy("In the lobby"), "grey", ""),
}


@register.inclusion_tag(COMPONENTS + "online_now.html", takes_context=True)
def online_now(context: Context) -> dict[str, object]:
    """{% online_now %}: the polling "Online now" section (counts, player list). Falls back to a static snapshot
    without JavaScript; with htmx it refreshes itself from `{% url 'web:live' %}`."""
    return {"live": live.current(), "site": context.get("site"), "request": context.get("request")}


@register.inclusion_tag(COMPONENTS + "badge.html")
def live_state_badge(value: object) -> dict[str, object]:
    """{% live_state_badge row.state %}: in_flight, on_ground, spawned, connected."""
    text, tone, icon = display.badge_spec(LIVE_STATES, value)
    return {"text": text, "tone": tone, "icon_html": icons.icon_markup(icon) if icon else ""}
