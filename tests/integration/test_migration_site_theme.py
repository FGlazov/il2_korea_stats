"""Migration 0024: the accent colour moves into the theme, the old links become ordered `NavLink` rows (TD-25)."""

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("il2ks_db", "0026_pool_friendly_fire_incidents")
AFTER = ("il2ks_db", "0027_site_theme_nav_links")


def migrated(
    accent: str, links: object
) -> tuple[dict[str, dict[str, str]], list[dict[str, str]], list[tuple[str, str, int]]]:
    try:
        executor = MigrationExecutor(connection)
        executor.migrate([BEFORE])
        old = executor.loader.project_state([BEFORE]).apps
        old.get_model("il2ks_db", "SiteSettings").objects.create(pk=1, accent_color=accent, links=links)
        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])
        new = executor.loader.project_state([AFTER]).apps
        site: Any = new.get_model("il2ks_db", "SiteSettings").objects.get(pk=1)
        rows = list(
            new.get_model("il2ks_db", "NavLink").objects.order_by("position").values_list("label", "url", "position")
        )
        return site.theme, site.links, rows
    finally:
        final = MigrationExecutor(connection)
        final.migrate(final.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_accent_and_links_are_carried_over() -> None:
    links = [
        {"label": "Discord", "url": "https://discord.gg/x"},
        {"label": "Evil", "url": "javascript:alert(1)"},  # the old form refused these; a hand-edited row is dropped
        {"label": "Forum", "url": "http://forum.example/"},
        "junk",
    ]

    theme, published, rows = migrated("#336699", links)

    assert theme == {"light": {"accent": "#336699"}, "dark": {"accent": "#336699"}}
    assert published == [
        {"label": "Discord", "url": "https://discord.gg/x", "icon": ""},
        {"label": "Forum", "url": "http://forum.example/", "icon": ""},
    ]
    assert rows == [("Discord", "https://discord.gg/x", 1), ("Forum", "http://forum.example/", 2)]


@pytest.mark.django_db(transaction=True)
def test_no_accent_means_the_default_theme() -> None:
    theme, published, rows = migrated("", [])

    assert (theme, published, rows) == ({}, [], [])
