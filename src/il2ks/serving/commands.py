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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from il2ks import logsetup
from il2ks.config import Config, ConfigError, find_config_file, load_config
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.serving import bootid, caddy, custom, procutil, service, setup_token, supervisor, webserver
from il2ks.serving.secret import DEV_SECRET_KEY, ensure_secret_key

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_LOCKED = 0, 1, 2, 3
COMMANDS = frozenset({"web", "run", "caddyfile", "service", "custom"})
MIGRATE_WAIT_S = 60.0
CONFIG_SETTLE_S = 5.0
"""A changed configuration file is acted on after it stayed unchanged this long: the page that wrote it (the setup page)
is still answering its browser, and a half-written file is never loaded."""

log = logging.getLogger("il2ks.run")


def no_containment() -> bool:
    return False


@dataclass(slots=True)
class Hooks:
    """What the commands need from the rest of the program; tests replace the parts that act on the machine."""

    django_setup: Callable[[], None]
    migrate: Callable[[Config, str, float | None], object]  # (cfg, command name, lock wait seconds)
    spawn: supervisor.Spawner = supervisor.spawn_subprocess
    runner: service.CommandRunner = service.real_runner
    install_signals: Callable[[threading.Event], Callable[[], None]] = procutil.stop_on_signals
    contain_children: Callable[[], bool] = no_containment  # `run`: children die with it (cli.py wires the real one)
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] | None = None  # None: wait on the stop event
    setup_pending: Callable[[], bool] = lambda: False  # no admin account yet? (needs Django and a migrated database)


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
    lst = actions.add_parser("list", help="your overrides and whether they are based on the current built-in version")
    lst.add_argument("--builtin", action="store_true", help="list the built-in files you can override instead")
    diff = actions.add_parser("diff", help="show how an override differs from the built-in file it replaces")
    diff.add_argument("path", help="like il2ks/home.html or static/css/site.css")
    accept = actions.add_parser(
        "accept", help="you brought an override up to date with the changed built-in file: stop warning"
    )
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
        return cmd_run(cfg, hooks, ns.config)
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
    custom.log_problems(custom.custom_dir(cfg), log)  # the admin pages show the same warning as a red banner
    try:
        hooks.migrate(cfg, "web", MIGRATE_WAIT_S)
        announce_setup(cfg, host, port, hooks)
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


def announce_setup(cfg: Config, host: str, port: int, hooks: Hooks) -> None:
    """While no admin account exists, create the one-time setup token and say where to open the setup page.

    The page (`/setup/`, doc 07 option B) answers only while `<data dir>/setup-token.txt` exists, only on this
    machine and only with the token. Any admin account removes the file again (the page is gone for good)."""
    if setup_token.page_disabled(os.environ) or not hooks.setup_pending():
        setup_token.discard_token(cfg.data_dir)
        return
    token = setup_token.ensure_token(cfg.data_dir)
    url = setup_token.setup_url(host, port, token)
    # The token is a credential: it goes to the console and its own private file, never into the log files.
    log.warning(
        "first-run setup is pending: the address of the setup page (with its one-time token) is printed on the console "
        "and kept in %s",
        setup_token.token_path(cfg.data_dir),
    )
    print(
        "\n*** First-run setup is pending (no admin account yet).\n"
        "*** Open this address in a browser ON THIS COMPUTER to finish it:\n"
        f"***   {url}\n"
        f"*** (the token is also in {setup_token.token_path(cfg.data_dir)})\n",
        flush=True,
    )


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


def build_child_specs(
    cfg: Config, *, caddy_binary: Path | None, env: dict[str, str], caddy_optional: bool = False
) -> list[supervisor.ChildSpec]:
    """The processes `run` supervises: web, watch (when a log folder is configured), Caddy (unless external or debug).

    Raises `StackError` when Caddy is needed and missing, unless `caddy_optional`: then the site runs without it and the
    problem is logged loudly (first run before setup, or right after the setup page changed the settings: the web server
    must stay up so the admin can see what to fix)."""
    child_env = {**env, "PYTHONUNBUFFERED": "1"}
    specs = [supervisor.ChildSpec("web", il2ks_command(cfg, "web"), env=child_env)]
    if cfg.logs.dir is not None:
        specs.append(supervisor.ChildSpec("watch", il2ks_command(cfg, "watch"), env=child_env))
    else:
        log.warning("no log folder configured ([logs] dir): the site runs, but nothing is ingested")
    if cfg.https.mode == "caddy" and not cfg.debug:
        if caddy_binary is None:
            help_text = CADDY_HELP.format(exe=".exe" if os.name == "nt" else "", bin=cfg.data_dir / "bin")
            if not caddy_optional:
                raise StackError(help_text)
            log.error("starting without HTTPS: %s", help_text)
            print(f"\n*** Starting without HTTPS. {help_text}\n", file=sys.stderr)
            return specs
        setup = caddy.prepare_caddy(cfg, caddy_binary)
        warning = caddy.cert_plan_warning(setup.plan)
        if warning:
            log.warning(warning)
            print(f"\n*** {warning}\n", file=sys.stderr)
        specs.append(supervisor.ChildSpec("caddy", setup.command, env=setup.env, forward_output=True))
    return specs


def _warn_about_overrides(cfg: Config) -> None:
    """The server owner starts `il2ks run` from a console (or reads its log): say loudly that overrides need a look.
    (`il2ks web`, started by `run`, writes one log line per file.)"""
    problems = custom.startup_problems(custom.custom_dir(cfg))
    if not problems:
        return
    names = ", ".join(item.key for item in problems)
    log.warning("%d custom override(s) need attention: %s", len(problems), names)
    print(
        f"\n*** WARNING: {len(problems)} file(s) in {custom.custom_dir(cfg)} are based on an older il2ks version "
        f"(or an unknown one) and may break or hide new content:\n    {names}\n"
        "*** Run `il2ks custom list` to see them and `il2ks custom diff <path>` to see what differs.\n",
        file=sys.stderr,
    )


RUN_LOCK_WAIT_S = 3.0  # a doctor probe may hold the lock for a moment; a real second `run` waits this long, then fails


type Signature = tuple[str, int, int] | None
"""(path, modification time, size) of the configuration file `load_config` would read now; None = there is none."""


def config_signature(explicit: Path | None, env: Mapping[str, str]) -> Signature:
    try:
        file = find_config_file(explicit, env)
        return None if file is None else (str(file), file.stat().st_mtime_ns, file.stat().st_size)
    except (ConfigError, OSError):
        return None


class ConfigWatch:
    """Notices that the configuration file changed (the setup page wrote it), for `il2ks run` to restart with it.

    `changed()` is polled by the supervisor. It turns True once the file has differed from the one the stack started
    with and then stayed unchanged for `settle_s`, and the new file loads (`new_config`); a file that does not load is
    reported once and the stack keeps running on the old settings until the file changes again. While `hold()` is true
    (a setup page submit is in flight, `setup_token.finishing`) nothing restarts, and `settle_s` counts from its end, so
    the browser always gets its answer before the web server is stopped."""

    def __init__(
        self,
        explicit: Path | None,
        clock: Callable[[], float],
        *,
        settle_s: float = CONFIG_SETTLE_S,
        env: Mapping[str, str] | None = None,
        hold: Callable[[], bool] | None = None,
    ) -> None:
        self._explicit = explicit
        self._clock = clock
        self._hold = hold
        self._settle_s = settle_s
        self._env = os.environ if env is None else env
        self._started_with = config_signature(explicit, self._env)
        self._candidate: Signature = self._started_with
        self._candidate_since = 0.0
        self._rejected: Signature = None
        self.new_config: Config | None = None

    def changed(self) -> bool:
        current = config_signature(self._explicit, self._env)
        if current == self._started_with:
            self._candidate = current
            return False
        now = self._clock()
        if current != self._candidate:
            self._candidate, self._candidate_since = current, now
            return False
        if self._hold is not None and self._hold():
            self._candidate_since = now  # the setup page is still answering: the settle time starts when it is done
            return False
        if now - self._candidate_since < self._settle_s or current == self._rejected:
            return False
        try:
            self.new_config = load_config(self._explicit, self._env)
        except ConfigError as exc:
            self._rejected = current
            log.error("the configuration file changed but does not load, so it is ignored for now: %s", exc)
            return False
        return True


def _config_watch(config_arg: Path | None, hooks: Hooks, cfg: Config) -> ConfigWatch:
    data_dir = cfg.data_dir
    return ConfigWatch(config_arg, hooks.clock, hold=lambda: setup_token.finishing(data_dir))


def _run_state_writer(data_dir: Path) -> Callable[[dict[str, int]], None]:
    def write(pids: dict[str, int]) -> None:
        procutil.write_run_state(data_dir, pids)

    return write


def cmd_run(cfg: Config, hooks: Hooks, config_arg: Path | None = None) -> int:
    export_config(cfg)
    prepare_data_dir(cfg)
    start_logging("run", cfg)
    # One `run` per data dir, by an OS lock held until we exit (however we exit): never "stuck" after a crash or reboot.
    lock = RunLock()
    try:
        lock.take(cfg.data_dir)
    except LockBusyError as exc:
        _print_run_busy(cfg.data_dir, exc)
        return EXIT_LOCKED
    try:
        return _run_locked(cfg, hooks, config_arg, lock)
    finally:
        lock.release()


def _print_run_busy(data_dir: Path, exc: LockBusyError) -> None:
    who = f" ({exc.holder.describe()})" if exc.holder is not None else ""
    print(f"il2ks run: already running for {data_dir}{who}", file=sys.stderr)


class RunLock:
    """The run lock of the data folder `il2ks run` currently serves. A configuration change may name another data
    folder (the setup page, or an edit by hand): `take` then locks the new one before the old one is let go, so a
    folder that is busy leaves the old lock in place."""

    def __init__(self) -> None:
        self._lock: WriterLock | None = None
        self.data_dir: Path | None = None

    def take(self, data_dir: Path) -> None:
        """Hold the run lock of `data_dir`. Raises `LockBusyError` if another `il2ks run` holds it."""
        if self._lock is not None and self.data_dir == data_dir:
            return
        new = procutil.run_lock(data_dir, wait=RUN_LOCK_WAIT_S)
        new.acquire()
        old = self._lock
        self._lock, self.data_dir = new, data_dir
        if old is not None:
            old.release()

    def release(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None


def _run_locked(cfg: Config, hooks: Hooks, config_arg: Path | None, lock: RunLock) -> int:
    if hooks.contain_children():
        log.info("children are tied to this process: they stop when it ends, however it ends")
    if not cfg.debug and not cfg.web.secret_key:
        ensure_secret_key(cfg.data_dir)  # before the children start, so they don't race to create it
    watch = _config_watch(config_arg, hooks, cfg)
    if cfg.source is None:
        log.warning("no il2ks.toml found: open the setup page when `web` prints its address (or run `il2ks setup`)")
    try:
        specs = build_child_specs(
            cfg, caddy_binary=caddy.find_caddy(cfg), env=dict(os.environ), caddy_optional=cfg.source is None
        )
    except StackError as exc:
        print(f"il2ks run: {exc}", file=sys.stderr)
        return EXIT_USAGE

    _warn_about_overrides(cfg)

    # Migrate once, before anything else starts, so `web` and `watch` don't fight over the writer lock at boot.
    log.info("checking the database")
    if hooks.runner(il2ks_command(cfg, "web", "--migrate-only")) != 0:
        print("il2ks run: the database could not be prepared (see above); not starting", file=sys.stderr)
        return EXIT_FAILED

    stop = threading.Event()
    finished = hooks.install_signals(stop)
    try:
        while True:
            runner = supervisor.Supervisor(
                specs,
                hooks.spawn,
                stop,
                hooks.clock,
                hooks.sleep or (lambda seconds: _wait(stop, seconds)),
                on_change=_run_state_writer(cfg.data_dir),
                restart_when=watch.changed,
            )
            log.info("starting %s (data dir %s)", ", ".join(s.name for s in specs), cfg.data_dir)
            if not runner.run():
                break
            assert watch.new_config is not None
            old_dir = cfg.data_dir
            cfg = watch.new_config
            log.info("the configuration changed (%s): restarting with the new settings", cfg.source)
            if cfg.data_dir != old_dir:
                log.info("the data folder changed: %s -> %s", old_dir, cfg.data_dir)
                try:
                    lock.take(cfg.data_dir)  # one `run` per data folder, also after a change of folder
                except LockBusyError as exc:
                    _print_run_busy(cfg.data_dir, exc)
                    return EXIT_LOCKED
                procutil.clear_run_state(old_dir)
            export_config(cfg)
            prepare_data_dir(cfg)
            if not cfg.debug and not cfg.web.secret_key:
                ensure_secret_key(cfg.data_dir)
            watch = _config_watch(config_arg, hooks, cfg)
            specs = build_child_specs(
                cfg, caddy_binary=caddy.find_caddy(cfg), env=dict(os.environ), caddy_optional=True
            )
            if hooks.runner(il2ks_command(cfg, "web", "--migrate-only")) != 0:
                log.error("the database could not be prepared (see above); starting anyway")
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

_STATE_LABEL: dict[custom.State, str] = {
    "current": "up to date",
    "outdated": "OUT OF DATE",
    "newer": "NEWER",
    "unversioned": "NO VERSION",
    "orphan": "ORPHAN",
    "custom-only": "yours only",
    "unchecked": "unchecked",
}


def cmd_custom(cfg: Config, ns: argparse.Namespace) -> int:
    try:
        if ns.custom_command == "copy":
            placed = custom.copy_builtin(cfg, ns.path, force=ns.force)
            print(f"copied {placed.original} to {placed.override}")
            print("Edit that copy; restart il2ks to see the change. Keep its first line (the version): il2ks uses it")
            print("to warn you when an upgrade changes the page your copy is based on.")
        elif ns.custom_command == "accept":
            placed = custom.accept_original(cfg, ns.path)
            print(f"{placed.kind}/{placed.rel}: marked as based on the current built-in version")
        elif ns.custom_command == "diff":
            print(custom.diff_override(cfg, ns.path), end="")
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
    checks = custom.override_checks(cfg)
    for item in checks:
        versions = ""
        if item.builtin_version is not None:
            mine = f"v{item.override_version}" if item.override_version is not None else "unknown version"
            versions = f"  (yours: {mine}, built-in: v{item.builtin_version})"
        print(f"{_STATE_LABEL[item.state]:<12} {item.key}{versions}")
        print(f"{'':<13}{item.message}")
        if item.fix:
            print(f"{'':<13}{item.fix}")
    if not checks:
        print(f"no overrides yet. Copy a file with `il2ks custom copy <path>`; they live in {custom.custom_dir(cfg)}")
