"""`GET /p/<slug>/`: a Markdown page the admin wrote, shown in the site layout (roadmap 0.2.0, OQ-133).

One read: the `Page` row carries the stored HTML of every language (`web.pages`). Nothing is rendered per request. The
text is the viewer's language when the page has it, else the base text. The response adds a small content security
policy of its own: the layout has no CSP, and a page may hotlink images from other hosts over https (and nothing else
from elsewhere, no plugins, no foreign `<base>`).
"""

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.safestring import mark_safe
from django.utils.translation import get_language

from il2ks.db.models import Page
from il2ks.web.pages import pick_text

PAGE_CSP = "img-src 'self' https: data:; object-src 'none'; base-uri 'self'; form-action 'self'"


def page(request: HttpRequest, slug: str) -> HttpResponse:
    row = Page.objects.filter(slug=slug).first()
    if row is None:
        raise Http404
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
