"""Backups of the admin state and their restore (FR-OPS-6).

What is in `<data dir>/backups/il2ks-backup-YYYYMMDD-HHMMSS.zip` (UTC time in the name): a consistent snapshot of the
SQLite database, the config file, `custom/`, `media/`, `server_uid.txt` and `secret_key.txt` when they exist, and
`manifest.json` (il2ks version, schema state, time, why). **Not** the archived mission logs: they are large and kept
forever on their own, and they rebuild the statistics (`il2ks reprocess`). What a backup protects is what the logs
cannot rebuild: hidden players, name overrides, branding, admin accounts, the server ID.

Backups are made by `il2ks backup`, before pending migrations are applied (`ops.migrate`), once a day by `il2ks watch`
(`backup_if_due`), and before every restore. SQLite only: Postgres is dev-side only (TD-04).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import tempfile
import tomllib
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from il2ks import __version__
from il2ks.config import CONFIG_FILE, DB_FILE, SERVER_UID_FILE, Config
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ops.dbfile import applied_migrations, integrity_problems, latest_migrations, snapshot_database

log = logging.getLogger(__name__)

type BackupReason = Literal["manual", "daily", "pre-migrate", "pre-restore"]

FORMAT_VERSION = 1
MANIFEST = "manifest.json"
SECRET_KEY_FILE = "secret_key.txt"
CONFIG_IN_ZIP = CONFIG_FILE
DIRECTORIES = ("custom", "media")
SINGLE_FILES = (SERVER_UID_FILE, SECRET_KEY_FILE)
BACKUP_INTERVAL = timedelta(days=1)
_NAME_RE = re.compile(r"il2ks-backup-(\d{8}-\d{6})\.zip")
_STAMP = "%Y%m%d-%H%M%S"


class BackupError(RuntimeError):
    """A backup or restore could not be done; the message tells the admin why and what to do."""


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- the backup folder ---------------------------------------------------------------------------------------------


def backup_time(path: Path) -> datetime | None:
    """The time in a backup's file name, or None if the name isn't one of ours."""
    m = _NAME_RE.fullmatch(path.name)
    return None if m is None else datetime.strptime(m.group(1), _STAMP).replace(tzinfo=UTC)


def list_backups(backup_dir: Path) -> list[Path]:
    """Our backup zips, oldest first. Half-written files (`.tmp`) and strangers are ignored."""
    if not backup_dir.is_dir():
        return []
    return sorted((p for p in backup_dir.iterdir() if p.is_file() and backup_time(p) is not None), key=lambda p: p.name)


def newest_backup_time(backup_dir: Path) -> datetime | None:
    found = list_backups(backup_dir)
    return backup_time(found[-1]) if found else None


def rotate(backup_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` backups; returns what was deleted."""
    doomed = list_backups(backup_dir)[:-keep] if keep > 0 else []
    for path in doomed:
        path.unlink(missing_ok=True)
    return doomed


# --- making a backup -----------------------------------------------------------------------------------------------


def _free_name(backup_dir: Path, when: datetime) -> tuple[Path, datetime]:
    """A file name nobody has used yet: two backups in the same second get the next second."""
    while True:
        path = backup_dir / f"il2ks-backup-{when.strftime(_STAMP)}.zip"
        if not path.exists():
            return path, when
        when += timedelta(seconds=1)


def _tree_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def create_backup(cfg: Config, reason: BackupReason = "manual", *, now: Callable[[], datetime] = utcnow) -> Path:
    """Write one backup zip and rotate. Raises `BackupError` if there is no database yet to protect.

    The database is copied with SQLite's online backup API into a temporary file first, so the zip holds a consistent
    snapshot even while `watch` or the website write to the live file."""
    migrations = applied_migrations(cfg.db_path)
    if migrations is None:
        raise BackupError(f"there is no database to back up yet ({cfg.db_path}); run il2ks setup first")
    backup_dir = cfg.backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    when = now()
    target, when = _free_name(backup_dir, when)
    partial = target.with_name(target.name + ".tmp")
    included = [DB_FILE]
    try:
        with tempfile.TemporaryDirectory(dir=backup_dir, prefix="snapshot-") as scratch:
            snapshot = Path(scratch) / DB_FILE
            snapshot_database(cfg.db_path, snapshot)
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
                zf.write(snapshot, DB_FILE)
                if cfg.source is not None and cfg.source.is_file():
                    zf.write(cfg.source, CONFIG_IN_ZIP)
                    included.append(CONFIG_IN_ZIP)
                for name in SINGLE_FILES:
                    if (cfg.data_dir / name).is_file():
                        zf.write(cfg.data_dir / name, name)
                        included.append(name)
                for folder in DIRECTORIES:
                    for path in _tree_files(cfg.data_dir / folder):
                        arcname = f"{folder}/{path.relative_to(cfg.data_dir / folder).as_posix()}"
                        zf.write(path, arcname)
                        included.append(arcname)
                manifest: dict[str, object] = {
                    "format": FORMAT_VERSION,
                    "il2ks_version": __version__,
                    "created_at": when.isoformat(timespec="seconds"),
                    "reason": reason,
                    "server_uid": str(cfg.server_uid),
                    "database": DB_FILE,
                    "migrations": latest_migrations(migrations),
                    "config_path": str(cfg.source) if cfg.source is not None else None,
                    "data_dir": str(cfg.data_dir),
                    "files": included,
                    "not_included": "archived mission logs (archive/): large, kept forever on their own",
                }
                zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    deleted = rotate(backup_dir, cfg.backup.keep)
    log.info("backup written: %s (%s); %d old backup(s) removed", target, reason, len(deleted))
    return target


def backup_before_migration(cfg: Config) -> Path | None:
    """Back up before pending migrations change the schema (NFR-INS-4). A fresh install has nothing to protect."""
    if applied_migrations(cfg.db_path) is None:
        return None
    return create_backup(cfg, "pre-migrate")


def backup_if_due(cfg: Config, now: datetime) -> Path | None:
    """The daily backup `watch` asks for on every tick: a directory listing unless one is really due.

    Skipped when switched off (`[backup] daily`), when the newest backup is younger than a day, when there is no
    database yet, or when another writer holds the lock (a restore must not be backed up half-done; try next tick)."""
    if not cfg.backup.daily:
        return None
    newest = newest_backup_time(cfg.backup_dir)
    if newest is not None and now - newest < BACKUP_INTERVAL:
        return None
    if applied_migrations(cfg.db_path) is None:
        return None
    try:
        with WriterLock(cfg.data_dir, "backup"):
            return create_backup(cfg, "daily", now=lambda: now)
    except LockBusyError:
        return None


# --- restore -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Manifest:
    il2ks_version: str
    created_at: str
    reason: str
    migrations: dict[str, str]
    config_path: str | None
    files: tuple[str, ...]


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def _as_str_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in cast(dict[object, object], value).items()}


def read_manifest(zip_path: Path) -> Manifest:
    """Open a backup zip and check it is a sound il2ks backup (CRC of every entry, manifest, database present)."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise BackupError(f"{zip_path} is damaged (entry {bad} fails its checksum)")
            names = zf.namelist()
            if MANIFEST not in names:
                raise BackupError(f"{zip_path} is not an il2ks backup (no {MANIFEST})")
            raw = cast(object, json.loads(zf.read(MANIFEST)))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise BackupError(f"cannot read {zip_path} as a backup: {exc}") from exc
    if not isinstance(raw, dict):
        raise BackupError(f"{zip_path}: the manifest is not valid")
    fields = cast(dict[str, object], raw)
    if fields.get("format") != FORMAT_VERSION:
        raise BackupError(f"{zip_path}: unsupported backup format {fields.get('format')!r}")
    if DB_FILE not in names:
        raise BackupError(f"{zip_path} holds no database ({DB_FILE})")
    for name in names:
        _check_member(name)
    config_path = fields.get("config_path")
    return Manifest(
        il2ks_version=str(fields.get("il2ks_version", "")),
        created_at=str(fields.get("created_at", "")),
        reason=str(fields.get("reason", "")),
        migrations=_as_str_dict(fields.get("migrations")),
        config_path=config_path if isinstance(config_path, str) else None,
        files=tuple(names),
    )


def _check_member(name: str) -> None:
    """Only the files a backup writes are restored; a crafted zip can't reach outside the data folder."""
    parts = name.split("/")
    known = name in {MANIFEST, DB_FILE, CONFIG_IN_ZIP, *SINGLE_FILES}
    in_folder = len(parts) > 1 and parts[0] in DIRECTORIES and parts[-1] != ""
    if not (known or in_folder) or ".." in parts or name.startswith("/") or "\\" in name or ":" in name:
        raise BackupError(f"unexpected entry {name!r} in the backup: refusing to restore it")


def web_probably_running(port: int) -> bool:
    """True if something accepts connections on localhost:`port` (the site's default is 8000)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class RestoreResult:
    manifest: Manifest
    safety_backup: Path | None
    config_written: Path | None
    restored_data_dir_differs: bool  # the restored config names another data folder than the one restored into


def restore_backup(
    cfg: Config, zip_path: Path, config_target: Path, *, now: Callable[[], datetime] = utcnow
) -> RestoreResult:
    """Put a backup back: database, config, `custom/`, `media/`, server ID and secret key.

    Order: validate the zip and unpack it next to the live files (nothing live touched yet), check the unpacked
    database, take a safety backup of what is there now, then swap the files in and verify. Raises `LockBusyError`
    while another writer is running and `BackupError` for anything wrong with the zip or the result."""
    manifest = read_manifest(zip_path)
    if _version_tuple(manifest.il2ks_version) > _version_tuple(__version__):
        raise BackupError(
            f"the backup was made by il2ks {manifest.il2ks_version}, newer than this il2ks ({__version__}): "
            "upgrade il2ks first"
        )
    data_dir = cfg.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    with WriterLock(data_dir, "restore"):
        staging = Path(tempfile.mkdtemp(prefix="restore-", dir=data_dir))
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(staging)
            problems = integrity_problems(staging / DB_FILE)
            if problems:
                raise BackupError(f"the database inside the backup is damaged: {problems[0]}")
            safety = create_backup(cfg, "pre-restore", now=now) if applied_migrations(cfg.db_path) else None
            try:
                written = _swap_in(cfg, staging, config_target)
            except OSError as exc:
                where = f" Your previous state is saved in {safety}." if safety is not None else ""
                raise BackupError(
                    f"could not replace the files ({exc}). Is the website still running (il2ks run or il2ks web)? "
                    f"Stop it and try again.{where}"
                ) from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    _verify(cfg, manifest)
    configured = None if written is None else _configured_data_dir(written)
    return RestoreResult(manifest, safety, written, configured is not None and configured != data_dir.resolve())


def _swap_in(cfg: Config, staging: Path, config_target: Path) -> Path | None:
    data_dir = cfg.data_dir
    for suffix in ("-wal", "-shm", "-journal"):  # leftovers of the replaced database must not meet the restored one
        Path(str(cfg.db_path) + suffix).unlink(missing_ok=True)
    os.replace(staging / DB_FILE, cfg.db_path)
    for name in SINGLE_FILES:
        if (staging / name).is_file():
            os.replace(staging / name, data_dir / name)
    for folder in DIRECTORIES:
        if (staging / folder).is_dir():
            shutil.rmtree(data_dir / folder, ignore_errors=True)
            os.replace(staging / folder, data_dir / folder)
    # A folder the backup doesn't have is left alone: the safety backup holds it either way.
    written: Path | None = None
    if (staging / CONFIG_IN_ZIP).is_file():
        config_target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging / CONFIG_IN_ZIP, config_target)
        written = config_target
    return written


def _configured_data_dir(config_file: Path) -> Path | None:
    """`data_dir` of a restored config (None = not set), to warn when a backup moves to another machine."""
    try:
        raw = cast(dict[str, object], tomllib.loads(config_file.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    value = raw.get("data_dir")
    if not isinstance(value, str) or not value:
        return None
    return (config_file.resolve().parent / Path(value).expanduser()).resolve()


def _verify(cfg: Config, manifest: Manifest) -> None:
    problems = integrity_problems(cfg.db_path)
    if problems:
        raise BackupError(f"the restored database failed its integrity check: {problems[0]}")
    restored = applied_migrations(cfg.db_path)
    if restored is None or latest_migrations(restored) != manifest.migrations:
        raise BackupError("the restored database does not match the backup's recorded schema state")
