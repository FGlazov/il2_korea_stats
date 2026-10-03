"""The `il2ks.toml` configuration (TD-11, FR-OPS-2).

Loaded once at startup by the CLI and passed in explicitly. The one module that loads it itself is `settings.py`:
Django reads that by name, so it finds the same file through the environment (the CLI exports `IL2KS_CONFIG` and
`IL2KS_DATA_DIR` for it and for child processes).

Sources, later ones win: built-in defaults, the TOML file, `IL2KS_*` environment variables. Every setting has one env
name: `IL2KS_<SECTION>_<KEY>` in upper case (`[logs] dir` -> `IL2KS_LOGS_DIR`); top-level keys drop the section
(`data_dir` -> `IL2KS_DATA_DIR`). Lists in env vars are comma-separated.

Which file: `--config`, else `IL2KS_CONFIG`, else `./il2ks.toml`, else `<data dir>/il2ks.toml`
(data dir from `IL2KS_DATA_DIR` or
the default). No file at all means defaults plus env. Relative paths in the file resolve against the file's folder.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import os
import re
import tomllib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from il2ks.core.ratings.elo import RatingRules
from il2ks.core.replay.config import ReplayRules

type AfterArchive = Literal["move", "keep", "delete"]
AFTER_ARCHIVE_VALUES: tuple[AfterArchive, ...] = ("move", "keep", "delete")
type HttpsMode = Literal["caddy", "external"]
HTTPS_MODES: tuple[HttpsMode, ...] = ("caddy", "external")
type CertSource = Literal["auto", "internal"]
CERT_SOURCES: tuple[CertSource, ...] = ("auto", "internal")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
SERVER_UID_FILE = "server_uid.txt"
CONFIG_FILE = "il2ks.toml"
DB_FILE = "il2ks.sqlite3"  # `settings.py` names the SQLite file the same way
DEFAULT_LOG_KEEP_DAYS = 14


class ConfigError(ValueError):
    """The configuration is invalid. The message says which setting and why."""


def default_data_dir(env: Mapping[str, str]) -> Path:
    """Same default as `settings.py`: `IL2KS_DATA_DIR`, else `./.il2ks-data`."""
    raw = env.get("IL2KS_DATA_DIR")
    return Path(raw) if raw else Path.cwd() / ".il2ks-data"


@dataclass(frozen=True, slots=True)
class LogsConfig:
    """Where DServer writes its text logs and what happens to them after archiving (FR-ING-1, FR-ING-10, FR-ING-16)."""

    dir: Path | None = None  # None: not configured; `il2ks ingest` (without --from) refuses to run
    after_archive: AfterArchive = "move"
    move_to: Path | None = None  # None: `<data dir>/ingested-logs`
    remote: bool = False  # FR-ING-16: logs are copied in from another machine


@dataclass(frozen=True, slots=True)
class IngestConfig:
    """Completeness, polling and retry settings (FR-ING-2, FR-ING-16, FR-ING-19)."""

    idle_minutes: float = 10.0  # complete if no part was written for this long
    # After AType 7: wait until no part changed for this long (cleanup lines follow AType 7). 300 s, not 60: a newer
    # mission's [0] file completes a mission anyway, so a long settle only delays the last mission before a pause.
    settle_seconds: float = 300.0
    stable_seconds: float = 60.0  # remote mode: a part counts as fully copied once unmodified this long
    watch_interval_s: float = 30.0
    retry_backoff_minutes: tuple[float, ...] = (5.0, 30.0, 120.0)  # then stop (FR-ING-19)

    @property
    def retry_backoff(self) -> tuple[timedelta, ...]:
        return tuple(timedelta(minutes=m) for m in self.retry_backoff_minutes)


@dataclass(frozen=True, slots=True)
class BackupConfig:
    """Backups of the admin state (FR-OPS-6): how many to keep and whether `watch` makes one every day."""

    keep: int = 10
    daily: bool = True


@dataclass(frozen=True, slots=True)
class WebConfig:
    """`il2ks web`: the granian server behind the HTTPS proxy (TD-10, NFR-INS-5)."""

    host: str = "127.0.0.1"  # localhost only: the proxy is the public face (TD-10)
    port: int = 8000
    workers: int = 1  # worker processes; Windows supports only 1 (granian), one is plenty for a small box
    threads: int = 4  # request threads per worker
    allowed_hosts: tuple[str, ...] = ()  # extra Host names Django accepts besides [https] domain and localhost
    secret_key: str = field(default="", repr=False)  # "" = generated into <data dir>/secret_key.txt (NFR-SEC-2)


@dataclass(frozen=True, slots=True)
class HttpsConfig:
    """HTTPS in front of the web server (TD-23, NFR-SEC-6)."""

    mode: HttpsMode = "caddy"  # "external" = the admin's own nginx/IIS terminates TLS
    domain: str = ""  # host name or IP address the site is reached at; "" = not set
    email: str = ""  # contact address for the certificate authority (expiry notices); optional
    cert: CertSource = "auto"  # "internal" = Caddy's own CA (browser warnings): testing only
    caddy_path: Path | None = None  # None: look on PATH, then <data dir>/bin
    http_port: int = 80
    https_port: int = 443
    hsts_seconds: int = 86400  # Strict-Transport-Security max-age; 0 = off. Raise it once HTTPS works (TD-23)


@dataclass(frozen=True, slots=True)
class Config:
    data_dir: Path
    server_uid: uuid.UUID
    timezone_name: str
    log_level: str = "INFO"
    log_keep_days: int = DEFAULT_LOG_KEEP_DAYS  # daily log files of each process are kept this many days (TD-27)
    debug: bool = False  # developer switch: Django DEBUG, plain-http cookies, dev secret key. Never on a public site
    web: WebConfig = field(default_factory=WebConfig)
    https: HttpsConfig = field(default_factory=HttpsConfig)
    logs: LogsConfig = field(default_factory=LogsConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    replay: ReplayRules = field(default_factory=ReplayRules)
    ratings: RatingRules = field(default_factory=RatingRules)
    backup: BackupConfig = field(default_factory=BackupConfig)
    source: Path | None = None  # the TOML file that was read, if any

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def db_path(self) -> Path:
        """The SQLite database file (the only user-facing database, TD-04)."""
        return self.data_dir / DB_FILE

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def move_to(self) -> Path:
        return self.logs.move_to or self.data_dir / "ingested-logs"

    @property
    def log_dir(self) -> Path:
        """Where the processes write their own rotating log files (TD-27)."""
        return self.data_dir / "logs"


# --- loading -----------------------------------------------------------------------------------------------------

type Raw = Mapping[str, object]


def find_config_file(explicit: Path | None, env: Mapping[str, str]) -> Path | None:
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigError(f"config file not found: {explicit}")
        return explicit
    if env.get("IL2KS_CONFIG"):
        path = Path(env["IL2KS_CONFIG"])
        if not path.is_file():
            raise ConfigError(f"IL2KS_CONFIG points to a missing file: {path}")
        return path
    for candidate in (Path.cwd() / CONFIG_FILE, default_data_dir(env) / CONFIG_FILE):
        if candidate.is_file():
            return candidate
    return None


def load_config(
    path: Path | None = None, env: Mapping[str, str] | None = None, *, create_server_uid: bool = True
) -> Config:
    """Read the config. `env` defaults to `os.environ` (tests pass their own).

    `create_server_uid`: when no server UID is configured, generate one into `<data dir>/server_uid.txt` (TD-17)."""
    env = os.environ if env is None else env
    file = find_config_file(path, env)
    raw: Raw = {}
    base = Path.cwd()
    if file is not None:
        try:
            raw = tomllib.loads(file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{file}: {exc}") from exc
        base = file.resolve().parent
    reader = _Reader(raw, env, base)

    data_dir = reader.path("", "data_dir") or default_data_dir(env)
    log_level = reader.str_("", "log_level", "INFO").upper()
    if log_level not in LOG_LEVELS:
        raise ConfigError(f"log_level must be one of {', '.join(LOG_LEVELS)}, got {log_level!r}")

    keep_days = reader.positive_int("", "log_keep_days", DEFAULT_LOG_KEEP_DAYS)
    debug = reader.bool_("", "debug", False)
    web = _load_web(reader)
    https = _load_https(reader)

    after = reader.str_("logs", "after_archive", "move")
    if after not in AFTER_ARCHIVE_VALUES:
        raise ConfigError(f"logs.after_archive must be one of {', '.join(AFTER_ARCHIVE_VALUES)}, got {after!r}")
    logs = LogsConfig(
        dir=reader.path("logs", "dir"),
        after_archive=after,
        move_to=reader.path("logs", "move_to"),
        remote=reader.bool_("logs", "remote", False),
    )

    defaults = IngestConfig()
    ingest = IngestConfig(
        idle_minutes=reader.positive("ingest", "idle_minutes", defaults.idle_minutes),
        settle_seconds=reader.non_negative("ingest", "settle_seconds", defaults.settle_seconds),
        stable_seconds=reader.non_negative("ingest", "stable_seconds", defaults.stable_seconds),
        watch_interval_s=reader.positive("ingest", "watch_interval_s", defaults.watch_interval_s),
        retry_backoff_minutes=reader.float_list("ingest", "retry_backoff_minutes", defaults.retry_backoff_minutes),
    )

    rule_values: dict[str, float] = {}
    for rule in dataclasses.fields(ReplayRules):
        if rule.name != "resupply_allowed":  # the one yes/no rule; every other rule is a threshold
            rule_values[rule.name] = reader.non_negative("replay", rule.name, cast(float, rule.default))
    resupply_allowed = reader.bool_("replay", "resupply_allowed", ReplayRules().resupply_allowed)
    replay = ReplayRules(resupply_allowed=resupply_allowed, **rule_values)

    rating_defaults = RatingRules()
    ratings = RatingRules(
        start=reader.non_negative("ratings", "start", rating_defaults.start),
        k=reader.non_negative("ratings", "k", rating_defaults.k),
        cross_pool_weight=reader.non_negative("ratings", "cross_pool_weight", rating_defaults.cross_pool_weight),
    )

    backup_defaults = BackupConfig()
    backup = BackupConfig(
        keep=reader.positive_int("backup", "keep", backup_defaults.keep),
        daily=reader.bool_("backup", "daily", backup_defaults.daily),
    )

    configured_tz = reader.str_("server", "timezone", "")
    tz_name = configured_tz or detect_os_timezone(env)
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        origin = "server.timezone" if configured_tz else "the OS timezone (TZ or /etc/localtime); set [server] timezone"
        raise ConfigError(f"{origin}: unknown IANA timezone {tz_name!r}") from exc

    uid_text = reader.str_("server", "uid", "")
    server_uid = _parse_uid(uid_text) if uid_text else stored_server_uid(data_dir, create=create_server_uid)

    return Config(
        data_dir=data_dir,
        server_uid=server_uid,
        timezone_name=tz_name,
        log_level=log_level,
        log_keep_days=keep_days,
        debug=debug,
        web=web,
        https=https,
        logs=logs,
        ingest=ingest,
        replay=replay,
        ratings=ratings,
        backup=backup,
        source=file,
    )


def _load_web(reader: _Reader) -> WebConfig:
    defaults = WebConfig()
    host = reader.str_("web", "host", defaults.host).strip()
    if not host:
        raise ConfigError("web.host must not be empty")
    return WebConfig(
        host=host,
        port=reader.port("web", "port", defaults.port),
        workers=reader.positive_int("web", "workers", defaults.workers),
        threads=reader.positive_int("web", "threads", defaults.threads),
        allowed_hosts=reader.str_list("web", "allowed_hosts", defaults.allowed_hosts),
        secret_key=reader.str_("web", "secret_key", "").strip(),
    )


_DNS_NAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?")
_EMAIL = re.compile(r"""[^\s@{}"'`#\\]+@[^\s@{}"'`#\\]+""")


def normalize_domain(text: str) -> str:
    """The `[https] domain` value cleaned up: no scheme, path, port or IPv6 brackets. Raises ValueError with advice."""
    domain = text.strip()
    if not domain:
        return ""
    if "://" in domain or "/" in domain or any(c.isspace() for c in domain):
        raise ValueError("write only the name or IP address, like stats.example.com (no https:// and no path)")
    if domain.startswith("[") and domain.endswith("]"):
        domain = domain[1:-1]
    elif domain.count(":") == 1:
        raise ValueError("write the name without a port; ports go in [https] https_port")
    domain = domain.lower()
    if not _is_ip_address(domain) and _DNS_NAME.fullmatch(domain) is None:
        raise ValueError(
            f"{text.strip()!r} is not a host name or IP address: use letters, digits, dots and hyphens, "
            "like stats.example.com"
        )
    return domain


def _is_ip_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def normalize_email(text: str) -> str:
    """The `[https] email` value, checked: it ends up in the Caddyfile. Raises ValueError with advice."""
    email = text.strip()
    if email and _EMAIL.fullmatch(email) is None:
        raise ValueError(f"{email!r} is not an email address (no spaces, quotes, braces or # allowed)")
    return email


def _load_https(reader: _Reader) -> HttpsConfig:
    defaults = HttpsConfig()
    mode = reader.str_("https", "mode", defaults.mode)
    if mode not in HTTPS_MODES:
        raise ConfigError(f"https.mode must be one of {', '.join(HTTPS_MODES)}, got {mode!r}")
    cert = reader.str_("https", "cert", defaults.cert)
    if cert not in CERT_SOURCES:
        raise ConfigError(f"https.cert must be one of {', '.join(CERT_SOURCES)}, got {cert!r}")
    try:
        domain = normalize_domain(reader.str_("https", "domain", ""))
    except ValueError as exc:
        raise ConfigError(f"https.domain: {exc}") from exc
    try:
        email = normalize_email(reader.str_("https", "email", ""))
    except ValueError as exc:
        raise ConfigError(f"https.email: {exc}") from exc
    return HttpsConfig(
        mode=mode,
        domain=domain,
        email=email,
        cert=cert,
        caddy_path=reader.path("https", "caddy_path"),
        http_port=reader.port("https", "http_port", defaults.http_port),
        https_port=reader.port("https", "https_port", defaults.https_port),
        hsts_seconds=reader.whole_number("https", "hsts_seconds", defaults.hsts_seconds),
    )


def _parse_uid(text: str) -> uuid.UUID:
    try:
        return uuid.UUID(text)
    except ValueError as exc:
        raise ConfigError(f"server.uid is not a UUID: {text!r}") from exc


def stored_server_uid(data_dir: Path, *, create: bool) -> uuid.UUID:
    """The server UID from `<data dir>/server_uid.txt`, generated on first use (TD-17).

    `il2ks setup` will write it into `il2ks.toml` as `[server] uid`; until then this file keeps it stable."""
    path = data_dir / SERVER_UID_FILE
    if path.is_file():
        return _parse_uid(path.read_text(encoding="utf-8").strip())
    new = uuid.uuid4()
    if create:
        data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{new}\n", encoding="utf-8")
    return new


def detect_os_timezone(env: Mapping[str, str]) -> str:
    """The OS timezone as an IANA name when it can be found with the stdlib, else "UTC" (TD-15).

    `TZ` env var, then the `/etc/localtime` symlink (Linux). Windows has no IANA name without extra packages,
    so Windows admins set `[server] timezone` (or run DServer on UTC, as doc 07 recommends)."""
    tz = env.get("TZ", "").lstrip(":")
    if tz:
        return tz
    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        target = str(localtime.resolve())
        marker = "zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    return "UTC"


class _Reader:
    """Typed access to `raw[section][key]` with the matching `IL2KS_*` env override."""

    def __init__(self, raw: Raw, env: Mapping[str, str], base: Path) -> None:
        self._raw = raw
        self._env = env
        self._base = base

    @staticmethod
    def env_name(section: str, key: str) -> str:
        return "IL2KS_" + (f"{section}_{key}" if section else key).upper()

    def _get(self, section: str, key: str) -> tuple[object, bool]:
        """(value, from_env). Missing -> (None, False)."""
        env_value = self._env.get(self.env_name(section, key))
        if env_value is not None:
            return env_value, True
        table: object = self._raw if not section else self._raw.get(section, {})
        if not isinstance(table, Mapping):
            raise ConfigError(f"[{section}] must be a table")
        return cast(Mapping[str, object], table).get(key), False

    @staticmethod
    def _label(section: str, key: str) -> str:
        return f"{section}.{key}" if section else key

    def str_(self, section: str, key: str, default: str) -> str:
        value, _ = self._get(section, key)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ConfigError(f"{self._label(section, key)} must be a string")
        return value

    def path(self, section: str, key: str) -> Path | None:
        value, from_env = self._get(section, key)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ConfigError(f"{self._label(section, key)} must be a path string")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() if from_env else self._base) / path
        return path

    def bool_(self, section: str, key: str, default: bool) -> bool:
        value, _ = self._get(section, key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            return value.lower() in {"1", "true", "yes", "on"}
        raise ConfigError(f"{self._label(section, key)} must be true or false")

    def _float(self, label: str, value: object) -> float:
        if isinstance(value, bool):
            raise ConfigError(f"{label} must be a number")
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
        raise ConfigError(f"{label} must be a number")

    def non_negative(self, section: str, key: str, default: float) -> float:
        value, _ = self._get(section, key)
        if value is None:
            return default
        number = self._float(self._label(section, key), value)
        if number < 0:
            raise ConfigError(f"{self._label(section, key)} must not be negative")
        return number

    def positive_int(self, section: str, key: str, default: int) -> int:
        number = self.positive(section, key, float(default))
        if number != int(number):
            raise ConfigError(f"{self._label(section, key)} must be a whole number")
        return int(number)

    def positive(self, section: str, key: str, default: float) -> float:
        number = self.non_negative(section, key, default)
        if number == 0:
            raise ConfigError(f"{self._label(section, key)} must be greater than 0")
        return number

    def whole_number(self, section: str, key: str, default: int) -> int:
        """An integer >= 0."""
        number = self.non_negative(section, key, float(default))
        if number != int(number):
            raise ConfigError(f"{self._label(section, key)} must be a whole number")
        return int(number)

    def port(self, section: str, key: str, default: int) -> int:
        number = self.positive_int(section, key, default)
        if number > 65535:
            raise ConfigError(f"{self._label(section, key)} must be a port number (1 to 65535)")
        return number

    def str_list(self, section: str, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        value, _ = self._get(section, key)
        label = self._label(section, key)
        if value is None:
            return default
        if isinstance(value, str):
            return tuple(p for p in (part.strip() for part in value.split(",")) if p)
        if not isinstance(value, list) or not all(isinstance(i, str) for i in cast(list[object], value)):
            raise ConfigError(f"{label} must be a list of strings")
        return tuple(s for s in (str(i).strip() for i in cast(list[str], value)) if s)

    def float_list(self, section: str, key: str, default: tuple[float, ...]) -> tuple[float, ...]:
        value, _ = self._get(section, key)
        label = self._label(section, key)
        if value is None:
            return default
        items: list[object]
        if isinstance(value, str):
            items = [part for part in (p.strip() for p in value.split(",")) if part]
        elif isinstance(value, list):
            items = list(cast(list[object], value))
        else:
            raise ConfigError(f"{label} must be a list of numbers")
        numbers = tuple(self._float(label, item) for item in items)
        if any(n <= 0 for n in numbers):
            raise ConfigError(f"{label}: every entry must be greater than 0")
        return numbers
