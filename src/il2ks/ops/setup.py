"""`il2ks setup`: first-time setup a server admin can follow (FR-OPS-1, NFR-INS-1).

Asks a few questions with the answer in brackets (Enter keeps it), or takes every answer from options for scripts
(`non_interactive`). Then it writes `il2ks.toml` from the shipped example, creates the data folder, applies the database
migrations and creates the admin account. It refuses to overwrite an existing config unless `force`, and then saves the
old file next to it first. Everything after the config is written is safe to repeat.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tomllib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from il2ks.config import CONFIG_FILE, ConfigError, default_data_dir, detect_os_timezone, load_config, stored_server_uid
from il2ks.exitcodes import EXIT_FAILED, EXIT_LOCKED, EXIT_OK, EXIT_USAGE
from il2ks.ingest.lock import LockBusyError
from il2ks.ops import admin
from il2ks.ops.detect import LogFolder, default_roots, find_log_folders
from il2ks.ops.prompt import Prompter
from il2ks.ops.template import Key, fill_template, template_text, toml_string

type HttpsMode = Literal["caddy", "external"]
HTTPS_MODES: tuple[HttpsMode, ...] = get_args(HttpsMode.__value__)
DEFAULT_HTTPS_MODE: HttpsMode = "caddy"
_DOMAIN_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")


@dataclass(frozen=True, slots=True)
class SetupOptions:
    """Answers given up front (flags or environment); None = ask, or use the default when not interactive."""

    config_path: Path | None = None  # where to write il2ks.toml
    data_dir: Path | None = None
    logs_dir: Path | None = None
    timezone: str | None = None
    domain: str | None = None
    https_mode: HttpsMode | None = None
    email: str | None = None
    admin_username: str | None = None
    admin_password: str | None = None
    no_admin: bool = False
    non_interactive: bool = False
    force: bool = False


type LogFinder = Callable[[], list[LogFolder]]


def default_log_finder(env: Mapping[str, str], home: Path, platform: str) -> LogFinder:
    def find() -> list[LogFolder]:
        return find_log_folders(default_roots(platform, env, home))

    return find


def config_target(explicit: Path | None, env: Mapping[str, str]) -> Path:
    """Where `il2ks.toml` goes: `--config`, else `IL2KS_CONFIG`, else `il2ks.toml` in the current folder.

    The current folder is also the first place every other command looks, so a later `il2ks run` finds the file."""
    if explicit is not None:
        return explicit.resolve()
    if env.get("IL2KS_CONFIG"):
        return Path(env["IL2KS_CONFIG"]).expanduser().resolve()
    return (Path.cwd() / CONFIG_FILE).resolve()


def normalize_domain(text: str) -> str:
    """`https://Stats.Example.com/` -> `stats.example.com`; raises ValueError if it can't be a host name."""
    domain = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text.strip(), flags=re.IGNORECASE).split("/")[0].lower()
    if domain and _DOMAIN_RE.fullmatch(domain) is None:
        raise ValueError(f"{text!r} is not a domain name or IP address (no spaces, no port)")
    return domain


def _valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _saved_server_uid(path: Path) -> uuid.UUID | None:
    """The `[server] uid` of an existing config, so starting over keeps the server's identity (TD-17)."""
    try:
        raw = cast(dict[str, object], tomllib.loads(path.read_text(encoding="utf-8")))
        server = raw.get("server")
        value = cast(dict[str, object], server).get("uid") if isinstance(server, dict) else None
        return uuid.UUID(value) if isinstance(value, str) else None
    except (OSError, ValueError):
        return None


class _Setup:
    def __init__(
        self,
        opts: SetupOptions,
        io: Prompter,
        env: Mapping[str, str],
        platform: str,
        find_logs: LogFinder,
        now: Callable[[], datetime],
    ) -> None:
        self.opts = opts
        self.io = io
        self.env = env
        self.platform = platform
        self.find_logs = find_logs
        self.now = now
        self.interactive = not opts.non_interactive

    # --- questions ---------------------------------------------------------------------------------------------

    def ask_data_dir(self) -> Path:
        if self.opts.data_dir is not None:
            return self.opts.data_dir.expanduser().resolve()
        default = default_data_dir(self.env)
        if not self.interactive:
            return default.resolve()
        self.io.say()
        self.io.say("1. Data folder")
        self.io.say("   il2ks keeps its database, archived mission logs, backups and log files here.")
        while True:
            answer = Path(self.io.ask("   Data folder", str(default))).expanduser().resolve()
            if answer.is_file():
                self.io.say(f"   {answer} is a file, not a folder. Choose another.")
                continue
            return answer

    def ask_logs_dir(self) -> Path | None:
        if self.opts.logs_dir is not None:
            chosen = self.opts.logs_dir.expanduser().resolve()
            if not chosen.is_dir():
                self.io.say(f"Note: the log folder {chosen} does not exist (yet). il2ks doctor will remind you.")
            return chosen
        if not self.interactive:
            return None
        self.io.say()
        self.io.say("2. DServer's text log folder")
        self.io.say("   il2ks reads the missionReport(...)[N].txt files the game server writes there.")
        self.io.say("   Looking for it (a few seconds)...")
        found = self.find_logs()
        default = ""
        if found:
            self.io.say("   Found folders with mission reports:")
            for i, folder in enumerate(found, start=1):
                newest = datetime.fromtimestamp(folder.newest_mtime).strftime("%Y-%m-%d %H:%M")
                self.io.say(f"     {i}) {folder.path}  ({folder.reports} files, newest {newest})")
            default = "1"
        else:
            self.io.say("   Nothing found. The server may not write text logs yet (see il2ks doctor afterwards).")
        prompt = "   Number from the list, or the folder's path; Enter" + ("" if found else " to skip")
        while True:
            answer = self.io.ask(prompt, default)
            if not answer:
                self.io.say("   Skipped. Set [logs] dir in il2ks.toml later.")
                return None
            if answer.isdigit() and 1 <= int(answer) <= len(found):
                return found[int(answer) - 1].path.resolve()
            path = Path(answer.strip('"')).expanduser().resolve()
            if path.is_dir() or self.io.confirm(f"   {path} does not exist. Use it anyway?", False):
                return path

    def ask_timezone(self) -> str:
        detected = detect_os_timezone(self.env)
        if self.opts.timezone is not None:
            if not _valid_timezone(self.opts.timezone):
                raise ValueError(f"unknown timezone {self.opts.timezone!r}: use an IANA name such as Europe/Berlin")
            return self.opts.timezone
        if not self.interactive:
            guess = "assumed" if self.platform == "win32" else "detected"
            self.io.say(f"Time zone: {detected} ({guess}). Use --timezone NAME if the game server's computer differs.")
            return detected
        self.io.say()
        self.io.say("3. Time zone of the game server's computer")
        self.io.say("   DServer names its logs in that computer's local time, so il2ks must know it.")
        self.io.say('   Use an IANA name such as "Europe/Berlin", "Asia/Seoul" or "America/New_York", or "UTC".')
        if self.platform == "win32":
            self.io.say("   (Windows cannot tell il2ks the name, so please type it.)")
        while True:
            answer = self.io.ask("   Time zone", detected)
            if _valid_timezone(answer):
                return answer
            self.io.say(f"   {answer!r} is not a known time zone name. Try again.")

    def ask_https(self) -> tuple[HttpsMode, str, str]:
        """(mode, domain, email)."""
        o = self.opts
        if not self.interactive:
            return (
                o.https_mode or DEFAULT_HTTPS_MODE,
                normalize_domain(o.domain or ""),
                (o.email or "").strip(),
            )
        self.io.say()
        self.io.say("4. Website address and HTTPS")
        self.io.say("   The site is served over HTTPS only. Players need an address that points to this computer.")
        domain = self._ask_domain()
        mode = o.https_mode
        if mode is None:
            self.io.say("   How should HTTPS be handled?")
            self.io.say("     1) il2ks does it with its bundled Caddy (recommended; gets the certificate by itself)")
            self.io.say("     2) I already run my own web server (nginx, IIS, ...) in front of il2ks")
            mode = "external" if self.io.ask("   Choose 1 or 2", "1") == "2" else "caddy"
        email = o.email or ""
        if mode == "caddy" and domain and o.email is None:
            email = self.io.ask("   E-mail for certificate notices (optional, Enter to skip)", "")
        return mode, domain, email.strip()

    def _ask_domain(self) -> str:
        if self.opts.domain is not None:
            return normalize_domain(self.opts.domain)
        while True:
            answer = self.io.ask("   Domain name (for example stats.example.com), Enter if you have none yet", "")
            try:
                return normalize_domain(answer)
            except ValueError as exc:
                self.io.say(f"   {exc}")

    # --- doing ---------------------------------------------------------------------------------------------------

    def write_config(self, target: Path, values: dict[Key, str]) -> Path | None:
        """Write the filled template; returns the backup of the config it replaced, if any."""
        text = fill_template(template_text(), values)
        backup: Path | None = None
        if target.exists():
            stamp = self.now().strftime("%Y%m%d-%H%M%S")
            backup = target.with_name(f"{target.name}.bak-{stamp}")
            shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".tmp")
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, target)
        return backup

    def create_admin(self, username_default: str) -> bool:
        """Create or reset the admin account and say what happened. False if it was wanted but failed."""
        o = self.opts
        if o.no_admin:
            self.io.say("Admin account: skipped (--no-admin). Create one later with: il2ks createadmin")
            return True
        username = o.admin_username or username_default
        try:
            if self.interactive:
                self.io.say(self._create_admin_interactively(username))
            elif o.admin_password is None:
                self.io.say(
                    "Admin account: not created (no password given: set IL2KS_ADMIN_PASSWORD or "
                    "--admin-password-file). Create one with: il2ks createadmin"
                )
            else:
                self.io.say(f"Admin account {username!r}: {admin.save_admin(username, o.admin_password)}")
        except admin.AdminError as exc:
            self.io.say(f"Admin account: NOT created ({exc}). Create one with: il2ks createadmin")
            return False
        return True

    def _create_admin_interactively(self, username_default: str) -> str:
        io = self.io
        io.say()
        io.say("5. Admin account")
        io.say("   The admin account opens the admin pages (branding, hiding players, ingestion history).")
        if admin.admin_exists() and not io.confirm(
            "   An admin account already exists. Create or reset another?", False
        ):
            return "Admin account: kept the existing one."
        if not io.confirm("   Create the admin account now?", True):
            return "Admin account: skipped. Create one with: il2ks createadmin"
        username = self.opts.admin_username or io.ask("   User name", username_default)
        password = self.opts.admin_password or admin.prompt_password(io, username)
        return f"Admin account {username!r}: {admin.save_admin(username, password)}"


def run_setup(
    opts: SetupOptions,
    io: Prompter,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    find_logs: LogFinder | None = None,
    home: Path | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> int:
    """Run the whole setup; returns the exit code (0 done, 2 refused or invalid answer, 3 database busy)."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    finder = find_logs or default_log_finder(env, home or Path.home(), platform)
    s = _Setup(opts, io, env, platform, finder, now)
    target = config_target(opts.config_path, env)

    if target.exists() and not opts.force:
        io.say(f"il2ks is already set up: {target} exists. Nothing was changed.")
        io.say("  To check the setup:          il2ks doctor")
        io.say("  To add or reset an admin:    il2ks createadmin")
        io.say("  To start over (the old file is saved next to it): il2ks setup --force")
        return EXIT_USAGE

    io.say("il2ks setup")
    io.say("Answer each question, or press Enter to accept the answer in [brackets].")
    try:
        data_dir = s.ask_data_dir()
        logs_dir = s.ask_logs_dir()
        timezone = s.ask_timezone()
        mode, domain, email = s.ask_https()
    except ValueError as exc:
        io.say(f"il2ks setup: {exc}")
        return EXIT_USAGE

    uid = (_saved_server_uid(target) if target.exists() else None) or stored_server_uid(data_dir, create=True)
    values: dict[Key, str] = {
        ("", "data_dir"): toml_string(data_dir),
        ("server", "timezone"): toml_string(timezone),
        ("server", "uid"): toml_string(str(uid)),
        ("https", "mode"): toml_string(mode),
    }
    if logs_dir is not None:
        values[("logs", "dir")] = toml_string(logs_dir)
    if domain:
        values[("https", "domain")] = toml_string(domain)
    if email:
        values[("https", "email")] = toml_string(email)

    old = s.write_config(target, values)
    try:
        cfg = load_config(target, env)
    except ConfigError as exc:  # can only be an environment override that clashes; the file itself is valid
        io.say(f"il2ks setup: the new configuration does not load: {exc}")
        return EXIT_USAGE
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    io.say()
    io.say(f"Wrote {target}" + (f" (the previous file is saved as {old})" if old is not None else ""))
    chosen: dict[str, str | None] = {
        "IL2KS_DATA_DIR": str(data_dir),
        "IL2KS_LOGS_DIR": None if logs_dir is None else str(logs_dir),
        "IL2KS_SERVER_TIMEZONE": timezone,
    }
    for name, value in chosen.items():
        if env.get(name) and value is not None and env[name] != value:
            io.say(f"Warning: the environment variable {name} is set and overrides this choice. Unset it.")

    from il2ks.ops import migrate

    os.environ["IL2KS_DATA_DIR"] = str(cfg.data_dir)  # settings.py reads the data folder from here
    if not cfg.web.secret_key:
        from il2ks.serving.secret import ensure_secret_key

        ensure_secret_key(cfg.data_dir)  # NFR-SEC-2: generated once, private to this install
    migrate.django_setup()
    try:
        migrate.migrate_if_needed(cfg, "setup", wait=30.0)
    except LockBusyError as exc:
        io.say(f"il2ks setup: {exc}")
        io.say("The configuration was written. Stop the other il2ks process and run: il2ks createadmin")
        return EXIT_LOCKED
    io.say("Database ready.")
    admin_ok = s.create_admin(admin.DEFAULT_USERNAME)
    _next_steps(io, target, cfg.data_dir, logs_dir, env)
    return EXIT_OK if admin_ok else EXIT_FAILED


def _next_steps(io: Prompter, target: Path, data_dir: Path, logs_dir: Path | None, env: Mapping[str, str]) -> None:
    io.say()
    io.say("Setup is done. Next steps:")
    step = 1
    if logs_dir is None:
        io.say(f"  {step}. Set [logs] dir in {target} to DServer's text log folder.")
        step += 1
    io.say(f"  {step}. Check everything:            il2ks doctor")
    io.say(f"  {step + 1}. Start the site and logs reader: il2ks run   (Ctrl+C stops it)")
    io.say(f"  {step + 2}. Start automatically after a reboot: see il2ks service --help")
    io.say(f"Your data (database, archive, backups) is in {data_dir}. Back it up now and then: il2ks backup")
    found = (Path.cwd() / CONFIG_FILE).resolve() == target or (
        env.get("IL2KS_CONFIG") and Path(env["IL2KS_CONFIG"]).expanduser().resolve() == target
    )
    if not found:
        io.say(
            f"il2ks finds its configuration when you run it in {target.parent}, or with: il2ks --config {target} ..."
        )
