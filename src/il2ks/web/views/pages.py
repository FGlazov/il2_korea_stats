"""`GET /p/<slug>/`: a Markdown page the admin wrote, shown in the site layout (roadmap 0.2.0, OQ-133).

One read: the `Page` row carries the stored HTML of every language (`web.pages`). Nothing is rendered per request. The
text is the viewer's language when the page has it, else the base text. The response adds a small content security
policy of its own: the layout has no CSP, and a page may hotlink images from other hosts over https (and no script
from elsewhere, no plugins, no foreign `<base>`).
"""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.safestring import mark_safe
from django.utils.translation import get_language

from il2ks.db.models import Page
from il2ks.web.pages import pick_text

# No `style-src`: the layout prints the owner's theme and background as inline `<style>` blocks. No inline
# script exists in the layout (a test checks), so scripts may come from the site only.
PAGE_CSP = "script-src 'self'; img-src 'self' https: data:; object-src 'none'; base-uri 'self'; form-action 'self'"


def page(request: HttpRequest, slug: str) -> HttpResponse:
    # `slug` is unique: `.get()` has no ORDER BY (`.first()` would add `Page.Meta.ordering`, a temp B-tree in the plan).
    try:
        row = Page.objects.get(slug=slug)
    except Page.DoesNotExist:
        raise Http404 from None
    text = pick_text(row, get_language() or "en")
    context = {
        "page_title": text.title,
        "title": text.title,
        "body": mark_safe(text.html),
        "text_language": text.language,
    }
    response = render(request, "il2ks/page.html", context)
    response["Content-Security-Policy"] = PAGE_CSP
    return response
