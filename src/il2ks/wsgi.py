import os

from django.core.handlers.wsgi import WSGIHandler
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")

application: WSGIHandler = get_wsgi_application()

# The front-page image (FR-ADM-2) follows its source file by polling; the thread lives in every web process, so it
# works with `il2ks run`, `web` alone, the Windows service and Docker. See `il2ks.web.feature_image`.
from il2ks.web.feature_image import poller_enabled, start_poller  # noqa: E402 - needs Django set up first

if poller_enabled():
    start_poller()
