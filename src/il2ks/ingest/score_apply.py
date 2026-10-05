"""Work off the admin's pending rule changes (`watch` calls `rescore_with_wanted` every tick): the flight-time score
option, the overrides of the game rules that change stored numbers (scoring, ratings, assists, tours) and the "new tour
after a decisive mission" switch. One rebuild for all of them."""

import logging

from django.db import transaction

from il2ks.config import Config
from il2ks.db.site import bump_data_version, clear_level2_pending
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ingest.rule_store import adopt_overrides, config_with, pending_effects, rebuild_overrides

log = logging.getLogger(__name__)


def rescore_with_wanted(cfg: Config) -> bool:
    """Under the writer lock re-score every sortie with the wanted rules and rebuild level 2 (reassigning the missions
    to tours first when a tour setting changed), then record the wanted rules as the applied ones. False when nothing
    was pending or the lock was busy (the next tick tries again). One transaction: a crash midway rolls back, leaves
    the change pending, and the next tick starts over."""
    if not pending_effects():
        return False
    try:
        with WriterLock(cfg.data_dir, "rules"):
            effects = pending_effects()
            if not effects:
                return False
            reassign = "retour" in effects
            log.info("applying the changed game rules (%s)", ", ".join(sorted(effects)))
            with transaction.atomic():
                overrides = rebuild_overrides(reassign_tours=reassign)
                run = config_with(cfg, overrides)
                rebuild_aggregates(
                    run.ratings, run.tours, reassign_tours=reassign, marks=run.marks, score=run.score, board=run.board
                )
                adopt_overrides(overrides)
                clear_level2_pending()
                bump_data_version()
    except LockBusyError as exc:
        log.info("rule change waits: %s", exc)
        return False
    return True
