"""The doctor checks of the ops area (FR-OPS-1): data folder, database, log folder, timezone, server ID, disk, ingestion
health and backups. Each is registered with `@check` and imported through `doctor.CHECK_MODULES`.

The checks only look: they never create the data folder or the database file, so running `il2ks doctor` on a machine
that isn't set up yet is harmless.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from il2ks.config import SERVER_UID_FILE, Config
from il2ks.core.logparse.files import mission_uid_from_name
from il2ks.ops.backup import list_backups, newest_backup_time
from il2ks.ops.dbfile import applied_migrations
from il2ks.ops.doctor import Finding, Level, check

STALE_REPORT_DAYS = 7
LOW_DISK_WARN_BYTES = 1 << 30  # 1 GiB
LOW_DISK_ERROR_BYTES = 200 << 20  # 200 MiB
STALE_BACKUP_DAYS_DAILY = 3
STALE_BACKUP_DAYS_OTHERWISE = 30
MAX_LISTED = 5

TEXT_LOGS_HELP = (
    "Make sure the game server writes text mission logs: the setting is in DServer's startup.cfg (in the older BoS "
    "DServer it is mission_text_log = 1 together with text_log_folder; the exact names for IL-2 Korea are not "
    "confirmed yet, so check your server's documentation or ask another Korea server operator). Then let one "
    "mission finish and look at whether missionReport(...)[0].txt files appear, and make sure [logs] dir in "
    "il2ks.toml is that folder."
)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _age(delta: timedelta) -> str:
    seconds = max(delta.total_seconds(), 0.0)
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes"
    if seconds < 86400 * 2:
        return f"{int(seconds // 3600)} hours"
    return f"{int(seconds // 86400)} days"


def _nearest_existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def raw_config(cfg: Config) -> dict[str, object]:
    if cfg.source is None:
        return {}
    try:
        return cast(dict[str, object], tomllib.loads(cfg.source.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _server_setting(cfg: Config, key: str) -> str:
    """`[server] <key>` as the admin set it (environment first, then the file); "" if they didn't."""
    env = os.environ.get(f"IL2KS_SERVER_{key.upper()}")
    if env is not None:
        return env
    table = raw_config(cfg).get("server")
    value = cast(dict[str, object], table).get(key) if isinstance(table, dict) else None
    return value if isinstance(value, str) else ""


@check
def data_dir_check(cfg: Config) -> Iterable[Finding]:
    folder = cfg.data_dir
    if not folder.is_dir():
        yield Finding(
            Level.ERROR,
            "Data folder does not exist",
            str(folder),
            "Run il2ks setup, or point data_dir in il2ks.toml (or IL2KS_DATA_DIR) at the folder you set up before.",
        )
        return
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix="doctor-", delete=True):
            pass
    except OSError as exc:
        yield Finding(
            Level.ERROR,
            "Data folder is not writable",
            f"{folder}: {exc}",
            "Run il2ks as a user who may write to this folder, or change its permissions.",
        )
        return
    yield Finding(Level.OK, "Data folder exists and is writable", str(folder))


@check
def database_check(cfg: Config) -> Iterable[Finding]:
    if not cfg.data_dir.is_dir():
        return  # the data folder check already reports it
    if not cfg.db_path.is_file():
        yield Finding(
            Level.ERROR,
            "Database not created yet",
            str(cfg.db_path),
            "Run il2ks setup. (Any command that writes, such as il2ks ingest, also creates it.)",
        )
        return
    applied = applied_migrations(cfg.db_path)
    if applied is None:
        yield Finding(
            Level.ERROR,
            "Database cannot be read or has not been set up",
            str(cfg.db_path),
            "Run il2ks setup. If the file is damaged, restore the newest backup: il2ks restore <zip> "
            f"(backups are in {cfg.backup_dir}).",
        )
        return
    from django.db.migrations.loader import MigrationLoader

    wanted = set(MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes())
    pending = sorted(wanted - applied)
    if pending:
        yield Finding(
            Level.WARN,
            f"Database needs an update ({len(pending)} pending migration(s))",
            ", ".join(f"{app}.{name}" for app, name in pending[:MAX_LISTED]),
            "Nothing to do by hand: the update is applied, after an automatic backup, the next time "
            "il2ks run, watch or ingest starts.",
        )
        return
    size_mb = cfg.db_path.stat().st_size / (1 << 20)
    yield Finding(Level.OK, "Database is readable and up to date", f"{cfg.db_path} ({size_mb:.1f} MB)")


def _report_files(folder: Path) -> tuple[int, float]:
    """(count, newest mtime) of mission report files directly in `folder`."""
    count, newest = 0, 0.0
    with os.scandir(folder) as entries:
        for entry in entries:
            name = entry.name.lower()
            if (name.endswith(".txt") or name.endswith(".txt.zip")) and mission_uid_from_name(entry.name) is not None:
                count += 1
                newest = max(newest, entry.stat().st_mtime)
    return count, newest


def _newest_archive_mtime(archive_dir: Path) -> float | None:
    """mtime of the newest archived mission: looks only at the newest year and month folders."""
    if not archive_dir.is_dir():
        return None
    for year in sorted((p for p in archive_dir.iterdir() if p.is_dir()), reverse=True):
        for month in sorted((p for p in year.iterdir() if p.is_dir()), reverse=True):
            mtimes = [p.stat().st_mtime for p in month.iterdir() if p.is_file()]
            if mtimes:
                return max(mtimes)
    return None


@check
def log_folder_check(cfg: Config) -> Iterable[Finding]:
    folder = cfg.logs.dir
    if folder is None:
        yield Finding(
            Level.WARN,
            "No log folder configured",
            "[logs] dir is not set, so il2ks ingest and il2ks watch have nothing to read.",
            "Set [logs] dir in il2ks.toml to DServer's text log folder (il2ks setup suggests candidates). "
            "Until then you can import archives with il2ks ingest --from FOLDER.",
        )
        return
    if not folder.is_dir():
        yield Finding(
            Level.ERROR,
            "Log folder does not exist",
            str(folder),
            "Fix [logs] dir in il2ks.toml. Under Wine the folder is inside the Wine prefix "
            "(for example ~/.wine/drive_c/...). If the logs are copied in from another machine, check the copy job.",
        )
        return
    try:
        reports, newest_in_folder = _report_files(folder)
    except OSError as exc:
        yield Finding(Level.ERROR, "Log folder cannot be read", f"{folder}: {exc}", "Check the folder's permissions.")
        return
    archived = _newest_archive_mtime(cfg.archive_dir)
    if reports == 0 and archived is None:
        yield Finding(
            Level.WARN,
            "No mission reports found: DServer text logs may be switched off",
            f"{folder} holds no missionReport(...)[N].txt files, and il2ks has not archived any mission yet.",
            TEXT_LOGS_HELP,
        )
        return
    newest = max(newest_in_folder, archived or 0.0)
    age = datetime.now(UTC) - datetime.fromtimestamp(newest, UTC)
    where = (
        f"{reports} mission report file(s) in {folder}" if reports else f"{folder} is empty (ingested files move out)"
    )
    if age > timedelta(days=STALE_REPORT_DAYS):
        yield Finding(
            Level.WARN,
            f"Newest mission report is {_age(age)} old",
            where,
            "If the server has been idle, ignore this. Otherwise check that DServer is running and still writes "
            "text logs. " + TEXT_LOGS_HELP,
        )
        return
    yield Finding(Level.OK, f"Mission reports are arriving (newest {_age(age)} old)", where)


@check
def timezone_check(cfg: Config) -> Iterable[Finding]:
    configured = _server_setting(cfg, "timezone")
    if _is_windows() and not configured:
        yield Finding(
            Level.WARN,
            "Server timezone is not set",
            "Windows does not tell il2ks the timezone by name, so it assumes UTC. DServer names its log files in the "
            "machine's local time, so mission times would be wrong unless the machine runs on UTC.",
            'Add the machine\'s timezone to il2ks.toml, for example [server] timezone = "Europe/Berlin" or '
            '"Asia/Seoul" (IANA names), or set the machine\'s clock to UTC.',
        )
        return
    how = "set in the config" if configured else "detected"
    yield Finding(Level.OK, f"Server timezone is {cfg.timezone_name}", how)


@check
def server_uid_check(cfg: Config) -> Iterable[Finding]:
    table = raw_config(cfg).get("server")
    in_config = isinstance(table, dict) and bool(cast(dict[str, object], table).get("uid"))
    in_file = (cfg.data_dir / SERVER_UID_FILE).is_file()
    if in_config or in_file or os.environ.get("IL2KS_SERVER_UID"):
        yield Finding(Level.OK, "Server ID is present", str(cfg.server_uid))
        return
    yield Finding(
        Level.WARN,
        "Server ID has not been created yet",
        "il2ks creates the ID the first time it writes, but a backup or a reinstall would not carry it.",
        "Run il2ks setup, or any writer command such as il2ks ingest once.",
    )


@check
def disk_space_check(cfg: Config) -> Iterable[Finding]:
    free = shutil.disk_usage(_nearest_existing(cfg.data_dir)).free
    text = f"{free / (1 << 30):.1f} GiB free where the data folder is"
    if free < LOW_DISK_ERROR_BYTES:
        yield Finding(
            Level.ERROR,
            "Almost no disk space left",
            text,
            "Free up space now: the database and archives cannot grow, and ingestion will fail.",
        )
    elif free < LOW_DISK_WARN_BYTES:
        yield Finding(Level.WARN, "Disk space is low", text, "Free up space or move the data folder to a bigger disk.")
    else:
        yield Finding(Level.OK, "Enough disk space", text)


def _failed_missions() -> tuple[list[str], list[str]]:
    """(waiting for a retry, given up): missions whose newest run failed and has no OK run after it."""
    from il2ks.db.models import IngestRun, IngestStatus

    newest_failure: dict[str, tuple[datetime, datetime | None]] = {}
    failures = IngestRun.objects.filter(status=IngestStatus.FAILED).order_by("started_at")
    for uid, started, retry in failures.values_list("mission_uid", "started_at", "next_retry_at"):
        newest_failure[uid] = (started, retry)
    waiting: list[str] = []
    gave_up: list[str] = []
    uids = list(newest_failure)
    recovered: set[str] = set()
    for start in range(0, len(uids), 500):
        chunk = uids[start : start + 500]
        for uid, started in IngestRun.objects.filter(status=IngestStatus.OK, mission_uid__in=chunk).values_list(
            "mission_uid", "started_at"
        ):
            if started > newest_failure[uid][0]:
                recovered.add(uid)
    for uid in sorted(set(uids) - recovered):
        (waiting if newest_failure[uid][1] is not None else gave_up).append(uid)
    return waiting, gave_up


def _unknown_types() -> dict[str, int]:
    """Unknown AType numbers and event keys in the 200 newest successful runs, with how often they appeared."""
    from il2ks.db.models import IngestRun, IngestStatus

    counts: dict[str, int] = {}
    runs = IngestRun.objects.filter(status=IngestStatus.OK).order_by("-started_at")[:200]
    for atypes, keys in runs.values_list("unknown_atypes", "unknown_keys"):
        for name, n in cast(dict[str, int], atypes).items():
            counts[f"AType {name}"] = counts.get(f"AType {name}", 0) + n
        for name, n in cast(dict[str, int], keys).items():
            counts[f"key {name}"] = counts.get(f"key {name}", 0) + n
    return counts


@check
def ingestion_check(cfg: Config) -> Iterable[Finding]:
    from django.db import DatabaseError

    if applied_migrations(cfg.db_path) is None:
        return  # no database to look into; the database check reports it
    try:
        waiting, gave_up = _failed_missions()
        unknown = _unknown_types()
    except DatabaseError as exc:
        yield Finding(
            Level.WARN, "Ingestion history could not be read", str(exc), "Run il2ks doctor again after an update."
        )
        return
    if gave_up:
        shown = ", ".join(gave_up[:MAX_LISTED]) + (" ..." if len(gave_up) > MAX_LISTED else "")
        yield Finding(
            Level.ERROR,
            f"{len(gave_up)} mission(s) failed and il2ks gave up on them",
            f"Missing from the statistics: {shown}",
            "Open the admin, Ingest runs, to read the error, or look in the il2ks log files. Fix the cause (updating "
            "il2ks often does), then run il2ks reprocess --mission <ID> for each.",
        )
    if waiting:
        shown = ", ".join(waiting[:MAX_LISTED]) + (" ..." if len(waiting) > MAX_LISTED else "")
        yield Finding(
            Level.WARN,
            f"{len(waiting)} mission(s) failed and will be retried",
            shown,
            "Usually nothing: il2ks retries after 5 minutes, 30 minutes and 2 hours. If it keeps failing, see the "
            "error in the admin (Ingest runs).",
        )
    if not gave_up and not waiting:
        yield Finding(Level.OK, "No failed missions")
    if unknown:
        listed = ", ".join(f"{name} ({n}x)" for name, n in sorted(unknown.items())[:MAX_LISTED])
        yield Finding(
            Level.WARN,
            "The logs contain event types il2ks does not know",
            f"{listed}. The statistics may miss what these events describe.",
            "Update il2ks. If you already run the newest version, report these names to the project.",
        )


@check
def tours_check(cfg: Config) -> Iterable[Finding]:
    """FR-WEB-10: missions must sit in the tours the `[tours]` settings give them (TD-26)."""
    from django.db import DatabaseError

    from il2ks.ingest.tours import tour_problems

    if applied_migrations(cfg.db_path) is None:
        return  # the database check reports it
    try:
        problems = tour_problems(cfg.tours)
    except DatabaseError:
        return  # a database from before tours existed: the database check asks for the update
    if not problems.needs_retour:
        yield Finding(Level.OK, f"Tours match the settings ({cfg.tours.label})")
        return
    parts = [
        f"{n} {what}"
        for n, what in (
            (problems.missions_without_tour, "mission(s) without a tour"),
            (problems.stale_tours, "tour(s) made under other settings"),
            (problems.missions_outside_their_tour, "mission(s) outside their tour's dates"),
        )
        if n
    ]
    yield Finding(
        Level.WARN,
        "Tours do not match the [tours] settings",
        ", ".join(parts),
        "Run il2ks rebuild-aggregates --retour to move the missions into the right tours.",
    )


@check
def ammo_mix_check(cfg: Config) -> Iterable[Finding]:
    """A mission with gun hits on kills but no mix rows: a database from before ammo mixes (no backfill, only a reprocess
    builds them). Checked per mission, so newer missions do not hide the old ones; kills without gun hits have no mix
    rows by design and are ignored."""
    from django.db import DatabaseError
    from django.db.models import Exists, OuterRef

    from il2ks.db.models import MissionAircraftAmmo, MissionAircraftAmmoMix

    if applied_migrations(cfg.db_path) is None:
        return  # the database check reports it
    try:
        stale = (
            MissionAircraftAmmo.objects.filter(hits__gt=0)
            .exclude(Exists(MissionAircraftAmmoMix.objects.filter(mission_id=OuterRef("mission_id"))))
            .exists()
        )
    except DatabaseError:
        return  # a database from before the table existed: the database check asks for the update
    if stale:
        yield Finding(
            Level.WARN,
            "Ammunition mixes are missing for the old missions",
            "Some missions have ammunition rows but no ammunition mixes (they were ingested by an older version).",
            'Run il2ks reprocess --all (see "Upgrading from a pre-release build" in the install guide).',
        )


@check
def backup_check(cfg: Config) -> Iterable[Finding]:
    newest = newest_backup_time(cfg.backup_dir)
    if newest is None:
        yield Finding(
            Level.WARN,
            "No backup yet",
            f"{cfg.backup_dir} has no il2ks backup.",
            "Run il2ks backup. With il2ks watch or run going, a backup is made automatically once a day.",
        )
        return
    age = datetime.now(UTC) - newest
    limit = STALE_BACKUP_DAYS_DAILY if cfg.backup.daily else STALE_BACKUP_DAYS_OTHERWISE
    count = len(list_backups(cfg.backup_dir))
    if age > timedelta(days=limit):
        yield Finding(
            Level.WARN,
            f"Newest backup is {_age(age)} old",
            f"{count} backup(s) in {cfg.backup_dir}",
            "Run il2ks backup. "
            + (
                "Is il2ks watch (or run) still running? It makes the daily backup."
                if cfg.backup.daily
                else "Daily backups are switched off ([backup] daily = false)."
            ),
        )
        return
    yield Finding(Level.OK, f"Newest backup is {_age(age)} old", f"{count} backup(s) in {cfg.backup_dir}")


@check
def config_warnings_check(cfg: Config) -> Iterable[Finding]:
    """Settings the config loader ignored (renamed keys): the site would silently use the defaults instead."""
    for message in cfg.warnings:
        yield Finding(Level.WARN, "Ignored setting in il2ks.toml", message, "Edit il2ks.toml as the message says.")
