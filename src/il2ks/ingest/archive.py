"""Mission log archives (FR-ING-8, FR-ING-10, TD-09).

Layout: `<data dir>/archive/YYYY/MM/missionReport(<uid>)[0].txt.zip` (YYYY/MM from the mission UID, the server's local
start time). One zip entry `missionReport(<uid>)[0].txt` holding all parts concatenated in order: the same format and
names as the `il2_stats` backups and `sample_data/`, so `group_mission_files` / `read_mission_lines` read our archives
like any import (FR-ING-13). DEFLATE: stdlib, fast, readable by every zip tool; logs still compress ~15x.

Writing goes to a temporary file in the target folder, is verified (CRC re-read and a sha256 of the uncompressed content
compared with what was written), and only then renamed over the final path. The returned `sha256` is of the zip file
itself; it's stored on `IngestRun` / `Mission`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from il2ks.config import AfterArchive

log = logging.getLogger(__name__)

_CHUNK = 1 << 20


class ArchiveError(RuntimeError):
    """Writing or verifying an archive failed."""


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    path: Path
    sha256: str  # of the zip file
    content_sha256: str  # of the uncompressed concatenated log
    size: int  # uncompressed bytes


def mission_log_name(mission_uid: str) -> str:
    return f"missionReport({mission_uid})[0].txt"


def archive_path_for(archive_dir: Path, mission_uid: str) -> Path:
    """`<archive dir>/YYYY/MM/missionReport(<uid>).txt.zip`."""
    year, month = mission_uid[0:4], mission_uid[5:7]
    if not (year.isdigit() and month.isdigit()):
        raise ArchiveError(f"mission UID doesn't start with a date: {mission_uid!r}")
    return archive_dir / year / month / f"{mission_log_name(mission_uid)}.zip"


def iter_zip_bytes(source: Path) -> Iterator[bytes]:
    """The bytes of the single log file inside a zip."""
    with zipfile.ZipFile(source) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        if len(members) != 1:
            raise ArchiveError(f"{source}: expected exactly one log file inside, found {len(members)}")
        with zf.open(members[0]) as fh:
            while chunk := fh.read(_CHUNK):
                yield chunk


def iter_source_bytes(source: Path) -> Iterator[bytes]:
    """The raw bytes of one source: a part or concatenated `.txt`, or the log inside a `.txt.zip`."""
    if source.name.lower().endswith(".zip"):
        yield from iter_zip_bytes(source)
        return
    with source.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            yield chunk


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def write_archive(
    sources: Sequence[Path], target: Path, mission_uid: str, entry_time: datetime | None = None
) -> ArchiveResult:
    """Concatenate `sources` into `target` (zip, one entry) and verify it. Replaces an existing archive atomically.

    A line break is inserted between sources when one doesn't end with one, so parts never merge lines.
    `entry_time` (local time, as zip stores it) defaults to now; callers pass part [0]'s mtime (TD-15 hint)."""
    if not sources:
        raise ArchiveError(f"{mission_uid}: no source files")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    stamp = (entry_time or datetime.now()).timetuple()[:6]
    info = zipfile.ZipInfo(mission_log_name(mission_uid), date_time=(max(stamp[0], 1980), *stamp[1:]))
    info.compress_type = zipfile.ZIP_DEFLATED
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf,
            zf.open(info, "w", force_zip64=True) as out,
        ):
            last = b"\n"
            for source in sources:
                if not last.endswith(b"\n"):
                    out.write(b"\r\n")
                    digest.update(b"\r\n")
                    size += 2
                for chunk in iter_source_bytes(source):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    last = chunk
        content_sha = digest.hexdigest()
        verify_archive(tmp, content_sha)
        zip_sha = file_sha256(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return ArchiveResult(path=target, sha256=zip_sha, content_sha256=content_sha, size=size)


def verify_archive(path: Path, content_sha256: str) -> None:
    """Re-read the archive once: it must be a valid zip, reading it to the end checks its CRC (`zipfile` raises
    `BadZipFile` on a mismatch), and the content hash must match what was written."""
    digest = hashlib.sha256()
    try:
        for chunk in iter_zip_bytes(path):
            digest.update(chunk)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"{path}: not a valid zip or CRC error ({exc})") from exc
    if digest.hexdigest() != content_sha256:
        raise ArchiveError(f"{path}: content doesn't match what was written")


def archive_matches(path: Path, sha256: str) -> bool:
    """Reconcile check: the archive exists and is byte-identical to the one recorded."""
    return path.is_file() and file_sha256(path) == sha256


@dataclass(frozen=True, slots=True)
class DisposeResult:
    done: tuple[Path, ...]
    failed: tuple[tuple[Path, str], ...]  # (file, error)


def dispose_originals(files: Sequence[Path], mode: AfterArchive, move_to: Path) -> DisposeResult:
    """Move, keep or delete original log files after their archive is verified (FR-ING-10).

    Per-file errors (on Windows: DServer or a copy tool still has the file open) are collected, not raised; the next run
    retries them. A file that already exists at the destination is replaced."""
    if mode == "keep":
        return DisposeResult((), ())
    done: list[Path] = []
    failed: list[tuple[Path, str]] = []
    for file in files:
        try:
            if mode == "delete":
                file.unlink()
            else:
                move_to.mkdir(parents=True, exist_ok=True)
                dest = move_to / file.name
                if dest.exists():
                    dest.unlink()
                shutil.move(file, dest)
            done.append(file)
        except FileNotFoundError:
            done.append(file)  # already gone: nothing to do
        except OSError as exc:
            failed.append((file, str(exc)))
            log.warning("could not %s %s: %s (will retry next run)", mode, file, exc)
    return DisposeResult(tuple(done), tuple(failed))
