"""The admin's "reprocess all missions" request (doc 14): filed by the web, worked off by a `watch` tick under the
writer lock, with the status page and the permissions around it."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.utils import timezone

from il2ks.config import Config
from il2ks.db.models import ReprocessRequest, ReprocessStatus
from il2ks.db.reprocess_requests import AlreadyPendingError, request_reprocess
from il2ks.ingest.lock import WriterLock
from il2ks.ingest.reprocess import ReprocessSummary, reprocess
from il2ks.ingest.reprocess_requests import fail_interrupted_requests, run_pending_request
from il2ks.ingest.runner import Pipeline
from il2ks.ingest.watch import watch
from tests.integration.test_ingest_runner import Env, fake_work, threads

pytestmark = pytest.mark.django_db

UIDS = ["2026-04-01_10-00-00", "2026-04-02_10-00-00", "2026-05-01_10-00-00"]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    env = Env(tmp_path)
    for uid in UIDS:
        env.add(uid)
    env.ingest()
    return env


def fast(cfg: Config) -> Config:
    return replace(cfg, ingest=replace(cfg.ingest, watch_interval_s=0.01))


def fake_reprocess(
    cfg: Config,
    pipeline: Pipeline,
    /,
    *,
    since: date | None,
    until: date | None,
    on_start: Callable[[int], None],
    on_progress: Callable[[ReprocessSummary], None],
) -> ReprocessSummary:
    """The real reprocess with thread workers, a fake replay and no aggregate rebuild."""
    return reprocess(
        cfg,
        pipeline,
        since=since,
        until=until,
        workers=2,
        work=fake_work,
        executor_factory=threads,
        rebuild=lambda: None,
        on_start=on_start,
        on_progress=on_progress,
    )


def tick(env: Env, fn: Callable[..., ReprocessSummary] = fake_reprocess) -> None:
    watch(
        fast(env.cfg),
        env.pipeline,
        max_ticks=1,
        now=env.now,
        reprocess_pipeline=lambda: env.pipeline,
        reprocess_fn=fn,
    )


# --- the queue ---


def test_a_request_is_pending_and_only_one_can_wait() -> None:
    first = request_reprocess("boss", timezone.now())

    assert (first.status, first.requested_by, first.is_all) == (ReprocessStatus.PENDING, "boss", True)
    with pytest.raises(AlreadyPendingError):
        request_reprocess("someone else", timezone.now())
    assert ReprocessRequest.objects.count() == 1


def test_a_new_request_is_possible_once_the_pending_one_has_started() -> None:
    ReprocessRequest.objects.update_or_create(
        status=ReprocessStatus.RUNNING, defaults={"requested_at": timezone.now(), "started_at": timezone.now()}
    )

    assert request_reprocess("boss", timezone.now()).status == ReprocessStatus.PENDING


# --- the watch tick ---


def test_a_watch_tick_picks_the_request_up_and_records_the_result(env: Env) -> None:
    request = request_reprocess("boss", env.now())
    seen: list[tuple[str, int, int]] = []

    def spying(
        cfg: Config,
        pipeline: Pipeline,
        /,
        *,
        since: date | None,
        until: date | None,
        on_start: Callable[[int], None],
        on_progress: Callable[[ReprocessSummary], None],
    ) -> ReprocessSummary:
        def watching(summary: ReprocessSummary) -> None:
            row = ReprocessRequest.objects.get(pk=request.pk)
            seen.append((row.status, row.missions_total, row.missions_ok))  # while it runs
            on_progress(summary)

        return fake_reprocess(cfg, pipeline, since=since, until=until, on_start=on_start, on_progress=watching)

    tick(env, spying)

    row = ReprocessRequest.objects.get(pk=request.pk)
    assert row.status == ReprocessStatus.DONE
    assert (row.missions_total, row.missions_ok, row.missions_failed, row.missions_missing) == (3, 3, 0, 0)
    assert row.started_at is not None
    assert row.finished_at is not None
    assert row.error == ""
    assert seen
    assert all(status == "running" and total == 3 for status, total, _ in seen)
    assert [ok for _, _, ok in seen] == [0, 1, 2]  # each finished mission is written before the next one reports
    assert request_reprocess("boss", env.now())  # the queue is free again


def test_a_busy_writer_lock_leaves_the_request_pending_until_the_next_tick(env: Env) -> None:
    request = request_reprocess("boss", env.now())

    with WriterLock(env.data, "ingest"):
        tick(env)
    row = ReprocessRequest.objects.get(pk=request.pk)
    assert (row.status, row.started_at) == (ReprocessStatus.PENDING, None)

    tick(env)
    assert ReprocessRequest.objects.get(pk=request.pk).status == ReprocessStatus.DONE


def test_a_request_with_a_date_span_only_reprocesses_that_span(env: Env) -> None:
    request = request_reprocess("boss", env.now(), since=date(2026, 4, 1), until=date(2026, 4, 30))

    tick(env)

    row = ReprocessRequest.objects.get(pk=request.pk)
    assert (row.status, row.missions_total, row.missions_ok) == (ReprocessStatus.DONE, 2, 2)
    assert not row.is_all


def test_missions_that_fail_are_counted_and_the_request_is_still_done(env: Env) -> None:
    request = request_reprocess("boss", env.now())

    def broken(uid: str, archive: Path, rules: object) -> object:
        raise RuntimeError("replay bug")

    def fn(
        cfg: Config,
        pipeline: Pipeline,
        /,
        *,
        since: date | None,
        until: date | None,
        on_start: Callable[[int], None],
        on_progress: Callable[[ReprocessSummary], None],
    ) -> ReprocessSummary:
        return reprocess(
            cfg,
            pipeline,
            since=since,
            until=until,
            workers=1,
            work=broken,  # pyright: ignore[reportArgumentType]
            executor_factory=threads,
            rebuild=lambda: None,
            on_start=on_start,
            on_progress=on_progress,
        )

    tick(env, fn)

    row = ReprocessRequest.objects.get(pk=request.pk)
    assert (row.status, row.missions_ok, row.missions_failed) == (ReprocessStatus.DONE, 0, 3)


def test_a_crashing_job_marks_the_request_failed_and_the_loop_goes_on(env: Env) -> None:
    request = request_reprocess("boss", env.now())

    def crash(*args: object, **kwargs: object) -> ReprocessSummary:
        raise RuntimeError("disk on fire")

    tick(env, crash)

    row = ReprocessRequest.objects.get(pk=request.pk)
    assert row.status == ReprocessStatus.FAILED
    assert "disk on fire" in row.error
    assert row.finished_at is not None


def test_a_request_left_running_by_a_dead_process_is_failed_when_watch_starts(env: Env) -> None:
    stale = ReprocessRequest.objects.create(
        requested_at=env.now() - timedelta(hours=1), status=ReprocessStatus.RUNNING, started_at=env.now()
    )

    tick(env)

    row = ReprocessRequest.objects.get(pk=stale.pk)
    assert row.status == ReprocessStatus.FAILED
    assert "interrupted" in row.error
    assert fail_interrupted_requests(env.now()) == 0


def test_without_a_request_the_tick_does_nothing(env: Env) -> None:
    assert run_pending_request(env.cfg, lambda: env.pipeline, reprocess_fn=fake_reprocess, now=env.now) is None


# --- the admin ---


@pytest.fixture
def boss(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def test_the_status_page_offers_the_action_and_the_confirmation_asks_first(boss: Client) -> None:
    page = boss.get("/admin/ingestion/").content.decode()
    assert "/admin/ingestion/reprocess/" in page

    confirm = boss.get("/admin/ingestion/reprocess/")
    assert confirm.status_code == 200
    assert "Yes, reprocess all missions" in confirm.content.decode()
    assert ReprocessRequest.objects.count() == 0  # a GET never files anything


def test_confirming_files_one_pending_request_and_the_status_page_shows_it(boss: Client) -> None:
    response = boss.post("/admin/ingestion/reprocess/")

    assert response.status_code == 302
    assert response["Location"] == "/admin/ingestion/"
    (row,) = ReprocessRequest.objects.all()
    assert (row.status, row.requested_by, row.is_all) == (ReprocessStatus.PENDING, "boss", True)

    page = boss.get("/admin/ingestion/").content.decode()
    assert "starts at the next watch tick" in page
    assert "/admin/ingestion/reprocess/" not in page  # no second request while one waits

    boss.post("/admin/ingestion/reprocess/")  # a double click, or a stale tab
    assert ReprocessRequest.objects.count() == 1
    assert boss.get("/admin/ingestion/reprocess/").status_code == 302


def test_the_status_page_shows_progress_and_the_last_result(boss: Client) -> None:
    ReprocessRequest.objects.create(
        requested_at=timezone.now(),
        requested_by="boss",
        status=ReprocessStatus.DONE,
        finished_at=timezone.now(),
        missions_ok=40,
        missions_failed=2,
        missions_missing=1,
    )
    ReprocessRequest.objects.create(
        requested_at=timezone.now(),
        requested_by="boss",
        status=ReprocessStatus.RUNNING,
        started_at=timezone.now(),
        missions_total=50,
        missions_ok=7,
    )

    page = boss.get("/admin/ingestion/").content.decode()

    assert "7 of 50 missions done" in page
    assert "40 missions reprocessed, 2 failed, 1 without an archive" in page


def test_staff_without_the_permission_cannot_request_a_reprocess(client: Client) -> None:
    clerk = User.objects.create_user("clerk", password="x", is_staff=True)
    clerk.user_permissions.add(Permission.objects.get(codename="view_ingestrun"))
    client.force_login(clerk)

    assert "/admin/ingestion/reprocess/" not in client.get("/admin/ingestion/").content.decode()
    assert client.get("/admin/ingestion/reprocess/").status_code == 403
    assert client.post("/admin/ingestion/reprocess/").status_code == 403
    assert ReprocessRequest.objects.count() == 0


def test_staff_granted_the_permission_can(client: Client) -> None:
    ops = User.objects.create_user("ops", password="x", is_staff=True)
    ops.user_permissions.add(
        Permission.objects.get(codename="view_ingestrun"), Permission.objects.get(codename="add_reprocessrequest")
    )
    client.force_login(ops)

    assert client.post("/admin/ingestion/reprocess/").status_code == 302
    assert ReprocessRequest.objects.get().requested_by == "ops"


def test_anonymous_and_non_staff_users_are_sent_to_the_login(client: Client) -> None:
    assert "/admin/login/" in client.post("/admin/ingestion/reprocess/")["Location"]
    client.force_login(User.objects.create_user("player", password="x"))
    assert "/admin/login/" in client.post("/admin/ingestion/reprocess/")["Location"]
    assert ReprocessRequest.objects.count() == 0
