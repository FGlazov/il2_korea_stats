"""The handlers of `il2ks setup`, `createadmin`, `doctor`, `backup` and `restore` (FR-OPS-1, FR-OPS-6).

`cli.py` parses the arguments and calls these; each returns the exit code. Prompts and file locations come from the
arguments and the environment, nothing is read at import time.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from il2ks.config import CONFIG_FILE, Config, ConfigError, load_config
from il2ks.exitcodes import EXIT_FAILED, EXIT_LOCKED, EXIT_OK, EXIT_USAGE
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ops import admin, backup, migrate, report
from il2ks.ops.checks import raw_config
from il2ks.ops.doctor import Finding, Level, run_checks
from il2ks.ops.prompt import Cancelled, ConsolePrompter, Prompter, interactive_terminal
from il2ks.ops.setup import HTTPS_MODES, SetupOptions, run_setup
from il2ks.serving.setup_token import discard_token

DEFAULT_WEB_PORT = 8000


def _say_error(command: str, message: str) -> None:
    print(f"il2ks {command}: {message}", file=sys.stderr)


def _path(value: str | Path | None) -> Path | None:
    return Path(value) if value else None


def _load(ns: argparse.Namespace, command: str) -> Config | None:
    """The config without side effects (no server UID file is created); prints the problem and returns None."""
    try:
        return load_config(ns.config, create_server_uid=False)
    except ConfigError as exc:
        _say_error(command, f"configuration error: {exc}")
        return None


def _prepare_django(cfg: Config) -> None:
    os.environ["IL2KS_DATA_DIR"] = str(cfg.data_dir)  # settings.py reads the data folder from here
    migrate.django_setup()


# --- setup -----------------------------------------------------------------------------------------------------------


def setup_options(ns: argparse.Namespace, env: Mapping[str, str]) -> SetupOptions:
    """Flags win over the environment (`IL2KS_DATA_DIR`, `IL2KS_LOGS_DIR`, `IL2KS_SERVER_TIMEZONE`,
    `IL2KS_HTTPS_MODE`, `IL2KS_HTTPS_DOMAIN`, `IL2KS_HTTPS_EMAIL`, `IL2KS_ADMIN_USERNAME`, `IL2KS_ADMIN_PASSWORD`)."""
    mode = ns.https or env.get("IL2KS_HTTPS_MODE") or None
    if mode is not None and mode not in HTTPS_MODES:
        raise ValueError(f"--https must be one of {', '.join(HTTPS_MODES)}, got {mode!r}")
    domain = ns.domain if ns.domain is not None else env.get("IL2KS_HTTPS_DOMAIN")
    return SetupOptions(
        config_path=ns.config,
        data_dir=_path(ns.data_dir or env.get("IL2KS_DATA_DIR")),
        logs_dir=_path(ns.logs_dir or env.get("IL2KS_LOGS_DIR")),
        timezone=ns.timezone or env.get("IL2KS_SERVER_TIMEZONE") or None,
        domain=domain,
        https_mode=mode,
        email=ns.email if ns.email is not None else env.get("IL2KS_HTTPS_EMAIL"),
        admin_username=ns.admin_username or env.get("IL2KS_ADMIN_USERNAME") or None,
        admin_password=admin.read_password(env, ns.admin_password_file),
        no_admin=ns.no_admin,
        non_interactive=ns.non_interactive or not interactive_terminal(),
        force=ns.force,
    )


def cmd_setup(ns: argparse.Namespace, env: Mapping[str, str] | None = None, io: Prompter | None = None) -> int:
    env = os.environ if env is None else env
    try:
        opts = setup_options(ns, env)
    except (ValueError, admin.AdminError) as exc:
        _say_error("setup", str(exc))
        return EXIT_USAGE
    try:
        return run_setup(opts, io or ConsolePrompter(), env=env)
    except (Cancelled, KeyboardInterrupt):
        print("\nil2ks setup: cancelled. Run it again whenever you like.", file=sys.stderr)
        return EXIT_FAILED


# --- createadmin -----------------------------------------------------------------------------------------------------


def cmd_createadmin(ns: argparse.Namespace, env: Mapping[str, str] | None = None, io: Prompter | None = None) -> int:
    env = os.environ if env is None else env
    io = io or ConsolePrompter()
    interactive = interactive_terminal()
    if ns.username is None and not interactive:
        _say_error("createadmin", "--username is required when not run in a terminal")
        return EXIT_USAGE
    try:
        password = admin.read_password(env, ns.password_file)
    except admin.AdminError as exc:
        _say_error("createadmin", str(exc))
        return EXIT_USAGE
    if password is None and not interactive:
        _say_error("createadmin", f"no password: set {admin.PASSWORD_ENV} or use --password-file")
        return EXIT_USAGE
    cfg = _load(ns, "createadmin")
    if cfg is None:
        return EXIT_USAGE
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    _prepare_django(cfg)
    try:
        migrate.migrate_if_needed(cfg, "createadmin", wait=ns.wait)
        if ns.if_none and admin.admin_exists():
            print("An admin account already exists: nothing changed.")  # the Docker entrypoint relies on this
            return EXIT_OK
        username = ns.username or io.ask("User name", admin.DEFAULT_USERNAME)
        question = f"The account {username!r} exists. Reset its password and make it an admin?"
        if interactive and admin.user_exists(username) and not io.confirm(question, False):
            io.say("Nothing changed.")
            return EXIT_OK
        if password is None:
            password = admin.prompt_password(io, username, ns.email or "")
        outcome = admin.save_admin(username, password, ns.email or "")
    except LockBusyError as exc:
        _say_error("createadmin", str(exc))
        return EXIT_LOCKED
    except admin.AdminError as exc:
        _say_error("createadmin", str(exc))
        return EXIT_USAGE
    except (Cancelled, KeyboardInterrupt):
        print("\nil2ks createadmin: cancelled.", file=sys.stderr)
        return EXIT_FAILED
    discard_token(cfg.data_dir)  # an admin exists: the browser setup page is closed for good
    print(f"Admin account {username!r}: {outcome}. Log in at /admin/ on your site.")
    return EXIT_OK


# --- doctor ----------------------------------------------------------------------------------------------------------


def gather_findings(config_path: Path | None) -> list[Finding]:
    """The config findings, then every registered check (if the config loads)."""
    try:
        cfg = load_config(config_path, create_server_uid=False)
    except ConfigError as exc:
        return [
            Finding(
                Level.ERROR,
                "Configuration file is invalid",
                str(exc),
                "Fix the setting named above in il2ks.toml, or run il2ks setup --force to start over "
                "(the old file is kept).",
            )
        ]
    if cfg.source is None:
        findings = [
            Finding(
                Level.WARN,
                "No il2ks.toml found: using the built-in defaults",
                "Looked for --config, IL2KS_CONFIG, ./il2ks.toml and <data dir>/il2ks.toml.",
                "Run il2ks setup, or run il2ks from the folder that holds your il2ks.toml, or pass --config.",
            )
        ]
    else:
        findings = [Finding(Level.OK, "Configuration file found and valid", str(cfg.source))]
    _prepare_django(cfg)
    return findings + run_checks(cfg)


def cmd_doctor(ns: argparse.Namespace) -> int:
    findings = gather_findings(ns.config)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(errors="replace")  # a path with letters the console can't show must not crash the report
    if ns.json:
        print(report.render_json(findings))
    else:
        print(report.render_text(findings, color=report.use_color(sys.stdout)))
    return report.exit_code(findings)


# --- backup and restore ----------------------------------------------------------------------------------------------


def cmd_backup(ns: argparse.Namespace) -> int:
    cfg = _load(ns, "backup")
    if cfg is None:
        return EXIT_USAGE
    try:
        path = backup.create_backup(cfg, "manual")
    except (backup.BackupError, OSError) as exc:
        _say_error("backup", str(exc))
        return EXIT_USAGE
    print(f"Backup written: {path} ({path.stat().st_size / (1 << 20):.1f} MB)")
    print(f"Kept: the newest {cfg.backup.keep} backups in {cfg.backup_dir}.")
    print("Contains the database, il2ks.toml, custom/, media/ and the server ID. The archived mission logs are NOT")
    print(f"included ({cfg.archive_dir}): copy that folder yourself if you want it elsewhere. Keep a copy of the")
    print("backup on another disk too.")
    return EXIT_OK


def web_port(cfg: Config, env: Mapping[str, str]) -> int:
    """The port the website listens on: `IL2KS_WEB_PORT`, else `[web] port` in the config file, else 8000."""
    raw: object = env.get("IL2KS_WEB_PORT")
    if raw is None:
        table = raw_config(cfg).get("web")
        raw = cast(dict[str, object], table).get("port") if isinstance(table, dict) else None
    try:
        return int(str(raw)) if raw is not None else DEFAULT_WEB_PORT
    except ValueError:
        return DEFAULT_WEB_PORT


def cmd_restore(ns: argparse.Namespace, env: Mapping[str, str] | None = None, io: Prompter | None = None) -> int:
    env = os.environ if env is None else env
    io = io or ConsolePrompter()
    cfg = _load(ns, "restore")
    if cfg is None:
        return EXIT_USAGE
    if os.environ.get("IL2KS_TEST_DB") == "postgres":
        _say_error("restore", "restore works with the SQLite database only (Postgres is for development)")
        return EXIT_USAGE
    zip_path: Path = ns.zip
    try:
        manifest = backup.read_manifest(zip_path)
        with WriterLock(cfg.data_dir, "restore"):
            pass  # refuse before asking anything while another writer (watch, ingest, reprocess) is running
    except backup.BackupError as exc:
        _say_error("restore", str(exc))
        return EXIT_USAGE
    except LockBusyError as exc:
        _say_error("restore", f"{exc}\nStop it first (for example il2ks run / il2ks watch), then restore.")
        return EXIT_LOCKED
    io.say(f"Backup: {zip_path}")
    io.say(f"  made {manifest.created_at} by il2ks {manifest.il2ks_version} ({manifest.reason})")
    io.say(f"Restoring replaces the database, config, custom/ and media/ in {cfg.data_dir}.")
    port = web_port(cfg, env)
    if backup.web_probably_running(port):
        io.say(f"WARNING: something is answering on port {port}: the website is probably running. Stop it first.")
    io.say("The current state is backed up first, so this can be undone.")
    if not ns.yes:
        if not interactive_terminal():
            _say_error("restore", "not in a terminal: add --yes to confirm")
            return EXIT_USAGE
        try:
            if not io.confirm("Restore this backup now?", False):
                io.say("Nothing changed.")
                return EXIT_OK
        except (Cancelled, KeyboardInterrupt):
            return EXIT_FAILED
    target = cfg.source or Path.cwd() / CONFIG_FILE
    try:
        result = backup.restore_backup(cfg, zip_path, target)
    except LockBusyError as exc:
        _say_error("restore", str(exc))
        return EXIT_LOCKED
    except (backup.BackupError, OSError) as exc:
        _say_error("restore", str(exc))
        return EXIT_USAGE
    io.say("Restored and verified.")
    if result.safety_backup is not None:
        io.say(f"The state before the restore is saved as {result.safety_backup}.")
    if result.config_written is not None:
        io.say(f"Config written to {result.config_written}.")
    if result.restored_data_dir_differs:
        io.say("Note: the restored config names another data folder than the one restored into. Check data_dir.")
    io.say("Start il2ks again. If the backup is from an older il2ks, the database is updated automatically.")
    return EXIT_OK
