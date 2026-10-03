"""MissionResult -> level-1 rows, plus the incremental level-2 update (FR-ING-5, FR-ING-6, FR-ING-9, TD-08).

CONTRACT STUB: the signatures are fixed, the bodies are iteration 1 work.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from il2ks.core.catalog.loader import Catalog
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import Mission


@dataclass(frozen=True, slots=True)
class MissionMeta:
    """What `ingest` knows about a mission besides the replay result."""

    server_uid: uuid.UUID
    mission_uid: str  # file name timestamp
    started_at: datetime  # UTC, resolved from the local-time file name (TD-15)
    archive_path: str


def save_mission(result: MissionResult, meta: MissionMeta, catalog: Catalog) -> Mission:
    """Upsert one mission's level-1 rows by natural key and update level-2 totals incrementally.

    Must run inside the caller's `transaction.atomic()`. Safe to call again for the same mission (re-ingest,
    reprocess): the mission's old contribution is subtracted from level 2 first, rows that no longer exist are deleted,
    and PKs of rows that still exist are kept (FR-ING-9, FR-WEB-13).
    """
    raise NotImplementedError
