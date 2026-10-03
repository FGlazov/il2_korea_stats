"""Template context every page gets: branding, data freshness and version (TD-25, TD-28).

Cost: two plain primary-key reads, never a write. A missing `SiteSettings` row falls back to an unsaved default
instance, so the very first GET on a fresh database works without creating anything (unlike `db.site.get_site_settings`,
which is for the admin and ingest). The harness in `tests/simple_reads.py` counts these two in every page budget.
"""

from datetime import datetime

from django.conf import settings as django_settings
from django.http import HttpRequest

from il2ks import __version__
from il2ks.db.models import DataVersion, SiteSettings
from il2ks.web.display import accent_css

ALLOWED_LINK_PREFIXES = ("https://", "http://", "mailto:", "/")


def safe_links(raw: object) -> list[tuple[str, str]]:
    """(label, url) pairs from `SiteSettings.links`; drops malformed rows and any scheme but http(s), mailto, local."""
    if not isinstance(raw, list):
        return []
    links: list[tuple[str, str]] = []
    for item in raw:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, dict):
            continue
        label, url = item.get("label"), item.get("url")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        if isinstance(label, str) and isinstance(url, str) and url.strip().lower().startswith(ALLOWED_LINK_PREFIXES):
            links.append((label.strip() or url.strip(), url.strip()))
    return links


def site(request: HttpRequest) -> dict[str, object]:
    """Adds `site`, `logo_url`, `site_links`, `accent_css`, `data_updated`, `il2ks_version` to every template context.

    - `site`: the `SiteSettings` row (unsaved defaults when none exists yet): site_title, server_name, description,
      logo, redfor_name, blufor_name, ...
    - `logo_url`: where the logo is served (MEDIA_URL + SiteSettings.logo), '' when there is none.
    - `site_links`: `site.links` as a list of (label, url), with unsafe URL schemes dropped.
    - `accent_css`: a ready-made CSS rule for the accent colour, '' unless `accent_color` is a valid '#RRGGBB'.
    - `data_updated`: aware datetime of the last data change, or None before the first one.
    - `il2ks_version`: the installed il2ks version.
    """
    row = SiteSettings.objects.filter(pk=1).first() or SiteSettings()
    version_row = DataVersion.objects.filter(pk=1).only("updated_at").first()
    data_updated: datetime | None = version_row.updated_at if version_row is not None else None
    logo_url = f"{django_settings.MEDIA_URL or '/media/'}{row.logo}" if row.logo else ""
    return {
        "site": row,
        "logo_url": logo_url,
        "site_links": safe_links(row.links),
        "accent_css": accent_css(row.accent_color),
        "data_updated": data_updated,
        "il2ks_version": __version__,
    }
