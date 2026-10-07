"""`/healthz`: the uptime monitor's URL.

200 `ok` when one row can be read from the database, 503 with a short body when that fails. No login, no ETag, no page
cache: `Cache-Control: no-store` and `web.caching` leaves `/healthz` alone, so a monitor (or a CDN in front) never gets
an old answer. It reads the data-version row only (one row, one tiny query): it proves the database file opens and the
schema is there, not that the statistics are fresh. Exempt from the HTTPS redirect (`serving.djsettings`), so a monitor
on the same machine can ask the web server directly over plain http (`http://127.0.0.1:8000/healthz`).
"""

import logging

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_safe

from il2ks.db.models import DataVersion

log = logging.getLogger(__name__)


def _answer(status: int, body: str) -> HttpResponse:
    response = HttpResponse(body, status=status, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


@require_safe
def healthz(request: HttpRequest) -> HttpResponse:
    try:
        DataVersion.objects.only("version").first()  # may be None before the first data change: the read is the point
    except Exception as exc:  # any failure at all means "not healthy"; the monitor only needs the status code
        log.warning("healthz: the database read failed: %s", exc)
        return _answer(503, "database unavailable\n")
    return _answer(200, "ok\n")
