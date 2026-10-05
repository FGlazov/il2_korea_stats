"""Batched level 2 for long runs (roadmap "Batched level 2", doc 14 "Batched level 2").

A batch of `BATCH_MIN` or more missions (a backlog `il2ks ingest`) saves level 1 only, mission by mission (one
transaction each, as ever), and tracks one thing: the ids of the tours its saves touched. Level 2 for those tours is
the full refresh every level-2 pass is (`aggregates.refresh_tours`), applied at every 10% of the batch; the Elo
ratings, the medal holder counts and the stat thresholds run once at the end. The end state equals the per-mission path
and a full rebuild: level 2 is always recomputed from level 1, never adjusted by deltas (TD-08), so doing it later and
for more missions at once changes nothing but the cost. No per-entity tracking (maintainer, 2026-10-05).

Smaller batches keep the per-mission path unchanged (`ingest.persist.save_mission`).
"""

import logging
from collections.abc import Callable, Iterable
from typing import cast

from django.db import transaction

from il2ks.core.ratings.elo import RatingRules
from il2ks.core.stat_marks import MarkRules
from il2ks.db.site import (
    bump_data_version,
    clear_level2_pending,
    level2_pending,
    set_level2_pending,
    store_level2_pending_tours,
)
from il2ks.ingest import persist

log = logging.getLogger(__name__)

BATCH_MIN = 20
"""From this many missions to ingest or reprocess in one run, level 2 is batched."""

PROGRESS_STEPS = 10
"""Level 2 is applied every `1/PROGRESS_STEPS` of the batch (10%)."""


def is_batch(missions: int) -> bool:
    return missions >= BATCH_MIN


class Level2Batch:
    """Collects the tour ids of a batch's level-1 saves and refreshes those tours at every 10% and at the end.

    Usage: `add(tour_ids)` after each save (inside its transaction: the marker then carries the tours exactly when the
    save is committed), `mission_done()` after each mission (saved or failed: progress counts missions, so a batch of
    failures does not stall), `finish()` at the end, also when the run is interrupted: what is pending is applied then
    (`finish` is idempotent). A hard kill (power cut, `kill -9`) loses the in-memory set, but the marker holds every
    tour of the batch: `repair_pending` refreshes those tours and replays the ratings (doc 14)."""

    def __init__(self, total: int, ratings: RatingRules, marks: MarkRules, command: str = "ingest") -> None:
        self.command = command
        self.started = False
        self.total = total
        self.ratings = ratings
        self.marks = marks
        self.done = 0
        self.flushes = 0  # intermediate level-2 passes so far
        self._pending: set[int] = set()  # tours touched since the last level-2 pass
        self._tours: set[int] = set()  # every tour any save touched: the thresholds are rewritten for them at the end
        self._saved = 0  # level-1 saves since the last `finish`

    def start(self) -> None:
        """Persist the "level 2 may lag" marker before the first save (a hard kill leaves it: `repair_pending`)."""
        set_level2_pending(self.command)
        self.started = True

    def add(self, tour_ids: Iterable[int]) -> None:
        ids = set(tour_ids)
        if ids - self._tours:
            self._tours |= ids
            store_level2_pending_tours(self._tours)  # one small update, only when the batch reaches a new tour
        self._pending |= ids
        self._saved += 1

    def mission_done(self) -> None:
        """Count one mission; apply level 2 when this crossed the next 10% line (the last line is `finish`'s)."""
        self.done += 1
        if self.done < self.total and self.done * PROGRESS_STEPS // self.total > self.flushes:
            self.flushes = self.done * PROGRESS_STEPS // self.total
            self.flush()

    def flush(self) -> None:
        """Level 2 for the tours touched so far, in its own transaction. No ratings, holders or thresholds yet.
        The pending set is cleared only after the pass committed, so a failed pass is retried by the next one."""
        if not self._pending:
            return
        log.info("level 2 for %d/%d missions", self.done, self.total)
        with transaction.atomic():
            persist.apply_level2(self._pending, payload_elo=False, holders=False)
            bump_data_version()
        self._pending = set()

    def finish(self) -> None:
        """The last level-2 pass, then the Elo ratings, the holder counts and the thresholds, once, in one transaction.
        The ratings replay every kill in mission order, whatever order the missions were saved in."""
        if self._saved == 0:
            if self.started:
                self.started = False
                clear_level2_pending()  # nothing was saved: nothing lags
            return
        log.info("level 2 for the last missions, then ratings and thresholds")
        with transaction.atomic():
            persist.apply_batch_end(self._pending, self._tours, self.ratings, self.marks)
            bump_data_version()
            clear_level2_pending()  # in the same transaction: either both happen or the marker stays
        self.started = False
        self._pending = set()
        self._tours = set()
        self._saved = 0


def repair_pending(rebuild: Callable[[], None], ratings: RatingRules, marks: MarkRules) -> bool:
    """Call first thing in a run that holds the writer lock: if an earlier batched run was killed (its marker is still
    set), level 2 and the Elo ratings lag level 1, and re-running skips those missions as unchanged. When the marker
    names the batch's tours, refresh those and replay the ratings; otherwise (a reprocess, an old marker) rebuild level
    2 from level 1 (`rebuild`). Either way the marker is cleared. Returns whether it repaired anything."""
    marker = level2_pending()
    if not marker:
        return False
    raw = marker.get("tours")
    listed = cast("list[object]", raw) if isinstance(raw, list) else []
    tours = sorted(t for t in listed if isinstance(t, int))
    log.warning(
        "a batched %s run started %s (pid %s) never finished: %s first",
        marker.get("command", "ingest"),
        marker.get("since", "?"),
        marker.get("pid", "?"),
        f"refreshing {len(tours)} tours and the ratings" if tours else "rebuilding level 2 and the ratings",
    )
    with transaction.atomic():
        if tours:
            persist.apply_level2(tours, payload_elo=False, holders=False)
            persist.finish_batch(tours, ratings, marks)
            bump_data_version()
        else:
            rebuild()
        clear_level2_pending()
    return True


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
