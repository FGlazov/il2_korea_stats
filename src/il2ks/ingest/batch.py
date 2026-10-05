"""Batched level 2 for long runs (roadmap "Batched level 2", doc 14 "Batched level 2").

A batch of `BATCH_MIN` or more missions (a backlog `il2ks ingest`, a long `reprocess`) saves level 1 only, mission by
mission (one transaction each, as ever), and collects what the saves touched in one `Touched` set. Level 2 for that set
is applied at every 10% of the batch; the Elo ratings, the medal holder counts and the stat thresholds run once at the
end. The end state equals the per-mission path and a full rebuild: level 2 is always recomputed from level 1 for the
touched rows, never adjusted by deltas (TD-08), so doing it later and for more missions at once changes nothing but
the cost.

Smaller batches keep the per-mission path unchanged (`ingest.persist.save_mission`).
"""

import logging

from django.db import transaction

from il2ks.core.ratings.elo import RatingRules
from il2ks.core.stat_marks import MarkRules
from il2ks.db.site import bump_data_version
from il2ks.ingest import persist
from il2ks.ingest.persist import Touched

log = logging.getLogger(__name__)

BATCH_MIN = 20
"""From this many missions to ingest or reprocess in one run, level 2 is batched."""

PROGRESS_STEPS = 10
"""Level 2 is applied every `1/PROGRESS_STEPS` of the batch (10%)."""


def is_batch(missions: int) -> bool:
    return missions >= BATCH_MIN


class Level2Batch:
    """Collects the `Touched` sets of a batch's level-1 saves and applies level 2 at every 10% and at the end.

    Usage: `add(touched)` after each committed save, `mission_done()` after each mission (saved or failed: progress
    counts missions, so a batch of failures does not stall), `finish()` at the end, also when the run is interrupted:
    what is pending is applied then (`finish` is idempotent). A hard kill (power cut, `kill -9`) loses the pending set;
    `il2ks rebuild-aggregates` repairs that (doc 14)."""

    def __init__(self, total: int, ratings: RatingRules, marks: MarkRules) -> None:
        self.total = total
        self.ratings = ratings
        self.marks = marks
        self.done = 0
        self.flushes = 0  # intermediate level-2 passes so far
        self._pending = Touched()
        self._tours: set[int] = set()  # every tour any save touched: the thresholds are rewritten for them at the end
        self._saved = 0  # level-1 saves since the last `finish`

    def add(self, touched: Touched) -> None:
        self._pending.update(touched)
        self._tours |= touched.tours
        self._saved += 1

    def mission_done(self) -> None:
        """Count one mission; apply level 2 when this crossed the next 10% line (the last line is `finish`'s)."""
        self.done += 1
        if self.done < self.total and self.done * PROGRESS_STEPS // self.total > self.flushes:
            self.flushes = self.done * PROGRESS_STEPS // self.total
            self.flush()

    def flush(self) -> None:
        """Level 2 for everything collected so far, in its own transaction. No ratings, holders or thresholds yet.
        The pending set is cleared only after the pass committed, so a failed pass is retried by the next one."""
        if not self._pending:
            return
        log.info("level 2 for %d/%d missions", self.done, self.total)
        with transaction.atomic():
            persist.apply_level2(self._pending, payload_elo=False, holders=False)
            bump_data_version()
        self._pending = Touched()

    def finish(self) -> None:
        """The last level-2 pass, then the Elo ratings, the holder counts and the thresholds, once, in one transaction.
        The ratings replay every kill in mission order, whatever order the missions were saved in."""
        if self._saved == 0:
            return
        log.info("level 2 for the last missions, then ratings and thresholds")
        with transaction.atomic():
            persist.apply_batch_end(self._pending, self._tours, self.ratings, self.marks)
            bump_data_version()
        self._pending = Touched()
        self._tours = set()
        self._saved = 0


def finish_quietly(batch: Level2Batch | None, *, failing: bool) -> None:
    """`batch.finish()` for a `finally` block: after a failure an error here is only logged."""
    if batch is None:
        return
    if not failing:
        batch.finish()
        return
    try:
        batch.finish()
    except Exception:
        log.exception("level 2 could not be finished after the failure; run `il2ks rebuild-aggregates`")
