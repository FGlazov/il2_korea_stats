"""`il2ks web`: hypercorn serving the Django WSGI app (TD-10), or Django's own server for development.

Logging: hypercorn starts worker processes (spawned, not forked) and hands each one a `logging.config.dictConfig` dict.
We give it `logsetup.dict_config`, so the parent and every worker write the same daily JSON file as the other il2ks
processes (TD-27), and hypercorn's own messages go through the same handlers.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from il2ks import logsetup
from il2ks.config import Config

if TYPE_CHECKING:
    from hypercorn.config import Config as HypercornConfig

ASGI_TARGET = "il2ks.asgi:application"
CUSTOM_SUBDIRS = ("templates", "static")
HYPERCORN_LOGGERS = ("hypercorn.error", "hypercorn.access")
KEEP_ALIVE_S = 130
"""Longer than Caddy keeps an idle connection (2 minutes): the proxy closes first, so it never reuses a closing one."""
STOP_WAIT_S = 30
"""How long a stop waits for requests in flight."""

log = logging.getLogger("il2ks.web")


def ensure_custom_dirs(data_dir: Path) -> None:
    """Create `<data dir>/custom/templates` and `custom/static` (TD-25), so the admin always finds them there."""
    for name in CUSTOM_SUBDIRS:
        (data_dir / "custom" / name).mkdir(parents=True, exist_ok=True)


def hypercorn_log_config(cfg: Config) -> dict[str, object]:
    """Root handlers for the `web` process; hypercorn's own loggers just propagate into them."""
    config = logsetup.dict_config(
        "web", cfg.log_dir, cfg.log_level, server_uid=cfg.server_uid, keep_days=cfg.log_keep_days
    )
    config["loggers"] = {name: {"handlers": [], "propagate": True} for name in HYPERCORN_LOGGERS}
    return config


def hypercorn_config(cfg: Config, host: str, port: int, *, workers: int) -> "HypercornConfig":
    from hypercorn.config import Config as HypercornConfig

    config = HypercornConfig()
    config.application_path = ASGI_TARGET
    config.bind = [f"[{host}]:{port}" if ":" in host else f"{host}:{port}"]
    config.workers = workers
    config.logconfig_dict = hypercorn_log_config(cfg)
    config.loglevel = cfg.log_level
    config.errorlog = None  # into `logconfig_dict`, not to stderr
    config.accesslog = None  # Caddy writes the access log (TD-23)
    config.include_server_header = False
    config.keep_alive_timeout = KEEP_ALIVE_S
    config.graceful_timeout = STOP_WAIT_S
    return config


def serve_hypercorn(cfg: Config, host: str, port: int, *, workers: int) -> None:
    """Serve until stopped (Ctrl+C, SIGTERM, Ctrl+Break), with `workers` processes of `[web] threads` threads each.

    Not uvicorn: to stop a worker on Windows it sends Ctrl+Break, which reaches every process on the console (Caddy,
    `watch`, `il2ks run` itself). Hypercorn stops its workers with a shared event. A worker that dies ends the server
    too, usually with exit code 0 (hypercorn loses the code): `il2ks run` starts it again either way."""
    from hypercorn.run import run

    log.info("serving on http://%s:%d (%d worker(s), %d thread(s) each)", host, port, workers, cfg.web.threads)
    run(hypercorn_config(cfg, host, port, workers=workers))


def serve_dev(host: str, port: int, *, reload: bool) -> None:
    """Django's development server: plain http, DEBUG, static files straight from the source folders."""
    from django.core.management import call_command

    call_command("runserver", f"{host}:{port}", use_reloader=reload)
