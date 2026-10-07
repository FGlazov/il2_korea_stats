"""Review #2 (2026-10-07), M1 M2 M3 L1 L2: what the saved wipe state does after an interrupted wipe."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from il2ks.config import Config
from il2ks.core.tours import TourRules
from il2ks.db.models import Mission, Player, PlayerSortie, Tour
from il2ks.exitcodes import EXIT_FAILED
from il2ks.ingest import wipe, wipe_state
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.runner import default_pipeline
from il2ks.ingest.tours import retour, start_manual_tour
from il2ks.ingest.watch import watch
from il2ks.ingest.wipe import wipe_and_reprocess
from il2ks.ingest.wipe_state import AdminMarks, TourMark, WipeState
from tests.integration.test_ingest_runner import threads
from tests.integration.test_wipe import FIXTURES, _ingest  # pyright: ignore[reportPrivateUsage]
from tests.integration.test_wipe_marks import _hide_some, _killed_wipe  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_active_wipe_state() -> Iterator[None]:
    yield
    wipe_state.deactivate()


# --- M1: an admin's change after the interruption is never reverted ---


def test_an_unhide_after_an_interrupted_wipe_is_not_reverted_by_the_next_reprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    accounts, _uids = _hide_some(FIXTURES["typical"], [FIXTURES["typical"]])
    _killed_wipe(cfg, monkeypatch)
    wipe_state.deactivate()  # the process died
    Player.objects.filter(account_uuid__in=accounts).update(is_hidden=False)  # the admin unhides
    Mission.objects.update(is_hidden=False)

    reprocess(cfg, default_pipeline(cfg, defer_ratings=True), executor_factory=threads, workers=1)

    assert not Player.objects.filter(account_uuid__in=accounts, is_hidden=True).exists()
    assert not Mission.objects.filter(is_hidden=True).exists()


def test_an_unhide_after_an_interrupted_wipe_is_not_reverted_at_watch_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    accounts, _uids = _hide_some(FIXTURES["typical"], [FIXTURES["typical"]])
    _killed_wipe(cfg, monkeypatch)
    wipe_state.deactivate()
    Player.objects.filter(account_uuid__in=accounts).update(is_hidden=False)

    watch(cfg, default_pipeline(cfg), max_ticks=1)

    assert not Player.objects.filter(account_uuid__in=accounts, is_hidden=True).exists()


def test_a_tour_rename_after_an_interrupted_wipe_is_not_reverted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    Tour.objects.update(title="The September tour")
    _killed_wipe(cfg, monkeypatch)
    wipe_state.deactivate()
    Tour.objects.update(title="Renamed later")

    reprocess(cfg, default_pipeline(cfg, defer_ratings=True), executor_factory=threads, workers=1)

    assert {t.title for t in Tour.objects.all()} == {"Renamed later"}


def test_the_state_is_dropped_after_a_resumed_reprocess_even_when_a_mission_never_comes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    _killed_wipe(cfg, monkeypatch)
    state = wipe_state.load(cfg)
    assert state is not None
    state.missions[("11111111-2222-3333-4444-555555555555", "2020-01-01_00-00-00")] = 99999  # fails for good
    wipe_state.save(cfg, state)
    wipe_state.deactivate()

    reprocess(cfg, default_pipeline(cfg, defer_ratings=True), executor_factory=threads, workers=1)

    assert not wipe_state.pending(cfg)
    assert wipe_state.active() is None


def test_a_narrowed_resumed_reprocess_keeps_the_state_while_missions_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    _killed_wipe(cfg, monkeypatch)
    wipe_state.deactivate()
    missing = set(FIXTURES.values()) - set(Mission.objects.values_list("mission_uid", flat=True))

    reprocess(
        cfg,
        default_pipeline(cfg, defer_ratings=True),
        mission_uids=[next(iter(missing))],
        executor_factory=threads,
        workers=1,
    )

    assert wipe_state.pending(cfg)


# --- M2 ---


def test_a_state_removed_by_another_process_is_dropped_from_this_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    _killed_wipe(cfg, monkeypatch)
    assert wipe_state.active() is not None
    wipe_state.state_path(cfg).unlink()

    assert wipe_state.resume(cfg) is None
    assert wipe_state.active() is None


def test_the_state_is_cleared_while_the_writer_lock_is_still_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    held: list[bool] = []
    real_clear = wipe_state.clear

    def clear(c: Config) -> None:
        try:
            with WriterLock(c.data_dir, "probe", wait=0):
                held.append(False)
        except LockBusyError:
            held.append(True)
        real_clear(c)

    monkeypatch.setattr(wipe_state, "clear", clear)
    wipe_and_reprocess(
        cfg,
        default_pipeline(cfg, defer_ratings=True),
        backup=lambda _cfg: Path("fake-backup.zip"),
        executor_factory=threads,
        workers=1,
    )

    assert held == [True]
    assert not wipe_state.pending(cfg)


# --- M3 ---


def _state(
    players: dict[str, int] | None = None,
    hidden: set[str] | None = None,
    tours: tuple[TourMark, ...] = (),
) -> WipeState:
    marks = AdminMarks(frozenset(hidden or ()), frozenset(), tours)
    return WipeState(marks, players or {})


T1 = datetime(2026, 9, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 1, tzinfo=UTC)


def test_merge_keeps_the_titles_of_tours_that_are_not_back_yet() -> None:
    old = _state(tours=(TourMark(T1, T2, "Opening", "manual", False), TourMark(T2, None, "Second", "manual", False)))
    new = _state(tours=(TourMark(T2, None, "Renamed", "manual", False),))

    merged = wipe_state.merge(old, new)

    titles = {(t.started_at, t.mode, t.by_win): t.title for t in merged.marks.tours}
    assert titles == {(T1, "manual", False): "Opening", (T2, "manual", False): "Renamed"}


def test_merge_keys_a_tour_by_start_mode_and_kind() -> None:
    old = _state(tours=(TourMark(T1, None, "Part", "monthly", True),))
    new = _state(tours=(TourMark(T1, None, "Tour", "monthly", False),))

    merged = wipe_state.merge(old, new)

    assert sorted(t.title for t in merged.marks.tours) == ["Part", "Tour"]


def test_merge_does_not_hide_again_what_the_admin_unhid_after_the_first_wipe() -> None:
    old = _state(players={"a": 1, "b": 2}, hidden={"a", "b"})
    new = _state(players={"a": 1}, hidden=set())  # "a" is back and visible now; "b" is still missing

    merged = wipe_state.merge(old, new)

    assert merged.marks.hidden_players == {"b"}


def test_a_wipe_killed_before_the_manual_boundaries_were_restored_gets_them_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path, TourRules(mode="manual"))
    second = start_manual_tour(datetime(2026, 9, 3, 0, 0, tzinfo=UTC), title="Second tour")
    Tour.objects.filter(started_at__lt=second.started_at).update(title="Opening tour")
    retour(cfg.tours)

    def die(*_args: object, **_kwargs: object) -> int:
        raise SystemExit("killed between the wipe and the boundaries")

    monkeypatch.setattr(wipe, "restore_manual_boundaries", die)
    with pytest.raises(SystemExit):
        wipe_and_reprocess(
            cfg,
            default_pipeline(cfg, defer_ratings=True),
            backup=lambda _cfg: Path("fake-backup.zip"),
            executor_factory=threads,
            workers=1,
        )
    monkeypatch.undo()
    wipe_state.deactivate()
    assert not Tour.objects.exists()

    reprocess(cfg, default_pipeline(cfg, defer_ratings=True), executor_factory=threads, workers=1)

    assert sorted(Tour.objects.values_list("title", flat=True)) == ["Opening tour", "Second tour"]
    assert not PlayerSortie.objects.filter(tour__isnull=True).exists()


# --- L1 ---


def test_restore_removes_a_pending_wipe_state(tmp_path: Path) -> None:
    from il2ks.ops import backup
    from tests.ops_helpers import make_instance

    cfg = make_instance(tmp_path)
    assert cfg.source is not None
    zip_path = backup.create_backup(cfg, now=lambda: T1)
    wipe_state.save(cfg, _state(players={"a": 1}))
    wipe_state.activate(_state(players={"a": 1}), cfg.data_dir)

    backup.restore_backup(cfg, zip_path, cfg.source)

    assert not wipe_state.pending(cfg)
    assert wipe_state.active() is None


def test_db_copy_refuses_while_a_wipe_state_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from il2ks.cli import main
    from tests.ops_helpers import make_instance

    cfg = make_instance(tmp_path)
    assert cfg.source is not None
    wipe_state.save(cfg, _state(players={"a": 1}))
    copied: list[str] = []
    monkeypatch.setattr("il2ks.db.copy.copy_all", lambda s, t: copied.append(t) or {})

    code = main(["--config", str(cfg.source), "db", "copy", "--to", "other"])

    assert code == EXIT_FAILED
    assert copied == []
    assert "wipe" in capsys.readouterr().err.lower()


# --- L2 ---


def test_a_resume_blocked_by_a_busy_lock_is_retried_by_the_next_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    _killed_wipe(cfg, monkeypatch)
    wipe_state.deactivate()
    ready = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with WriterLock(cfg.data_dir, "web", wait=0):
            ready.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    ready.wait(10)
    calls: list[str] = []
    real = wipe.resume_unfinished_wipe

    def spy(c: Config) -> object:
        result = real(c)
        calls.append(str(result))
        if len(calls) == 1:
            release.set()
            holder.join()
        return result

    monkeypatch.setattr("il2ks.ingest.watch.resume_unfinished_wipe", spy)
    try:
        watch(cfg, default_pipeline(cfg), max_ticks=2)
    finally:
        release.set()
        holder.join()

    assert len(calls) >= 2
