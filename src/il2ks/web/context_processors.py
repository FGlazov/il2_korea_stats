"""Template context every page gets: branding, data freshness and version (TD-25, TD-28).

Cost: two plain primary-key reads (the data version is shared with the caching middleware), never a write. A missing
`SiteSettings` row falls back to an unsaved default instance, so the very first GET on a fresh database works without
creating anything (unlike `db.site.get_site_settings`, which is for the admin and ingest). The harness in
`tests/simple_reads.py` counts these two in every page budget. The navigation links and the theme come from the settings
row itself (`links`, `theme`), so they add no query.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.utils import timezone
from django.utils.safestring import SafeString

from il2ks import __version__
from il2ks.db.models import NavIcon, NoticeLevel, SiteSettings
from il2ks.db.validators import validate_http_url
from il2ks.web.branding_images import background_css, has_background, icon_urls
from il2ks.web.caching import request_data_version
from il2ks.web.feature_image import HomeFeatureView, home_feature_view
from il2ks.web.theme import theme_css


@dataclass(frozen=True, slots=True)
class NavItem:
    """One custom navigation link as the templates see it. `icon` is an icon name for `{% icon %}` ('' = none)."""

    label: str
    url: str
    icon: str


def nav_items(raw: object) -> list[NavItem]:
    """The custom links from `SiteSettings.links`, in stored order. Malformed rows and any URL that is not a full
    http(s) address are dropped (the stored data was validated on save; this guards against a hand-edited row)."""
    if not isinstance(raw, list):
        return []
    items: list[NavItem] = []
    for entry in raw:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(entry, dict):
            continue
        fields: dict[str, object] = entry  # pyright: ignore[reportUnknownVariableType]
        label, url, icon = fields.get("label"), fields.get("url"), fields.get("icon", "")
        if not isinstance(label, str) or not isinstance(url, str) or not isinstance(icon, str):
            continue
        try:
            validate_http_url(url.strip())
        except ValidationError:
            continue
        known = icon in NavIcon.values and icon != ""
        items.append(NavItem(label.strip() or url.strip(), url.strip(), f"nav/{icon}" if known else ""))
    return items


@dataclass(frozen=True, slots=True)
class SiteNotice:
    """The site-wide notice banner as the templates see it: `level` is the notice component's kind, `until` the ISO
    expiry for `localtime.js` ('' = none; the cached page cannot hide itself when the moment passes)."""

    text: str
    level: str
    until: str


def site_notice(row: SiteSettings, now: datetime) -> SiteNotice | None:
    """The banner to show, or None: no text (whitespace only counts as none) or the expiry already past."""
    text = row.notice_text.strip()
    if not text or (row.notice_until is not None and row.notice_until <= now):
        return None
    level = row.notice_level if row.notice_level in NoticeLevel.values else NoticeLevel.INFO.value
    return SiteNotice(text, level, row.notice_until.isoformat() if row.notice_until is not None else "")


_SITE_ATTR = "_il2ks_site_row"


def site_row(request: HttpRequest) -> SiteSettings:
    """The `SiteSettings` row (unsaved defaults when none exists), read once per request: the `site` context processor
    and a view that needs a setting (the achievements' switches and words) share it, so the setting costs no query."""
    row = getattr(request, _SITE_ATTR, None)
    if row is None:
        row = SiteSettings.objects.filter(pk=1).first() or SiteSettings()
        setattr(request, _SITE_ATTR, row)
    return cast(SiteSettings, row)


def site(request: HttpRequest) -> dict[str, object]:
    """Adds `site`, `logo_url`, `nav_links`, `site_links`, `theme_css`, `data_updated`, `il2ks_version` to the context.

    - `site`: the `SiteSettings` row (unsaved defaults when none exists yet): site_title, server_name, description,
      logo, redfor_name, blufor_name, ...
    - `site_notice`: the notice banner (`SiteNotice(text, level, until)`) or None (no text, or expired).
    - `logo_url`: where the logo is served (MEDIA_URL + SiteSettings.logo), '' when there is none.
    - `nav_links`: the admin's extra navigation links, as `NavItem(label, url, icon)` in their order.
    - `site_links`: the same links as (label, url) pairs (the footer lists them).
    - `theme_css`: the `:root { ... }` rule for the colour and font overrides, '' for the default look. Safe by
      construction (`il2ks.web.theme`), meant for `<style>{{ theme_css }}</style>`.
    - `data_updated`: aware datetime of the last data change, or None before the first one.
    - `il2ks_version`: the installed il2ks version.
    - `bg_css`, `home_bg`, `header_bg`: the `:root` custom properties of the optional banner/header background pictures
      and whether each is set (`web.branding_images`); `icon_url`, `apple_touch_icon_url`: the uploaded tab icon ('' =
      the built-in favicon).
    - `home_feature`: the large front-page image (`HomeFeatureView`) when the admin turned it on and it is usable,
      else None (`web.feature_image`; no query: it comes from the settings row).
    """
    row = site_row(request)
    version_row = request_data_version(request)  # shared with the caching middleware: one read per request
    data_updated: datetime | None = version_row.updated_at if version_row is not None else None
    logo_url = f"{django_settings.MEDIA_URL or '/media/'}{row.logo}" if row.logo else ""
    links = nav_items(row.links)
    css: SafeString = theme_css(
        row.theme, row.heading_font, row.body_font, row.custom_fonts, django_settings.MEDIA_URL or "/media/"
    )
    feature: HomeFeatureView | None = home_feature_view(row)
    media_url = django_settings.MEDIA_URL or "/media/"
    icons = icon_urls(media_url, row.favicon_custom, row.favicon)
    return {
        "bg_css": background_css(
            media_url,
            (row.home_bg_image, row.home_bg_position, row.home_bg_shade),
            (row.header_bg_image, row.header_bg_position, row.header_bg_shade),
        ),
        "home_bg": has_background(row.home_bg_image, "home"),
        "header_bg": has_background(row.header_bg_image, "header"),
        "icon_url": icons[0] if icons else "",
        "apple_touch_icon_url": icons[1] if icons else "",
        "site": row,
        "site_notice": site_notice(row, timezone.now()),
        "home_feature": feature,
        "logo_url": logo_url,
        "nav_links": links,
        "site_links": [(link.label, link.url) for link in links],
        "theme_css": css,
        "data_updated": data_updated,
        "il2ks_version": __version__,
    }
