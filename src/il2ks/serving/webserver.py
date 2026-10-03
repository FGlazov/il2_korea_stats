"""`il2ks web`: granian serving the Django WSGI app (TD-10), or Django's own server for development.

Logging: granian starts worker processes (spawned, not forked) and hands each one a `logging.config.dictConfig` dict.
We give it `logsetup.dict_config`, so the parent and every worker write the same daily JSON file as the other il2ks
processes (TD-27), and granian's own messages go through the same handlers.
"""

import logging
from pathlib import Path

from il2ks import logsetup
from il2ks.config import Config
from il2ks.serving import procutil

WSGI_TARGET = "il2ks.wsgi:application"
CUSTOM_SUBDIRS = ("templates", "static")

log = logging.getLogger("il2ks.web")


def ensure_custom_dirs(data_dir: Path) -> None:
    """Create `<data dir>/custom/templates` and `custom/static` (TD-25), so the admin always finds them there."""
    for name in CUSTOM_SUBDIRS:
        (data_dir / "custom" / name).mkdir(parents=True, exist_ok=True)


def granian_log_config(cfg: Config) -> dict[str, object]:
    """Root handlers for the `web` process; granian's own loggers just propagate into them."""
    config = logsetup.dict_config(
        "web", cfg.log_dir, cfg.log_level, server_uid=cfg.server_uid, keep_days=cfg.log_keep_days
    )
    config["loggers"] = {
        "_granian": {"handlers": [], "propagate": True},
        "granian.access": {"handlers": [], "propagate": True},
    }
    return config


def serve_granian(cfg: Config, host: str, port: int) -> None:
    """Serve until stopped (Ctrl+C, SIGTERM, Ctrl+Break). On Windows granian runs one worker whatever `workers` says."""
    from granian import Granian
    from granian.constants import Interfaces
    from granian.log import LogLevels

    server = Granian(
        WSGI_TARGET,
        address=host,
        port=port,
        interface=Interfaces.WSGI,
        workers=cfg.web.workers,
        blocking_threads=cfg.web.threads,
        log_level=LogLevels(cfg.log_level.lower()),
        log_dictconfig=granian_log_config(cfg),
        log_access=False,  # Caddy writes the access log (TD-23)
    )
    log.info("serving on http://%s:%d (%d worker(s), %d thread(s) each)", host, port, cfg.web.workers, cfg.web.threads)

    # `il2ks run` stops us with Ctrl+Break on Windows; granian's own handler can't wake its main loop for that (see
    # procutil.on_windows_console_stop), so wake it ourselves the way its signal handler would.
    def wake_main_loop() -> None:
        server.signal_handler_interrupt()  # pyright: ignore[reportUnknownMemberType]

    procutil.on_windows_console_stop(wake_main_loop)
    server.serve()


def serve_dev(host: str, port: int, *, reload: bool) -> None:
    """Django's development server: plain http, DEBUG, static files straight from the source folders."""
    from django.core.management import call_command

    call_command("runserver", f"{host}:{port}", use_reloader=reload)
