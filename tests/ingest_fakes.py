"""Fakes for the ingest jobs: file grouping, parse/replay/persist stand-ins and helpers to build log folders.

The runner takes its steps through `Pipeline`, so its tests don't depend on the real parser, replay or persist.
"""

from __future__ import annotations

import os
import re
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from il2ks.config import AfterArchive, Config, IngestConfig, LogsConfig
from il2ks.core.logparse.events import LogEvent
from il2ks.core.logparse.files import MissionLog
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import Mission
from il2ks.ingest.archive import iter_source_bytes
from il2ks.ingest.persist import MissionMeta
from il2ks.ingest.runner import Pipeline
from il2ks.ingest.timeutil import ResolvedStart

SERVER_UID = uuid.UUID("11111111-2222-3333-4444-555555555555")
T0 = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

_PART = re.compile(r"^missionReport\((\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)\)\[(\d+)\]\.txt$")
_WHOLE = re.compile(r"^missionReport\((\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)\)\.txt(\.zip)?$")


def fake_group(paths: Iterable[Path]) -> list[MissionLog]:
    """Same contract as `group_mission_files`, simplified (parts win over a whole-mission file)."""
    parts: dict[str, list[tuple[int, Path]]] = {}
    wholes: dict[str, Path] = {}
    for path in paths:
        if m := _PART.match(path.name):
            parts.setdefault(m.group(1), []).append((int(m.group(2)), path))
        elif m := _WHOLE.match(path.name):
            wholes[m.group(1)] = path
    logs: list[MissionLog] = []
    for uid in sorted(parts.keys() | wholes.keys()):
        if uid in parts:
            logs.append(MissionLog(uid, "parts", tuple(p for _, p in sorted(parts[uid]))))
        else:
            logs.append(MissionLog(uid, "archive", (wholes[uid],)))
    return logs


def part_name(uid: str, n: int) -> str:
    return f"missionReport({uid})[{n}].txt"


def write_parts(
    folder: Path, uid: str, texts: list[str], *, age_s: float, now: datetime = T0, end: bool = True
) -> list[Path]:
    """Write `[0]..[n]` parts with every mtime `age_s` seconds before `now`. The last one holds AType 7 if `end`."""
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, text in enumerate(texts):
        body = f"T:{i} AType:15 VER:18\r\n{text}\r\n"
        if end and i == len(texts) - 1:
            body += f"T:{i + 100} AType:7\r\n"
        path = folder / part_name(uid, i)
        path.write_bytes(body.encode())
        set_mtime(path, now - timedelta(seconds=age_s))
        paths.append(path)
    return paths


def set_mtime(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def write_zip(path: Path, uid: str, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"missionReport({uid}).txt", text)
    return path


@dataclass(slots=True)
class FakeSteps:
    """Records what the pipeline did. `fail_on` makes `save` raise for those mission UIDs."""

    fail_on: set[str] = field(default_factory=set[str])
    parsed: list[tuple[str, bytes]] = field(default_factory=list[tuple[str, bytes]])  # (uid, archive content)
    saved: list[MissionMeta] = field(default_factory=list[MissionMeta])
    warnings: tuple[str, ...] = ()


def make_pipeline(steps: FakeSteps) -> Pipeline:
    def parse(log: MissionLog, stats: ParseStats) -> Iterable[LogEvent]:
        content = b"".join(iter_source_bytes(log.files[0]))
        steps.parsed.append((log.mission_uid, content))
        stats.lines_total = content.count(b"\n")
        stats.lines_bad = 1
        stats.log_version = 18
        stats.unknown_atypes[99] += 2
        stats.unknown_keys["12:FOO"] += 1
        stats.warnings.append("a bad line")
        return iter(())

    def replay(events: Iterable[LogEvent]) -> MissionResult:
        return cast(MissionResult, object())

    def save(result: MissionResult, meta: MissionMeta) -> Mission:
        if meta.mission_uid in steps.fail_on:
            raise RuntimeError(f"cannot save {meta.mission_uid}")
        steps.saved.append(meta)
        mission, _ = Mission.objects.update_or_create(
            server_uid=meta.server_uid,
            mission_uid=meta.mission_uid,
            defaults={
                "mission_file": "m.Mission",
                "file_path": meta.archive_path,
                "started_at": meta.started_at,
                "ended_at": meta.started_at,
                "duration_s": 1.0,
                "game_date": "1951.9.15",
                "game_time": "13:0:0",
                "game_type": 2,
                "completed_cleanly": True,
            },
        )
        return mission

    def resolve_start(uid: str, tz: ZoneInfo, hint: datetime | None) -> ResolvedStart:
        local = datetime.strptime(uid, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=tz)
        return ResolvedStart(local.astimezone(UTC), steps.warnings)

    return Pipeline(group=fake_group, parse=parse, replay=replay, save=save, resolve_start=resolve_start)


def make_config(
    data_dir: Path,
    log_dir: Path | None,
    *,
    after_archive: AfterArchive = "move",
    remote: bool = False,
    backoff_minutes: tuple[float, ...] = (5.0, 30.0, 120.0),
) -> Config:
    return Config(
        data_dir=data_dir,
        server_uid=SERVER_UID,
        timezone_name="UTC",
        logs=LogsConfig(dir=log_dir, after_archive=after_archive, remote=remote),
        ingest=IngestConfig(
            idle_minutes=10.0,
            settle_seconds=60.0,
            stable_seconds=60.0,
            retry_backoff_minutes=backoff_minutes,
        ),
    )


