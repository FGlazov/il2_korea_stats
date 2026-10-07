""" "Delete all data and reprocess" (roadmap 0.2.0; admin button on the ingestion status page, CLI `--all --wipe`).

Back up, delete every ingested row (missions, sorties, players, tours and everything derived from them), then reprocess
every archived mission, so the tours are cut again from the current tour settings. The admin's settings survive.

**What counts as an admin setting** is decided in ONE place, `KEEP_MODELS` below. Every model of the `il2ks_db` app that
is not listed is wiped, and every model of another app (Django's auth, sessions, admin log, django-axes, ...) is never
touched. `tests/integration/test_wipe.py` fails when a model exists that is neither in `KEEP_MODELS` nor in the test's
list of wiped ones: whoever adds a model has to decide which kind it is (a new admin-settings model, for example `Page`,
goes into `KEEP_MODELS`).

Settings that live ON an ingested row cannot survive the deletion of the row, so they are captured by a stable key
and saved to disk before anything is deleted (`ingest.wipe_state`: durable across a crash). The rows that come back
are hidden at creation (no moment when a hidden player or mission is public) and get their old primary keys, so the
public URLs keep working; at the end the marks are applied once more (`AdminMarks`):
- `Player.is_hidden`: by game account UUID;
- `Mission.is_hidden`: by (server UID, mission UID), the log file's timestamp;
- a tour's title (an admin's rename): by its start, when the new tour has the same start, mode and kind (a part cut
  by a decisive mission or not);
- manual mode's tour boundaries (their starts and titles): the tours of mode `manual` that are not `by_win` are
  recreated before the reprocess when the effective mode is still manual, because in manual mode the stored tours ARE
  the periods.
A wipe that is killed half way leaves the saved file: `watch` and the next `reprocess` pick it up
(`resume_unfinished_wipe`; rows that are back are left alone, the manual boundaries missing are recreated).
Primary keys kept: players, missions, sorties and tours with the same start; a tour the current settings cut
differently gets a new key.
The admin's chosen game rules, achievement settings and flight-time option are adopted as applied before the wipe
(a full rebuild computes with them anyway), so the new rows follow what is chosen now.
"""

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal, cast

from django.apps import apps
from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.db.models import F

from il2ks.config import Config
from il2ks.core.tours import TourRules
from il2ks.db.models import IngestRun, Mission, Player, SiteSettings, Tour
from il2ks.db.site import bump_data_version, get_site_settings
from il2ks.ingest import wipe_state
from il2ks.ingest.achievements import adopt_wanted_rules, recompute_holders
from il2ks.ingest.activity import day_of, recompute_days
from il2ks.ingest.archive import archive_matches
from il2ks.ingest.flight_score import adopt_wanted_flight_score
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ingest.reprocess import ReprocessSummary, WorkFn, default_executor, reprocess
from il2ks.ingest.rule_store import adopt_overrides, effective_config, rebuild_overrides
from il2ks.ingest.runner import Pipeline
from il2ks.ingest.wipe_state import AdminMarks, WipeState
from il2ks.ingest.worker import parse_and_replay

log = logging.getLogger(__name__)

APP_LABEL = "il2ks_db"

KEEP_MODELS: Mapping[str, str] = {
    "SiteSettings": "branding, quips, achievements, game rules, flight-time and tour options, picture names",
    "NavLink": "the navigation links",
    "Page": "the admin's Markdown pages of the navigation",
    "PageTranslation": "the pages' texts in other languages",
    "DataVersion": "the page-cache counter (bumped by the wipe)",
    "GameObject": "the catalog and the admin's name overrides (`name_overridden`)",
    "Country": "the catalog and the admin's country names",
    "IngestRun": "the history that tells `reprocess` which archive to use (its `mission` link is cleared)",
    "ReprocessRequest": "the running job's own row",
    "LiveMission": "the running mission as `watch` saw it (not an ingested row)",
    "LivePlayer": "online now (its `player` link is cleared)",
}
"""The `il2ks_db` models the wipe keeps, each with the reason. Everything else in that app is deleted. A model that
arrives with another feature and holds admin settings (a `Page`, for example) is added HERE."""


class WipePhase(StrEnum):
    CHECK = "check"
    BACKUP = "backup"
    WIPE = "wipe"
    REPROCESS = "reprocess"


class WipeError(Exception):
    """The wipe did not start (nothing was deleted): no backup, or missions without a usable archive."""


type BackupFn = Callable[[Config], Path]


def wiped_models() -> list[type[models.Model]]:
    """The models to delete, children first (a row is deleted before the rows it points to)."""
    wiped = [m for m in apps.get_app_config(APP_LABEL).get_models() if m.__name__ not in KEEP_MODELS]
    wiped_set = set(wiped)
    pointed_at: dict[type[models.Model], set[type[models.Model]]] = defaultdict(set)  # model -> models pointing to it
    for model in wiped:
        for field in model._meta.concrete_fields:
            if not isinstance(field, models.ForeignKey):
                continue
            target = cast(type[models.Model], field.related_model)  # pyright: ignore[reportUnknownMemberType]
            if target in wiped_set and target is not model:
                pointed_at[target].add(model)
    order: list[type[models.Model]] = []
    remaining = set(wiped)
    while remaining:
        ready = sorted((m for m in remaining if not (pointed_at[m] & remaining)), key=lambda m: m.__name__)
        if not ready:
            raise WipeError(
                "the models to wipe point at each other in a circle: " + ", ".join(m.__name__ for m in remaining)
            )
        order.extend(ready)
        remaining -= set(ready)
    return order


def _clear_links_into(wiped: list[type[models.Model]]) -> None:
    """Rows that stay and point at rows that go (IngestRun.mission, LivePlayer.player, ...): set the link to NULL, as
    `on_delete=SET_NULL` would. A kept model with a required link into a wiped one cannot be kept."""
    wiped_set = set(wiped)
    for model in apps.get_models():
        if model in wiped_set:
            continue
        for field in model._meta.concrete_fields:
            if isinstance(field, models.ForeignKey) and field.related_model in wiped_set:  # pyright: ignore[reportUnknownMemberType]
                if not field.null:
                    raise WipeError(f"{model.__name__}.{field.name} must allow NULL: it points at a wiped model")
                model._default_manager.exclude(**{f"{field.name}__isnull": True}).update(**{field.name: None})


def wipe_ingested_data(order: list[type[models.Model]] | None = None) -> dict[str, int]:
    """Delete every row of every wiped model in one transaction; returns the rows deleted per model."""
    order = wiped_models() if order is None else order
    deleted: dict[str, int] = {}
    with transaction.atomic():
        _clear_links_into(order)
        for model in order:
            rows = cast("models.QuerySet[models.Model]", model._default_manager.all())
            deleted[model.__name__] = rows._raw_delete(using=DEFAULT_DB_ALIAS)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        bump_data_version()
    return deleted


def restore_manual_boundaries(marks: AdminMarks, mode: str, label: str) -> int:
    """Manual mode: the boundaries are the tours, so recreate them (empty) before the missions come back."""
    if mode != "manual":
        return 0
    boundaries = [t for t in marks.tours if t.mode == "manual" and not t.by_win]
    have = set(Tour.objects.values_list("started_at", flat=True))
    with transaction.atomic():
        for index, mark in enumerate(boundaries):
            if mark.started_at in have:
                continue  # a resume after the first run got that far
            following = boundaries[index + 1].started_at if index + 1 < len(boundaries) else None
            Tour.objects.create(title=mark.title, started_at=mark.started_at, ended_at=following, mode=label)
    return len(boundaries)


def restore_marks(marks: AdminMarks) -> None:
    """Apply the captured settings to the rows that exist now (the reprocess is over). A key without a row is dropped:
    the mission has no archive, or the player never flew in an archived mission."""
    with transaction.atomic():
        players = Player.objects.filter(account_uuid__in=marks.hidden_players, is_hidden=False)
        players.update(is_hidden=True)
        by_uid: dict[str, set[str]] = defaultdict(set)
        for server, uid in marks.hidden_missions:
            by_uid[uid].add(server)
        days: set[date] = set()
        for mission in Mission.objects.filter(mission_uid__in=list(by_uid), is_hidden=False):
            if str(mission.server_uid) in by_uid[mission.mission_uid]:
                Mission.objects.filter(pk=mission.pk).update(is_hidden=True)
                days.add(day_of(mission.started_at))
        recompute_days(days)
        recompute_holders()  # the medal counts cover visible players only (FR-WEB-26)
        _relink_runs(marks.runs)
        wanted = {(t.started_at, t.mode, t.by_win): t.title for t in marks.tours}
        for tour in Tour.objects.all():
            title = wanted.get((tour.started_at, tour.mode, tour.by_win))
            if title is not None and title != tour.title:
                Tour.objects.filter(pk=tour.pk).update(title=title)
        bump_data_version()


def _relink_runs(runs: tuple[tuple[int, str, str], ...]) -> None:
    """The ingest history kept its runs while their missions were deleted (the link was cleared): point each at the
    rebuilt mission of the same server and mission UID again, so the status page and `Mission.ingest_runs` show the
    whole history. A run whose mission has not come back stays unlinked."""
    by_key = {
        (str(server), uid): pk for pk, server, uid in Mission.objects.values_list("pk", "server_uid", "mission_uid")
    }
    for run_pk, server, uid in runs:
        mission_pk = by_key.get((server, uid))
        if mission_pk is not None:
            IngestRun.objects.filter(pk=run_pk, mission__isnull=True).update(mission_id=mission_pk)


type ResumeResult = Literal["none", "resumed", "busy"]


def resume_unfinished_wipe(cfg: Config) -> ResumeResult:
    """A wipe that never finished left its marks and keys on disk. Activate them (the rows that come back later are
    hidden, named and keyed at creation) and bring back what the killed run did not: the manual tour boundaries and
    the links of the ingest history. Nothing the admin changed since is touched: the rows that are back already keep
    what they have. Returns "none" (no saved wipe), "resumed", or "busy" when the writer lock is taken (the caller
    tries again on a later tick)."""
    if not wipe_state.pending(cfg):
        return "none"
    try:
        with WriterLock(cfg.data_dir, "watch", wait=0):
            state = wipe_state.resume(cfg)
            if state is not None:
                restore_missing_boundaries(effective_config(cfg).tours, state.marks)
                apply_resume_marks(state.marks)
                _clear_if_complete(cfg, state)
    except LockBusyError:
        log.info("a saved wipe is waiting; it is resumed on the first tick that gets the writer lock")
        return "busy"
    return "resumed"


def restore_missing_boundaries(rules: TourRules, marks: AdminMarks) -> None:
    """The first run died after the wipe, before the manual boundaries were recreated: recreate those missing."""
    restore_manual_boundaries(marks, rules.mode, rules.label)


def apply_resume_marks(marks: AdminMarks) -> None:
    """What a resume applies: the run links only. Hiding and tour names were given to the rows when they were created
    (`wipe_state` receivers), so a player the admin unhid or a tour the admin renamed since stays as it is."""
    with transaction.atomic():
        _relink_runs(marks.runs)
        bump_data_version()


def finish_resumed(cfg: Config, *, whole: bool = True) -> None:
    """The end of a plain reprocess or ingest that found the saved state of an unfinished wipe. A reprocess of
    everything is the end of the wipe: the state is dropped even when some mission never came back (no usable
    archive, or it keeps failing) and the keys not used are logged. A narrowed one keeps the state while missions
    are missing."""
    state = wipe_state.active()
    if state is not None:
        apply_resume_marks(state.marks)
        _clear_if_complete(cfg, state, force=whole)


def _clear_if_complete(cfg: Config, state: WipeState, *, force: bool = False) -> None:
    """The saved state is done with when every mission the wipe deleted is back (or `force`)."""
    missing = wipe_state.unfinished_missions(state)
    if missing == 0 or force:
        wipe_state.clear(cfg)
        if missing:
            log.warning(
                "the interrupted wipe is over; %d mission(s) never came back, their saved keys are dropped", missing
            )
        else:
            log.info("the interrupted wipe is complete: its saved state is removed")


def adopt_current_settings() -> None:
    """The new rows follow what the admin has chosen now: every wanted rule, tour option, achievement setting and
    flight-time option becomes the applied one (a full rebuild computes with them anyway)."""
    with transaction.atomic():
        get_site_settings()
        adopt_overrides(rebuild_overrides(reassign_tours=True))
        SiteSettings.objects.filter(pk=1).update(tour_on_win_applied=F("tour_on_win"))
        adopt_wanted_rules()
        adopt_wanted_flight_score()


def check_archives(cfg: Config, targets: Mapping[str, IngestRun]) -> None:
    """Nothing is deleted unless every stored mission can be built again: its archive file is there and unchanged."""
    problems: list[str] = []
    for uid in Mission.objects.values_list("mission_uid", flat=True).distinct():
        run = targets.get(uid)
        if run is None or not archive_matches(cfg.data_dir / run.archive_path, run.archive_sha256):
            problems.append(uid)
    if problems:
        shown = ", ".join(sorted(problems)[:5]) + (" ..." if len(problems) > 5 else "")
        raise WipeError(f"{len(problems)} stored mission(s) have no archive, so nothing was deleted: {shown}")


def default_backup(cfg: Config) -> Path:
    from il2ks.ops.backup import create_backup

    return create_backup(cfg, "pre-wipe")


def wipe_and_reprocess(
    cfg: Config,
    pipeline: Pipeline,
    /,
    *,
    backup: BackupFn = default_backup,
    on_phase: Callable[[WipePhase], None] | None = None,
    on_start: Callable[[int], None] | None = None,
    on_progress: Callable[[ReprocessSummary], None] | None = None,
    workers: int | None = None,
    lock_wait: float | None = None,
    work: WorkFn = parse_and_replay,
    executor_factory: Callable[[int], Executor] = default_executor,
) -> ReprocessSummary:
    """Delete everything ingested and reprocess every archived mission, under the writer lock (one hold for both).

    Raises `WipeError` (nothing deleted) when a stored mission has no archive or the backup fails; the backup comes
    first and its failure aborts. The settings captured before the wipe are applied again at the end, also when the
    reprocess itself failed half way (whatever was rebuilt keeps the admin's hiding and tour names)."""

    def no_phase(_step: WipePhase) -> None:
        return None

    phase = on_phase or no_phase
    state: dict[str, AdminMarks] = {}  # the marks, once captured

    def prepare(targets: Mapping[str, IngestRun]) -> None:
        phase(WipePhase.CHECK)
        check_archives(cfg, targets)
        phase(WipePhase.BACKUP)
        try:
            path = backup(cfg)
        except Exception as exc:
            raise WipeError(f"the backup failed, so nothing was deleted: {exc}") from exc
        log.info("backup before the wipe: %s", path)
        phase(WipePhase.WIPE)
        order = wiped_models()  # first: a model cycle must be found before anything is changed
        adopt_current_settings()
        captured = wipe_state.capture_state()
        earlier = wipe_state.load(cfg)  # a wipe that never finished: keep what it saved
        saved = wipe_state.merge(earlier, captured) if earlier is not None else captured
        wipe_state.save(cfg, saved)  # durable before the first row goes
        wipe_state.activate(saved, cfg.data_dir)
        state["marks"] = saved.marks
        rules = effective_config(cfg).tours
        deleted = wipe_ingested_data(order)
        log.info("wiped %d rows in %d tables", sum(deleted.values()), len(deleted))
        restore_manual_boundaries(saved.marks, rules.mode, rules.label)
        phase(WipePhase.REPROCESS)

    def finish() -> None:
        if "marks" in state:
            restore_marks(state["marks"])

    result = reprocess(
        cfg,
        pipeline,
        workers=workers,
        lock_wait=lock_wait,
        on_start=on_start,
        on_progress=on_progress,
        prepare=prepare,
        finish=finish,
        after=lambda: wipe_state.clear(cfg),  # under the writer lock: no other process can load the file meanwhile
        work=work,
        executor_factory=executor_factory,
    )
    return result
