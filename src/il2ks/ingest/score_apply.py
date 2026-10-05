"""Work off a pending change of the admin's flight-time score option (`watch` calls it every tick)."""

import logging

from django.db import transaction

from il2ks.config import Config
from il2ks.db.site import bump_data_version
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.flight_score import flight_score_pending
from il2ks.ingest.lock import LockBusyError, WriterLock

log = logging.getLogger(__name__)


def rescore_with_wanted(cfg: Config) -> bool:
    """Under the writer lock re-score every sortie with the wanted settings and rebuild level 2 (`rebuild_aggregates`
    adopts them as the applied ones). False when nothing was pending or the lock was busy (the next tick tries
    again). One transaction: a crash midway rolls back, leaves the change pending, and the next tick starts over."""
    if not flight_score_pending():
        return False
    try:
        with WriterLock(cfg.data_dir, "score"):
            if not flight_score_pending():
                return False
            log.info("re-scoring with the changed flight-time score settings")
            with transaction.atomic():
                rebuild_aggregates(cfg.ratings, cfg.tours, marks=cfg.marks, score=cfg.score, board=cfg.board)
                bump_data_version()
    except LockBusyError as exc:
        log.info("score recompute waits: %s", exc)
        return False
    return True
