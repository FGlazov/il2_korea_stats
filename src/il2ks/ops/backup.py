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
import sqlite3
import tempfile
import tomllib
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Literal, cast

from il2ks import __version__
from il2ks.config import CONFIG_FILE, DB_FILE, SERVER_UID_FILE, Config
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ops.dbfile import (
    applied_migrations,
    integrity_problems,
    latest_migrations,
    open_readonly,
    snapshot_database,
)

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


_STALE_PARTIAL = timedelta(hours=1)


def _claim_name(backup_dir: Path, when: datetime) -> tuple[Path, Path, IO[bytes], datetime]:
    """Reserve a backup name nobody else is using: (final path, temp path, the temp file opened for writing, time).

    A manual backup and the daily one (or two `il2ks backup`) can run in the same second. The temp file is created
    exclusively (`x`), so exactly one of them gets a given name; the loser takes the next second. The final name is
    checked again after winning the temp file, because the other backup may have finished (temp renamed away) in
    between. A temp file left by a crashed backup (older than an hour) is cleared out of the way."""
    while True:
        target = backup_dir / f"il2ks-backup-{when.strftime(_STAMP)}.zip"
        partial = target.with_name(target.name + ".tmp")
        if not target.exists():
            try:
                handle = partial.open("xb")
            except FileExistsError:
                if _remove_stale(partial):
                    continue  # that name is free again
            else:
                if not target.exists():
                    return target, partial, handle, when
                handle.close()
                partial.unlink(missing_ok=True)
        when += timedelta(seconds=1)


def _remove_stale(partial: Path) -> bool:
    """Delete a temp file nobody has written to for an hour; whether it is gone."""
    try:
        if datetime.now(UTC) - datetime.fromtimestamp(partial.stat().st_mtime, UTC) <= _STALE_PARTIAL:
            return False
        partial.unlink(missing_ok=True)
    except OSError:
        return False  # being finished or deleted by its owner right now: the next name is as good
    return True


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
    target, partial, out, when = _claim_name(backup_dir, when)
    included = [DB_FILE]
    try:
        with out, tempfile.TemporaryDirectory(dir=backup_dir, prefix="snapshot-") as scratch:
            snapshot = Path(scratch) / DB_FILE
            snapshot_database(cfg.db_path, snapshot)
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
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
    copy_to_second_folder(cfg)
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
            made = create_backup(cfg, "daily", now=lambda: now)
    except LockBusyError:
        return None
    mirror_archive(cfg)  # after the lock is released: the first mirror of a big archive takes a while
    return made


# --- the second copy (`[backup] copy_to`) --------------------------------------------------------------------------

COPY_STATUS_FILE = "backup_copy_status.json"
ARCHIVE_COPY_NAME = "archive"
_MAX_ARCHIVE_FAILURES = 3  # a share that went away fails every file: give up after a few, the next backup retries
_MTIME_SLACK_S = 2  # FAT and some shares store modification times with 2 s granularity


@dataclass(frozen=True, slots=True)
class CopyOutcome:
    """What the last attempt to copy to `[backup] copy_to` did, kept in `<data dir>/backup_copy_status.json` so
    `il2ks doctor` can show it (the copy never fails the backup itself)."""

    kind: Literal["backups", "archive"]
    ok: bool
    at: str  # ISO time
    detail: str  # what was copied, or why it failed


def _status_path(cfg: Config) -> Path:
    return cfg.data_dir / COPY_STATUS_FILE


def read_copy_status(cfg: Config) -> dict[str, CopyOutcome]:
    """The last copy outcomes by kind (`backups`, `archive`); empty when nothing was copied yet or the file is bad."""
    try:
        raw = cast(object, json.loads(_status_path(cfg).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}
    found: dict[str, CopyOutcome] = {}
    if isinstance(raw, dict):
        for kind, entry in cast(dict[str, object], raw).items():
            if kind in {"backups", "archive"} and isinstance(entry, dict):
                fields = cast(dict[str, object], entry)
                found[kind] = CopyOutcome(
                    cast(Literal["backups", "archive"], kind),
                    fields.get("ok") is True,
                    str(fields.get("at", "")),
                    str(fields.get("detail", "")),
                )
    return found


def _record(cfg: Config, outcome: CopyOutcome) -> None:
    status = read_copy_status(cfg)
    status[outcome.kind] = outcome
    payload = {k: {"ok": v.ok, "at": v.at, "detail": v.detail} for k, v in status.items()}
    try:
        _status_path(cfg).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("could not write %s: %s", _status_path(cfg), exc)


def _copy_file(source: Path, dest: Path) -> None:
    """Copy through a temporary name next to the destination, so a reader never sees half a file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".tmp")
    try:
        shutil.copyfile(source, partial)
        shutil.copystat(source, partial)
        os.replace(partial, dest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _fail(cfg: Config, kind: Literal["backups", "archive"], message: str) -> CopyOutcome:
    log.warning("[backup] copy_to: %s", message)
    outcome = CopyOutcome(kind, False, utcnow().isoformat(timespec="seconds"), message)
    _record(cfg, outcome)
    return outcome


def copy_to_second_folder(cfg: Config) -> CopyOutcome | None:
    """Copy the backups that the second folder lacks and rotate it with the same `[backup] keep`; None if not set up.

    Never raises: a failure is logged and recorded for doctor. Every backup of the main folder the copy lacks is copied
    (newest `keep` only), so a share that was offline for a few days catches up with the next backup."""
    second = cfg.backup.copy_to
    if second is None:
        return None
    try:
        if second.resolve() == cfg.backup_dir.resolve():
            return _fail(cfg, "backups", f"copy_to ({second}) is the backup folder itself; choose another folder")
        second.mkdir(parents=True, exist_ok=True)
        copied = 0
        for path in list_backups(cfg.backup_dir)[-cfg.backup.keep :]:
            dest = second / path.name
            if not dest.is_file() or dest.stat().st_size != path.stat().st_size:
                _copy_file(path, dest)
                copied += 1
        deleted = rotate(second, cfg.backup.keep)
    except OSError as exc:
        return _fail(cfg, "backups", f"could not copy the backup to {second}: {exc}")
    detail = f"{copied} backup(s) copied to {second}, {len(deleted)} old one(s) removed there"
    log.info("backup copy: %s", detail)
    outcome = CopyOutcome("backups", True, utcnow().isoformat(timespec="seconds"), detail)
    _record(cfg, outcome)
    return outcome


def _needs_copy(source: Path, dest: Path) -> bool:
    try:
        have = dest.stat()
    except FileNotFoundError:
        return True
    now = source.stat()
    return have.st_size != now.st_size or now.st_mtime - have.st_mtime > _MTIME_SLACK_S


def mirror_archive(cfg: Config) -> CopyOutcome | None:
    """Mirror the mission archive into `<copy_to>/archive`, copying only new or changed files; None if not switched on.

    Nothing is ever deleted from the mirror (the archive is the source of truth and never loses files on its own, so a
    file missing in the main archive is a mistake the mirror should survive). Never raises, like the backup copy."""
    second = cfg.backup.copy_to
    if second is None or not cfg.backup.copy_archive:
        return None
    source_root = cfg.archive_dir
    if not source_root.is_dir():
        return None
    mirror = second / ARCHIVE_COPY_NAME
    copied = failed = 0
    first_error = ""
    try:
        files = sorted(p for p in source_root.rglob("*.zip") if p.is_file())
    except OSError as exc:
        return _fail(cfg, "archive", f"could not read the archive folder {source_root}: {exc}")
    for path in files:
        dest = mirror / path.relative_to(source_root)
        try:
            if _needs_copy(path, dest):
                _copy_file(path, dest)
                copied += 1
        except OSError as exc:
            failed += 1
            first_error = first_error or f"{path.name}: {exc}"
            if failed >= _MAX_ARCHIVE_FAILURES:
                break
    if failed:
        return _fail(cfg, "archive", f"archive mirror to {mirror} failed after {copied} file(s): {first_error}")
    detail = f"{copied} new or changed archive file(s) copied to {mirror}"
    log.info("backup copy: %s", detail)
    outcome = CopyOutcome("archive", True, utcnow().isoformat(timespec="seconds"), detail)
    _record(cfg, outcome)
    return outcome


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
            previous_version = read_data_version(cfg.db_path)
            try:
                written = _swap_in(cfg, staging, config_target, now=now)
            except OSError as exc:
                where = f" Your previous state is saved in {safety}." if safety is not None else ""
                raise BackupError(
                    f"could not replace the files ({exc}). Is the website still running (il2ks run or il2ks web)? "
                    f"Stop it and try again.{where}"
                ) from exc
            _bump_data_version(cfg.db_path, previous_version, now())
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    _verify(cfg, manifest)
    configured = None if written is None else _configured_data_dir(written)
    return RestoreResult(manifest, safety, written, configured is not None and configured != data_dir.resolve())


DATA_VERSION_TABLE = "il2ks_db_dataversion"  # db.models.DataVersion (a test keeps the two in step)
DATA_VERSION_JUMP = 1000


def read_data_version(db_path: Path) -> int:
    """The site's data version (TD-28: ETags are built from it) in this database file; 0 if there is none."""
    try:
        with closing(open_readonly(db_path)) as conn:
            row = conn.execute(f"SELECT version FROM {DATA_VERSION_TABLE} WHERE id = 1").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row is not None else 0


def _bump_data_version(db_path: Path, previous: int, when: datetime) -> None:
    """Move the restored database's data version past anything a browser may have cached.

    The backup carries the version of its day; the live site may have been much further along, and a browser holding a
    page ETag from that later time must not get a 304 for a page that now shows older data. So the restored version
    becomes max(restored, previous) + 1000. Ignored when the backup predates the table (migrations create it later)."""
    try:
        with closing(sqlite3.connect(db_path, timeout=30)) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (DATA_VERSION_TABLE,)
            ).fetchone()
            if exists is None:
                return
            row = conn.execute(f"SELECT version FROM {DATA_VERSION_TABLE} WHERE id = 1").fetchone()
            restored = int(row[0]) if row is not None else 0
            new = max(restored, previous) + DATA_VERSION_JUMP
            stamp = when.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
            if row is None:
                conn.execute(
                    f"INSERT INTO {DATA_VERSION_TABLE} (id, version, updated_at) VALUES (1, ?, ?)",
                    (new, stamp),
                )
            else:
                conn.execute(
                    f"UPDATE {DATA_VERSION_TABLE} SET version = ?, updated_at = ? WHERE id = 1",
                    (new, stamp),
                )
            conn.commit()
    except sqlite3.Error as exc:
        log.warning(
            "restore: could not move the data version forward (%s); browsers may keep an old page for a minute", exc
        )


def _swap_in(cfg: Config, staging: Path, config_target: Path, *, now: Callable[[], datetime] = utcnow) -> Path | None:
    """Replace the live files with the unpacked ones, all or nothing.

    1. Rename every live target aside (`custom` -> `custom.old-<stamp>`, the database and its -wal/-shm/-journal, the
       secrets, the config). On Windows this is what fails when something still holds a file open (the website, an
       editor in `custom/`), and it fails before anything was changed: whatever was renamed is renamed back.
    2. Move the restored items into place. If that fails, the restored items are moved out again and step 1 is undone.
    3. Only then delete the `.old-<stamp>` copies (the safety backup holds the same data).

    A folder the backup doesn't have is left alone."""
    data_dir = cfg.data_dir
    stamp = now().strftime(_STAMP)
    incoming: list[tuple[Path, Path]] = [(staging / DB_FILE, cfg.db_path)]
    incoming += [(staging / name, data_dir / name) for name in SINGLE_FILES if (staging / name).is_file()]
    incoming += [(staging / folder, data_dir / folder) for folder in DIRECTORIES if (staging / folder).is_dir()]
    written: Path | None = None
    if (staging / CONFIG_IN_ZIP).is_file():
        config_target.parent.mkdir(parents=True, exist_ok=True)
        incoming.append((staging / CONFIG_IN_ZIP, config_target))
        written = config_target

    live = [dest for _, dest in incoming]
    live += [Path(str(cfg.db_path) + suffix) for suffix in _DB_SIDE_FILES]  # must not meet the restored database
    aside: list[tuple[Path, Path]] = []  # (live path, where it went), for the undo
    placed: list[tuple[Path, Path]] = []  # (live path, where it came from)
    try:
        for path in live:
            if path.exists():
                moved = path.with_name(f"{path.name}.old-{stamp}")
                if moved.exists():  # a leftover of an earlier failed restore in the same second: never merge into it
                    _remove(moved)
                os.replace(path, moved)
                aside.append((path, moved))
        for source, dest in incoming:
            os.replace(source, dest)
            placed.append((dest, source))
    except OSError:
        for dest, source in reversed(placed):
            _move_back(dest, source)
        for path, moved in reversed(aside):
            _move_back(moved, path)
        raise
    for _, moved in aside:
        _remove(moved)
    return written


_DB_SIDE_FILES = ("-wal", "-shm", "-journal")


def _move_back(source: Path, dest: Path) -> None:
    try:
        os.replace(source, dest)
    except OSError as exc:
        log.error("restore undo: could not move %s back to %s: %s", source, dest, exc)


def _remove(path: Path) -> None:
    """Delete a file or folder, best effort: an `.old-<stamp>` copy that stays behind is only clutter."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        log.warning("could not delete %s (%s); it is a copy of the replaced data and can be deleted by hand", path, exc)


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
