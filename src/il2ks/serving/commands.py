"""The `il2ks` subcommands of this area: `web`, `run`, `caddyfile`, `service`, `custom` (FR-OPS-1, FR-ADM-6).

`cli.py` owns parsing and exit codes; it calls `add_parsers` and `dispatch`. What it also owns (Django setup, the
migration step with its lock and backup) arrives here as `Hooks`, so this module doesn't import the CLI and tests can
swap the parts that start processes or touch the machine.
"""

import argparse
import dataclasses
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from il2ks import logsetup
from il2ks.config import Config, ConfigError, load_config
from il2ks.ingest.lock import LockBusyError
from il2ks.serving import bootid, caddy, custom, procutil, service, supervisor, webserver
from il2ks.serving.secret import DEV_SECRET_KEY, ensure_secret_key

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_LOCKED = 0, 1, 2, 3
COMMANDS = frozenset({"web", "run", "caddyfile", "service", "custom"})
MIGRATE_WAIT_S = 60.0

log = logging.getLogger("il2ks.run")


@dataclass(slots=True)
class Hooks:
    """What the commands need from the rest of the program; tests replace the parts that act on the machine."""

    django_setup: Callable[[], None]
    migrate: Callable[[Config, str, float | None], object]  # (cfg, command name, lock wait seconds)
    spawn: supervisor.Spawner = supervisor.spawn_subprocess
    runner: service.CommandRunner = service.real_runner
    install_signals: Callable[[threading.Event], Callable[[], None]] = procutil.stop_on_signals
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] | None = None  # None: wait on the stop event


type SubParsers = "argparse._SubParsersAction[argparse.ArgumentParser]"  # pyright: ignore[reportPrivateUsage]


def add_parsers(sub: SubParsers) -> None:
    web = sub.add_parser("web", help="serve the website (granian; --dev for plain-http development)")
    web.add_argument("--dev", action="store_true", help="development: debug mode, plain http, Django's own server")
    web.add_argument("--reload", action="store_true", help="with --dev: restart when the code changes")
    web.add_argument("--migrate-only", action="store_true", help="apply pending database migrations, then exit")
    web.add_argument("--host", help="address to listen on (default: [web] host)")
    web.add_argument("--port", type=int, help="port to listen on (default: [web] port)")

    sub.add_parser("run", help="web + watch + HTTPS proxy together; restarts what crashes (Ctrl+C stops all)")
    sub.add_parser("caddyfile", help="print the Caddyfile that `run` generates from the configuration")

    svc = sub.add_parser(
        "service", help="start `run` at boot: print (or with --install, set up) a systemd unit or task"
    )
    kinds = svc.add_subparsers(dest="service_kind", required=True)
    systemd = kinds.add_parser("systemd", help="Linux: print a systemd unit")
    systemd.add_argument("--name", default=service.DEFAULT_NAME, help="service name (default: il2ks)")
    systemd.add_argument("--user", help="Linux user to run as (default: you)")
    systemd.add_argument("--write", type=Path, metavar="FILE", help="save the unit to FILE instead of printing it")
    systemd.add_argument(
        "--install", action="store_true", help="REALLY install it (needs root): write the unit and enable it"
    )
    schtasks = kinds.add_parser("schtasks", help="Windows: print the schtasks command that starts il2ks at boot")
    schtasks.add_argument("--name", default=service.DEFAULT_NAME, help="task name (default: il2ks)")
    schtasks.add_argument(
        "--user", help="Windows user to run as, DOMAIN\\name (default: you); SYSTEM needs no password"
    )
    schtasks.add_argument(
        "--xml", type=Path, metavar="FILE", help="write a task definition with restart-on-failure to FILE"
    )
    schtasks.add_argument("--install", action="store_true", help="REALLY create the task (run as Administrator)")

    cst = sub.add_parser("custom", help="template and static-file overrides in <data dir>/custom (TD-25)")
    actions = cst.add_subparsers(dest="custom_command", required=True)
    copy = actions.add_parser("copy", help="copy a built-in template or static file into custom/ to edit it")
    copy.add_argument("path", help="like il2ks/home.html or static/css/site.css (`custom list --builtin` shows all)")
    copy.add_argument("--force", action="store_true", help="replace your existing override with a fresh copy")
    lst = actions.add_parser("list", help="your overrides and whether the original changed since you copied it")
    lst.add_argument("--builtin", action="store_true", help="list the built-in files you can override instead")
    accept = actions.add_parser("accept", help="you checked an override against the changed original: stop warning")
    accept.add_argument("path")


def dispatch(ns: argparse.Namespace, hooks: Hooks) -> int:
    command: str = ns.command
    try:
        cfg = load_config(ns.config, create_server_uid=command in {"web", "run"})
    except ConfigError as exc:
        print(f"il2ks: configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if command == "web":
        return cmd_web(cfg, ns, hooks)
    if command == "run":
        return cmd_run(cfg, hooks)
    if command == "caddyfile":
        print(caddy.render_caddyfile(cfg), end="")
        return EXIT_OK
    if command == "service":
        return cmd_service(cfg, ns, hooks)
    return cmd_custom(cfg, ns)


# --- shared ---------------------------------------------------------------------------------------------------------


def export_config(cfg: Config) -> None:
    """Make Django's `settings.py` (and child processes) load the same configuration this process did."""
    os.environ["IL2KS_DATA_DIR"] = str(cfg.data_dir)
    if cfg.source is not None:
        os.environ["IL2KS_CONFIG"] = str(cfg.source)
    if cfg.debug:
        os.environ["IL2KS_DEBUG"] = "1"


def prepare_data_dir(cfg: Config) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    webserver.ensure_custom_dirs(cfg.data_dir)


def start_logging(process: str, cfg: Config) -> None:
    logsetup.configure_logging(
        process, cfg.log_dir, cfg.log_level, server_uid=cfg.server_uid, keep_days=cfg.log_keep_days
    )


# --- web ------------------------------------------------------------------------------------------------------------


def cmd_web(cfg: Config, ns: argparse.Namespace, hooks: Hooks) -> int:
    """Migrate, collect static files, serve. `--dev`: the same on Django's server with DEBUG and plain http."""
    dev: bool = ns.dev
    if dev:
        cfg = dataclasses.replace(cfg, debug=True)
    export_config(cfg)
    bootid.export_boot_id()  # one ETag salt for all workers of this start (TD-28)
    prepare_data_dir(cfg)
    start_logging("web", cfg)
    if not cfg.debug and not cfg.web.secret_key:
        ensure_secret_key(cfg.data_dir)
    hooks.django_setup()

    from django.conf import settings

    if not settings.DEBUG and settings.SECRET_KEY == DEV_SECRET_KEY:
        print(
            "il2ks web: refusing to start with the built-in development secret key. Remove 'secret_key' from "
            "il2ks.toml (a private key is then generated for you), or set a long random one.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    host: str = ns.host or cfg.web.host
    port: int = ns.port or cfg.web.port
    if ns.migrate_only:
        try:
            hooks.migrate(cfg, "web", MIGRATE_WAIT_S)
        except LockBusyError as exc:
            print(f"il2ks web: {exc}", file=sys.stderr)
            return EXIT_LOCKED
        return EXIT_OK
    if host not in {"127.0.0.1", "localhost", "::1"} and not cfg.debug:
        log.warning("the web server listens on %s, not only on this machine: keep it behind your firewall", host)
    try:
        hooks.migrate(cfg, "web", MIGRATE_WAIT_S)
        if not settings.DEBUG:
            from django.core.management import call_command

            call_command("collectstatic", interactive=False, verbosity=0)
        if dev:
            log.info("development mode: DEBUG on, http only, http://%s:%d/", host, port)
            webserver.serve_dev(host, port, reload=ns.reload)
        else:
            webserver.serve_granian(cfg, host, port)
    except LockBusyError as exc:
        print(f"il2ks web: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except KeyboardInterrupt:
        log.info("stopped")
    return EXIT_OK


def _wait(stop: threading.Event, seconds: float) -> None:
    stop.wait(seconds)


# --- run ------------------------------------------------------------------------------------------------------------


class StackError(RuntimeError):
    """`il2ks run` can't start; the message says what to do."""


CADDY_HELP = (
    "Caddy (the HTTPS proxy) was not found. Install it (see docs/install.md: on Windows "
    "`winget install CaddyServer.Caddy`, on Linux your package manager or caddyserver.com/docs/install), "
    "or put caddy{exe} into {bin}, or set [https] "
    'caddy_path in il2ks.toml. If you run your own proxy (nginx, IIS), set [https] mode = "external".'
)


def il2ks_command(cfg: Config, *args: str, python: str | None = None) -> list[str]:
    argv = [python or sys.executable, "-m", "il2ks"]
    if cfg.source is not None:
        argv += ["--config", str(cfg.source)]
    return [*argv, *args]


def build_child_specs(cfg: Config, *, caddy_binary: Path | None, env: dict[str, str]) -> list[supervisor.ChildSpec]:
    """The processes `run` supervises: web, watch (when a log folder is configured), Caddy (unless external or debug).

    Raises `StackError` when Caddy is needed and missing."""
    child_env = {**env, "PYTHONUNBUFFERED": "1"}
    specs = [supervisor.ChildSpec("web", il2ks_command(cfg, "web"), env=child_env)]
    if cfg.logs.dir is not None:
        specs.append(supervisor.ChildSpec("watch", il2ks_command(cfg, "watch"), env=child_env))
    else:
        log.warning("no log folder configured ([logs] dir): the site runs, but nothing is ingested")
    if cfg.https.mode == "caddy" and not cfg.debug:
        if caddy_binary is None:
            raise StackError(CADDY_HELP.format(exe=".exe" if os.name == "nt" else "", bin=cfg.data_dir / "bin"))
        setup = caddy.prepare_caddy(cfg, caddy_binary)
        warning = caddy.cert_plan_warning(setup.plan)
        if warning:
            log.warning(warning)
            print(f"\n*** {warning}\n", file=sys.stderr)
        specs.append(supervisor.ChildSpec("caddy", setup.command, env=setup.env, forward_output=True))
    return specs


def cmd_run(cfg: Config, hooks: Hooks) -> int:
    export_config(cfg)
    prepare_data_dir(cfg)
    start_logging("run", cfg)
    running = procutil.running_stack(cfg.data_dir)
    if running is not None:
        print(f"il2ks run: already running for {cfg.data_dir} (PID {running.pid})", file=sys.stderr)
        return EXIT_LOCKED
    if not cfg.debug and not cfg.web.secret_key:
        ensure_secret_key(cfg.data_dir)  # before the children start, so they don't race to create it
    try:
        specs = build_child_specs(cfg, caddy_binary=caddy.find_caddy(cfg), env=dict(os.environ))
    except StackError as exc:
        print(f"il2ks run: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # Migrate once, before anything else starts, so `web` and `watch` don't fight over the writer lock at boot.
    log.info("checking the database")
    if hooks.runner(il2ks_command(cfg, "web", "--migrate-only")) != 0:
        print("il2ks run: the database could not be prepared (see above); not starting", file=sys.stderr)
        return EXIT_FAILED

    stop = threading.Event()
    finished = hooks.install_signals(stop)
    runner = supervisor.Supervisor(
        specs,
        hooks.spawn,
        stop,
        hooks.clock,
        hooks.sleep or (lambda seconds: _wait(stop, seconds)),
        on_change=lambda pids: procutil.write_run_state(cfg.data_dir, pids),
    )
    log.info("starting %s (data dir %s)", ", ".join(s.name for s in specs), cfg.data_dir)
    try:
        runner.run()
    finally:
        procutil.clear_run_state(cfg.data_dir)
        finished()
    log.info("stopped")
    return EXIT_OK


# --- service --------------------------------------------------------------------------------------------------------


def cmd_service(cfg: Config, ns: argparse.Namespace, hooks: Hooks) -> int:
    if ns.service_kind == "systemd":
        return _service_systemd(cfg, ns, hooks)
    return _service_schtasks(cfg, ns, hooks)


def _service_systemd(cfg: Config, ns: argparse.Namespace, hooks: Hooks) -> int:
    import getpass

    name: str = ns.name
    unit = service.render_systemd_unit(cfg, name=name, user=ns.user or getpass.getuser())
    if ns.install:
        if sys.platform != "win32" and os.geteuid() != 0:
            print("il2ks service systemd --install: needs root. Run it with sudo.", file=sys.stderr)
            return EXIT_USAGE
        target = service.SYSTEMD_DIR / f"{name}.service"
        target.write_text(unit, encoding="utf-8")
        print(f"wrote {target}")
        for args in service.systemd_install_commands(name):
            print("running:", service.display(args))
            if hooks.runner(args) != 0:
                print(f"il2ks service: {service.display(args)} failed", file=sys.stderr)
                return EXIT_FAILED
        print(f"{name} is enabled and started. Follow it with: journalctl -u {name} -f")
        return EXIT_OK
    if ns.write is not None:
        ns.write.write_text(unit, encoding="utf-8")
        print(f"wrote {ns.write}")
    else:
        print(unit, end="")
    target = service.SYSTEMD_DIR / f"{name}.service"
    print(
        f"\nTo install: sudo il2ks service systemd --install   (or save the unit as {target}, then run "
        f"`sudo systemctl daemon-reload && sudo systemctl enable --now {name}`)",
        file=sys.stderr,
    )
    return EXIT_OK


def _service_schtasks(cfg: Config, ns: argparse.Namespace, hooks: Hooks) -> int:
    name: str = ns.name
    user: str = ns.user or service.default_windows_user()
    xml_path: Path | None = ns.xml
    if ns.install and xml_path is None:
        xml_path = cfg.data_dir / service.TASK_XML_NAME
    if xml_path is not None:
        service.write_task_xml(xml_path, service.render_task_xml(cfg))
        args = service.schtasks_xml_args(name=name, user=user, xml_path=xml_path)
        print(f"wrote {xml_path} (starts at boot, restarts il2ks if it dies)")
    else:
        args = service.schtasks_create_args(cfg, name=name, user=user)
    if ns.install:
        print("running:", service.display(args))
        if hooks.runner(args) != 0:
            print("il2ks service: schtasks failed (run this from an Administrator prompt)", file=sys.stderr)
            return EXIT_FAILED
        print(f'Created. Start it now with: schtasks /Run /TN "{name}"')
        return EXIT_OK
    print("Run this in an Administrator command prompt (it asks for the password of the user):\n")
    print(service.display(args))
    if xml_path is None:
        print("\nAdd --xml FILE to also get restart-on-failure for il2ks itself.", file=sys.stderr)
    return EXIT_OK


# --- custom ---------------------------------------------------------------------------------------------------------

_STATE_TEXT: dict[str, str] = {
    "ok": "up to date",
    "original-changed": "ORIGINAL CHANGED since you copied it: compare, then `il2ks custom accept`",
    "original-missing": "the built-in file no longer exists: this override does nothing useful",
    "override-deleted": "you deleted the override file",
}


def cmd_custom(cfg: Config, ns: argparse.Namespace) -> int:
    try:
        if ns.custom_command == "copy":
            status = custom.copy_builtin(cfg, ns.path, force=ns.force)
            print(f"copied {status.original} to {status.override}")
            print("Edit that copy; restart il2ks to see the change. `il2ks custom list` tracks it for upgrades.")
        elif ns.custom_command == "accept":
            status = custom.accept_original(cfg, ns.path)
            print(f"{status.key}: recorded the current original")
        elif ns.builtin:
            for kind in custom.KINDS:
                for rel in custom.builtin_listing(kind):
                    print(f"{kind}/{rel}")
        else:
            _print_overrides(cfg)
    except custom.CustomError as exc:
        print(f"il2ks custom: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _print_overrides(cfg: Config) -> None:
    statuses = custom.override_statuses(cfg)
    for status in statuses:
        print(f"{status.key}: {_STATE_TEXT[status.state]} (copied with il2ks {status.copied_version})")
        if status.state == "original-changed":
            print(f"    yours:    {status.override}\n    built-in: {status.original}")
    for kind, rel, original in custom.untracked_overrides(cfg):
        print(f"{kind}/{rel}: not recorded (made by hand), cannot tell if the original changed; built-in: {original}")
    if not statuses and not custom.untracked_overrides(cfg):
        print(f"no overrides yet. Copy a file with `il2ks custom copy <path>`; they live in {custom.custom_dir(cfg)}")
