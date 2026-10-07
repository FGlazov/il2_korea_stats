"""The wipe's review findings (2026-10-07): hidden players and missions stay hidden during the whole run and across a
crash (H1), the URL keys of players, missions, sorties and tours survive (H2), and the small ones (LOW)."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone

from il2ks.db.models import (
    IngestRun,
    Mission,
    Player,
    PlayerSortie,
    ReprocessRequest,
    ReprocessStatus,
    SiteSettings,
    Tour,
)
from il2ks.db.reprocess_requests import request_reprocess
from il2ks.ingest import wipe, wipe_state
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.reprocess_requests import fail_interrupted_requests
from il2ks.ingest.runner import default_pipeline
from il2ks.ingest.watch import watch
from il2ks.ingest.wipe import WipeError, wipe_and_reprocess
from tests.db_canon import canonical_dump, diff_dumps
from tests.integration.test_ingest_runner import threads
from tests.integration.test_wipe import FIXTURES, _ingest, _wipe  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_active_wipe_state() -> Iterator[None]:
    """A killed wipe leaves its state active in the process (as in production); a test must not leak it."""
    yield
    wipe_state.deactivate()


def _hidden_keys() -> tuple[set[str], set[str]]:
    return (
        set(Player.objects.filter(is_hidden=True).values_list("account_uuid", flat=True)),
        set(Mission.objects.filter(is_hidden=True).values_list("mission_uid", flat=True)),
    )


def _hide_some(players_of: str, missions: list[str]) -> tuple[set[str], set[str]]:
    """Hide the players of one mission and some missions; returns what is hidden (account UUIDs, mission UIDs)."""
    accounts = set(PlayerSortie.objects.filter(mission__mission_uid=players_of).values_list("account_uuid", flat=True))
    Player.objects.filter(account_uuid__in=accounts).update(is_hidden=True)
    Mission.objects.filter(mission_uid__in=missions).update(is_hidden=True)
    return accounts, set(missions)


def _killed_wipe(cfg: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wipe whose process dies after the first mission: nothing at the end of the run happens."""

    def kill(_summary: object) -> None:
        raise KeyboardInterrupt("killed")

    def no_cleanup(*_args: object, **_kwargs: object) -> None:
        raise SystemExit("killed: the end of the run never ran")

    monkeypatch.setattr(wipe, "restore_marks", no_cleanup)
    with pytest.raises((KeyboardInterrupt, SystemExit)):
        wipe_and_reprocess(
            cfg,  # pyright: ignore[reportArgumentType]
            default_pipeline(cfg, defer_ratings=True),  # pyright: ignore[reportArgumentType]
            backup=lambda _cfg: Path("fake-backup.zip"),
            executor_factory=threads,
            workers=1,
            on_progress=kill,
        )
    monkeypatch.undo()
    assert 0 < Mission.objects.count() < len(FIXTURES)


# --- H1 ---


def test_nothing_hidden_is_public_while_the_reprocess_runs(tmp_path: Path) -> None:
    """After every reprocessed mission, each hidden player and mission that is back is still hidden."""
    cfg = _ingest(tmp_path)
    accounts, uids = _hide_some(FIXTURES["typical"], [FIXTURES["typical"], FIXTURES["two_mission_ends"]])
    exposed: list[str] = []
    checks: list[int] = []

    def look(_summary: object) -> None:
        checks.append(Player.objects.filter(account_uuid__in=accounts).count() + Mission.objects.count())
        exposed.extend(
            Player.objects.filter(account_uuid__in=accounts, is_hidden=False).values_list("account_uuid", flat=True)
        )
        exposed.extend(
            Mission.objects.filter(mission_uid__in=uids, is_hidden=False).values_list("mission_uid", flat=True)
        )

    summary = wipe_and_reprocess(
        cfg,
        default_pipeline(cfg, defer_ratings=True),
        backup=lambda _cfg: Path("fake-backup.zip"),
        executor_factory=threads,
        workers=1,
        on_progress=look,
    )

    assert len(summary.ok) == len(FIXTURES)
    assert min(checks) > 0  # the look found rows
    assert exposed == []
    assert _hidden_keys() == (accounts, uids)


def test_a_wipe_killed_between_two_missions_keeps_its_marks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The marks are on disk: the next reprocess brings the other missions back hidden, with the tour's name."""
    cfg = _ingest(tmp_path)
    accounts, uids = _hide_some(FIXTURES["typical"], [FIXTURES["typical"], FIXTURES["no_mission_end"]])
    Tour.objects.update(title="The September tour")

    _killed_wipe(cfg, monkeypatch)
    summary = reprocess(cfg, default_pipeline(cfg, defer_ratings=True), executor_factory=threads, workers=1)

    assert len(summary.ok) == len(FIXTURES)
    assert _hidden_keys() == (accounts, uids)
    assert "The September tour" in [t.title for t in Tour.objects.all()]


def test_watch_start_applies_the_marks_of_a_wipe_that_never_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _ingest(tmp_path)
    accounts, uids = _hide_some(FIXTURES["typical"], [FIXTURES["typical"]])

    _killed_wipe(cfg, monkeypatch)
    Player.objects.update(is_hidden=False)  # whatever lost the marks on the rows that came back
    Mission.objects.update(is_hidden=False)

    watch(cfg, default_pipeline(cfg), max_ticks=1)

    assert not Player.objects.filter(account_uuid__in=accounts, is_hidden=False).exists()
    assert not Mission.objects.filter(mission_uid__in=uids, is_hidden=False).exists()


# --- H2 ---


def _sortie_keys() -> dict[tuple[str, str, int], int]:
    return {
        (uid, account, tick): pk
        for pk, uid, account, tick in PlayerSortie.objects.values_list(
            "pk", "mission__mission_uid", "account_uuid", "spawn_tick"
        )
    }


def test_player_mission_sortie_and_tour_keys_survive_the_wipe(tmp_path: Path) -> None:
    """/players/<pk>/, /missions/<pk>/, /sorties/<pk>/ and ?tour=<pk> are shared and indexed: they keep working."""
    cfg = _ingest(tmp_path)
    players = dict(Player.objects.values_list("account_uuid", "pk"))
    missions = dict(Mission.objects.values_list("mission_uid", "pk"))
    sorties = _sortie_keys()
    tours = dict(Tour.objects.values_list("started_at", "pk"))
    assert players
    assert missions
    assert sorties
    assert tours

    _wipe(cfg)

    assert dict(Player.objects.values_list("account_uuid", "pk")) == players
    assert dict(Mission.objects.values_list("mission_uid", "pk")) == missions
    assert _sortie_keys() == sorties
    assert dict(Tour.objects.values_list("started_at", "pk")) == tours
    sortie = PlayerSortie.objects.order_by("pk").select_related("mission").first()
    assert sortie is not None
    client = Client()
    assert client.get(f"/players/{sortie.player_id}/").status_code == 200
    assert client.get(f"/missions/{sortie.mission_id}/").status_code == 200
    assert client.get(f"/sorties/{sortie.pk}/").status_code == 200


def test_sorties_named_inside_a_sortie_are_the_same_after_the_wipe(tmp_path: Path) -> None:
    """The damage and timeline JSON name other sorties by pk."""
    cfg = _ingest(tmp_path)
    before = {pk: (b, t) for pk, b, t in PlayerSortie.objects.values_list("pk", "damage_breakdown", "timeline")}

    _wipe(cfg)

    assert {pk: (b, t) for pk, b, t in PlayerSortie.objects.values_list("pk", "damage_breakdown", "timeline")} == before


def test_keys_survive_a_killed_wipe_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _ingest(tmp_path)
    players = dict(Player.objects.values_list("account_uuid", "pk"))
    missions = dict(Mission.objects.values_list("mission_uid", "pk"))
    sorties = _sortie_keys()

    _killed_wipe(cfg, monkeypatch)
    reprocess(cfg, default_pipeline(cfg, defer_ratings=True), executor_factory=threads, workers=1)

    assert dict(Player.objects.values_list("account_uuid", "pk")) == players
    assert dict(Mission.objects.values_list("mission_uid", "pk")) == missions
    assert _sortie_keys() == sorties


# --- LOW ---


def test_a_wipe_that_cannot_order_its_models_adopts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The models' order is computed before the admin's wanted settings are adopted: a cycle error changes nothing."""
    cfg = _ingest(tmp_path)
    SiteSettings.objects.filter(pk=1).update(tour_on_win=True, tour_on_win_applied=False)
    before = canonical_dump()

    def cycle() -> list[object]:
        raise WipeError("the models to wipe point at each other in a circle: X")

    monkeypatch.setattr(wipe, "wiped_models", cycle)
    with pytest.raises(WipeError, match="circle"):
        wipe_and_reprocess(
            cfg,
            default_pipeline(cfg, defer_ratings=True),
            backup=lambda _cfg: Path("fake-backup.zip"),
            executor_factory=threads,
            workers=2,
        )

    assert SiteSettings.objects.get(pk=1).tour_on_win_applied is False
    assert diff_dumps(before, canonical_dump()) == []


def test_an_error_at_the_end_does_not_hide_the_original_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`finish()` raising while the run already failed must not replace the run's own error."""
    cfg = _ingest(tmp_path)

    def broken_end(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("restore failed")

    def fail(_summary: object) -> None:
        raise ValueError("the run's own error")

    monkeypatch.setattr(wipe, "restore_marks", broken_end)
    with pytest.raises(ValueError, match="the run's own error"):
        wipe_and_reprocess(
            cfg,
            default_pipeline(cfg, defer_ratings=True),
            backup=lambda _cfg: Path("fake-backup.zip"),
            executor_factory=threads,
            workers=1,
            on_progress=fail,
        )


def test_old_runs_are_relinked_by_server_and_mission(tmp_path: Path) -> None:
    """A run of a mission of another server with the same UID is not linked to this server's mission."""
    cfg = _ingest(tmp_path)
    typical = Mission.objects.get(mission_uid=FIXTURES["typical"])
    own_run = IngestRun.objects.filter(mission=typical).first()
    assert own_run is not None
    foreign = Mission.objects.get(pk=typical.pk)
    foreign.pk = None
    foreign.server_uid = uuid.uuid4()
    foreign.save()
    foreign_run = IngestRun.objects.create(
        mission_uid=typical.mission_uid, mission=foreign, status="ok", started_at=timezone.now(), il2ks_version="x"
    )

    _wipe(cfg)

    assert IngestRun.objects.get(pk=foreign_run.pk).mission_id is None
    assert IngestRun.objects.get(pk=own_run.pk).mission_id is not None


def test_an_interrupted_request_does_not_keep_its_phase() -> None:
    """A request the process died on is failed and no longer shows a running step."""
    request = request_reprocess("boss", timezone.now(), wipe=True)
    ReprocessRequest.objects.filter(pk=request.pk).update(status=ReprocessStatus.RUNNING, phase="reprocess")

    assert fail_interrupted_requests(timezone.now()) == 1

    row = ReprocessRequest.objects.get(pk=request.pk)
    assert (row.status, row.phase) == (ReprocessStatus.FAILED, "")
