"""Markdown pages in the navigation: rendering, sanitising and the choice of text (roadmap 0.2.0, OQ-133).

The admin types Markdown; `render_markdown` turns it into HTML **once, on save**, and the page view only prints the
stored HTML (never per request). The output is sanitised with nh3 (an allowlist of tags and attributes; only http(s)
and relative links): a `<script>`, an `onclick=`, a `javascript:` or `data:` link never reaches a visitor, whatever the
admin pasted. Images may be hotlinked (`![](https://...)` stays a link to the remote host).

`published_translations` builds the per-language copy kept on the `Page` row so the view reads one row.
`pick_text` chooses what a viewer sees: their language's text if there is one, else the base text. Nothing is forced.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

import nh3
from django.conf import settings
from django.urls import reverse
from markdown_it import MarkdownIt

from il2ks.db.models import NavLink, Page, PageTranslation, SiteSettings

MAX_SOURCE_LENGTH: Final = 100_000

# CommonMark plus tables and strikethrough; raw HTML in the source is allowed through the parser and then filtered by
# the allowlist below, so `<details>` or `<kbd>` work but `<script>` does not.
_MARKDOWN: Final = MarkdownIt("commonmark", {"html": True, "linkify": False}).enable(["table", "strikethrough"])

ALLOWED_TAGS: Final = frozenset(
    {
        "a", "abbr", "b", "blockquote", "br", "code", "del", "details", "summary", "em", "h1", "h2", "h3", "h4", "h5",
        "h6", "hr", "i", "img", "kbd", "li", "ol", "p", "pre", "s", "strong", "sub", "sup", "table", "tbody", "td",
        "tfoot", "th", "thead", "tr", "ul",
    }
)  # fmt: skip
ALLOWED_ATTRIBUTES: Final = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "ol": {"start"},
    "td": {"align"},
    "th": {"align"},
    "abbr": {"title"},
    "details": {"open"},
}
ALLOWED_SCHEMES: Final = frozenset({"http", "https"})


def render_markdown(source: str) -> str:
    """The sanitised HTML of `source` (CommonMark). Safe to print without escaping."""
    html = _MARKDOWN.render(source[:MAX_SOURCE_LENGTH])
    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        attributes={tag: set(names) for tag, names in ALLOWED_ATTRIBUTES.items()},
        url_schemes=set(ALLOWED_SCHEMES),
        link_rel="noopener noreferrer",
        set_tag_attribute_values={"img": {"loading": "lazy", "referrerpolicy": "no-referrer"}},
    )


def site_languages() -> list[tuple[str, str]]:
    """The site's languages as (code, own-language name)."""
    return [(str(code), str(name)) for code, name in settings.LANGUAGES]


def published_translations(translations: Iterable[PageTranslation]) -> dict[str, dict[str, str]]:
    return {t.language: {"title": t.title.strip(), "html": t.html} for t in translations if t.html.strip()}


def publish_page(page: Page) -> None:
    """Re-render every text of `page` and store them (the page row and its translation rows); the caller bumps the
    data version. Also refreshes the navigation links that point at the page (its slug may have changed)."""
    page.html = render_markdown(page.source)
    rows = list(PageTranslation.objects.filter(page=page))
    for row in rows:
        row.html = render_markdown(row.source)
        row.save(update_fields=["html"])
    page.translations = published_translations(rows)
    page.save(update_fields=["html", "translations", "updated_at"])
    for site in SiteSettings.objects.filter(nav_links__page=page).distinct():
        publish_nav_links(site)


def publish_nav_links(site: SiteSettings) -> None:
    """Write the `NavLink` rows of `site` into `SiteSettings.links` (pages read that copy; no extra query). A link to a
    page is stored with that page's address and `"page": "1"` (it opens in the same tab)."""
    published: list[dict[str, str]] = []
    for link in NavLink.objects.filter(site=site).select_related("page"):
        if link.page is not None:
            address = reverse("web:page", args=[link.page.slug])
            published.append({"label": link.label, "url": address, "icon": link.icon, "page": "1"})
        else:
            published.append({"label": link.label, "url": link.url, "icon": link.icon})
    site.links = published
    site.save(update_fields=["links", "updated_at"])


@dataclass(frozen=True, slots=True)
class PageText:
    title: str
    html: str
    language: str  # the language the text is written in


def pick_text(page: Page, language: str) -> PageText:
    """The viewer's language text when the page has one (`pt-br` as is, else its main part), else the base text."""
    for code in (language, language.split("-")[0]):
        entry = page.translations.get(code)
        if code and code != page.base_language and entry and entry.get("html", "").strip():
            return PageText(entry.get("title") or page.title, entry["html"], code)
    return PageText(page.title, page.html, page.base_language)
