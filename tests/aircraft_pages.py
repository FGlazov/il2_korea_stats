"""Helpers for tests of the aircraft detail page."""

from django.test import Client

from il2ks.queries.aircraft import Loadout


def all_loadouts(client: Client, url: str) -> list[Loadout]:
    """Every loadout row of a detail page URL. Under the role "all" the page shows one role's loadouts at a time (a
    tab, `?lrole=`), so both are read; with a role in the URL the table follows it and is read once."""
    separator = "&" if "?" in url else "?"
    bare = url.replace("lrole=", "")
    if "role=air_superiority" in bare or "role=attack" in bare:
        return list(client.get(url).context["loadouts"])
    rows: list[Loadout] = []
    for mode in ("air_superiority", "attack"):
        rows += list(client.get(f"{url}{separator}lrole={mode}").context["loadouts"])
    return rows
