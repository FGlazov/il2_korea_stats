"""What hypercorn serves (TD-10): the plain WSGI app behind a2wsgi's thread pool, which `[web] threads` sizes.

Not Django's ASGI handler: our views are all synchronous, so it would only add a hop, and it reads a whole static file
into memory for each request, because WhiteNoise's files are synchronous iterators.

A worker sets up logging first: hypercorn applies its own log configuration only after it has imported this module."""

import logging
import os

from a2wsgi import WSGIMiddleware
from django.conf import settings

from il2ks import logsetup
from il2ks.config import load_config

_cfg = load_config(create_server_uid=False)  # `il2ks web` made it
logsetup.configure_logging(
    "web", _cfg.log_dir, _cfg.log_level, server_uid=_cfg.server_uid, keep_days=_cfg.log_keep_days
)

from il2ks.web import page_cache  # noqa: E402 - after the logging setup, like the app
from il2ks.wsgi import application as wsgi_application  # noqa: E402 - logs from its start must reach the file

application = WSGIMiddleware(
    wsgi_application,  # pyright: ignore[reportArgumentType]  # Django types the environ more narrowly
    workers=settings.IL2KS_WEB_THREADS,
)

page_cache.start_warmer(wsgi_application)

logging.getLogger("il2ks.web").info("web worker %d started", os.getpid())
