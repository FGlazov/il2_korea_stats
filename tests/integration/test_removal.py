"""Delete a tour or single missions (FR-ADM-3, FR-ADM-8, `il2ks.ingest.removal`): the result equals never having
ingested them, it survives a "Delete all data and reprocess", manual tours close the gap and can keep their missions,
nothing changes when it cannot run, and the admin asks what happens to the missions and for a typed word. A full wipe
alone can't do this: it reads the same archives back."""

import shutil
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client, override_settings
from django.utils import timezone

from il2ks.config import Config
from il2ks.core.tours import TourRules
from il2ks.db.models import (
    IngestRun,
    Mission,
    Player,
    PlayerSortie,
    RemovedMission,
    ReprocessRequest,
    ReprocessStatus,
    Tour,
)
from il2ks.db.reprocess_requests import request_mission_removal, request_tour_removal
from il2ks.ingest.removal import RemovalError, RemovalPhase, remove_missions, remove_tour
from il2ks.ingest.reprocess import archived_targets, reprocess
from il2ks.ingest.reprocess_requests import run_pending_request
from il2ks.ingest.runner import IngestOptions, default_pipeline, ingest_once
from il2ks.ingest.tours import move_missions, retour, start_manual_tour
from il2ks.ingest.wipe import wipe_and_reprocess
from tests.conftest import FIXTURE_LOGS
from tests.db_canon import canonical_dump, diff_dumps
from tests.ingest_fakes import make_config
from tests.integration.test_ingest_runner import threads
from tests.integration.test_tours_decisive import put
from tests.sortie_tours import assert_sortie_tours_consistent

pytestmark = pytest.mark.django_db

SEPTEMBER = {"typical": "2026-09-01_10-00-00", "most_bailouts": "2026-09-02_10-00-00"}
OCTOBER = {"two_mission_ends": "2026-10-03_10-00-00", "no_mission_end": "2026-10-04_10-00-00"}


def _import(tmp_path: Path, cfg: Config, missions: dict[str, str]) -> None:
    src = tmp_path / "import"
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir()
    for name, uid in missions.items():
        shutil.copy(FIXTURE_LOGS / f"{name}.txt.zip", src / f"missionReport({uid})[0].txt.zip")
    summary = ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=src))
    assert summary.failed == []


def _config(tmp_path: Path, name: str = "data", tours: TourRules | None = None) -> Config:
    cfg = make_config(tmp_path / name, None, after_archive="keep")
    return cfg if tours is None else replace(cfg, tours=tours)


def _no_backup(cfg: Config) -> Path:
    return cfg.data_dir / "fake-backup.zip"


def _remove(cfg: Config, tour: Tour, *, keep_missions: bool = False) -> int:
    return remove_tour(cfg, tour.pk, "boss", keep_missions=keep_missions, backup=_no_backup)


def _remove_missions(cfg: Config, uids: list[str]) -> int:
    ids = list(Mission.objects.filter(mission_uid__in=uids).values_list("pk", flat=True))
    return remove_missions(cfg, ids, "boss", backup=_no_backup)


def _wipe(cfg: Config) -> None:
    summary = wipe_and_reprocess(
        cfg,
        default_pipeline(cfg, defer_ratings=True),
        backup=_no_backup,
        executor_factory=threads,
        workers=2,
    )
    assert summary.failed == []


def _clear_all(tmp_path: Path) -> None:
    """A second world in the same test database: what ingesting only `SEPTEMBER` gives."""
    PlayerSortie.objects.all().delete()
    Mission.objects.all().delete()
    Player.objects.all().delete()
    Tour.objects.all().delete()
    IngestRun.objects.all().delete()
    RemovedMission.objects.all().delete()
    shutil.rmtree(tmp_path / "import", ignore_errors=True)


def test_the_result_equals_never_having_ingested_the_tour(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)
    _import(tmp_path, cfg, OCTOBER)
    assert Tour.objects.count() == 2

    assert _remove(cfg, Tour.objects.get(title="October 2026")) == len(OCTOBER)
    after = canonical_dump()

    _clear_all(tmp_path)
    fresh = _config(tmp_path, "fresh")
    _import(tmp_path, fresh, SEPTEMBER)
    assert diff_dumps(canonical_dump(), after) == []


def test_the_deleted_tour_stays_deleted_through_a_wipe_and_new_ingests(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, {**SEPTEMBER, **OCTOBER})
    _remove(cfg, Tour.objects.get(title="October 2026"))
    kept = canonical_dump()
    assert set(RemovedMission.objects.values_list("mission_uid", flat=True)) == set(OCTOBER.values())
    assert set(archived_targets(cfg, None)) == set(SEPTEMBER.values())

    _import(tmp_path, cfg, OCTOBER)  # the same logs turn up again, in the folder or in an import
    assert not Tour.objects.filter(title="October 2026").exists()

    _wipe(cfg)
    assert not Tour.objects.filter(title="October 2026").exists()
    assert diff_dumps(kept, canonical_dump()) == []


def test_asking_for_a_mission_by_uid_brings_it_back(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, {**SEPTEMBER, **OCTOBER})
    _remove(cfg, Tour.objects.get(title="October 2026"))
    uid = OCTOBER["two_mission_ends"]

    reprocess(cfg, default_pipeline(cfg, defer_ratings=True), [uid], executor_factory=threads, workers=1)

    assert Mission.objects.filter(mission_uid=uid).exists()
    assert Tour.objects.filter(title="October 2026").exists()
    assert not RemovedMission.objects.filter(mission_uid=uid).exists()
    assert RemovedMission.objects.filter(mission_uid=OCTOBER["no_mission_end"]).exists()


def test_deleting_missions_equals_never_having_ingested_them(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, {**SEPTEMBER, **OCTOBER})
    gone = [SEPTEMBER["most_bailouts"], OCTOBER["two_mission_ends"], OCTOBER["no_mission_end"]]

    assert _remove_missions(cfg, gone) == 3
    after = canonical_dump()

    assert not Tour.objects.filter(title="October 2026").exists()  # its last mission went
    assert set(RemovedMission.objects.values_list("mission_uid", "tour_title")) >= {
        (OCTOBER["no_mission_end"], "October 2026"),
        (SEPTEMBER["most_bailouts"], "September 2026"),
    }
    _clear_all(tmp_path)
    _import(tmp_path, _config(tmp_path, "fresh"), {"typical": SEPTEMBER["typical"]})
    assert diff_dumps(canonical_dump(), after) == []


def test_a_deleted_mission_stays_deleted_through_a_wipe(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)
    gone = SEPTEMBER["most_bailouts"]
    _remove_missions(cfg, [gone])
    kept = canonical_dump()

    _import(tmp_path, cfg, {"most_bailouts": gone})
    _wipe(cfg)

    assert not Mission.objects.filter(mission_uid=gone).exists()
    assert diff_dumps(kept, canonical_dump()) == []


def test_deleting_the_last_mission_of_a_manual_tour_keeps_the_tour(tmp_path: Path) -> None:
    cfg = _config(tmp_path, tours=TourRules(mode="manual"))
    _import(tmp_path, cfg, {"typical": SEPTEMBER["typical"]})
    tour = Tour.objects.get()

    _remove_missions(cfg, [SEPTEMBER["typical"]])

    assert Tour.objects.get().pk == tour.pk


def test_a_running_mission_stops_a_mission_removal(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)
    Mission.objects.filter(mission_uid=SEPTEMBER["typical"]).update(is_live=True)

    with pytest.raises(RemovalError, match="still running"):
        _remove_missions(cfg, [SEPTEMBER["typical"], SEPTEMBER["most_bailouts"]])

    assert Mission.objects.count() == len(SEPTEMBER)
    assert not RemovedMission.objects.exists()


def test_a_manual_tour_can_leave_its_missions_to_the_tour_before(tmp_path: Path) -> None:
    rules = TourRules(mode="manual")
    cfg = _config(tmp_path, tours=rules)
    _import(tmp_path, cfg, SEPTEMBER)
    first = Tour.objects.get()
    second = start_manual_tour(datetime(2026, 10, 1, tzinfo=UTC), "Dynamic Campaign Test")
    _import(tmp_path, cfg, OCTOBER)

    assert _remove(cfg, second, keep_missions=True) == 0

    assert Tour.objects.get().pk == first.pk
    assert Mission.objects.filter(tour=first).count() == len(SEPTEMBER) + len(OCTOBER)
    assert not RemovedMission.objects.exists()
    assert_sortie_tours_consistent()
    moved = canonical_dump()
    rebuilt_players = Player.objects.count()
    _wipe(cfg)  # the kept missions stay where they are
    assert Player.objects.count() == rebuilt_players
    assert diff_dumps(moved, canonical_dump()) == []


def test_the_first_manual_tour_leaves_its_missions_to_the_tour_after(tmp_path: Path) -> None:
    cfg = _config(tmp_path, tours=TourRules(mode="manual"))
    _import(tmp_path, cfg, SEPTEMBER)
    first = Tour.objects.get()
    second = start_manual_tour(datetime(2026, 10, 1, tzinfo=UTC))

    _remove(cfg, first, keep_missions=True)

    assert Mission.objects.filter(tour=second).count() == len(SEPTEMBER)
    assert_sortie_tours_consistent()


def test_a_calendar_tour_cannot_keep_its_missions(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)

    with pytest.raises(RemovalError, match="no other tour"):
        _remove(cfg, Tour.objects.get(), keep_missions=True)

    assert Tour.objects.count() == 1


def test_deleting_the_newest_manual_tour_reopens_the_one_before(tmp_path: Path) -> None:
    rules = TourRules(mode="manual")
    cfg = _config(tmp_path, tours=rules)
    _import(tmp_path, cfg, SEPTEMBER)
    newest = start_manual_tour(datetime(2026, 10, 1, tzinfo=UTC))
    _import(tmp_path, cfg, OCTOBER)
    assert Mission.objects.filter(tour=newest).count() == len(OCTOBER)

    _remove(cfg, newest)

    assert Tour.objects.get().ended_at is None
    assert Mission.objects.count() == len(SEPTEMBER)


def test_deleting_a_middle_manual_tour_gives_its_time_to_the_tour_before(tmp_path: Path) -> None:
    rules = TourRules(mode="manual")
    cfg = _config(tmp_path, tours=rules)
    _import(tmp_path, cfg, SEPTEMBER)
    first = Tour.objects.get()
    middle = start_manual_tour(datetime(2026, 9, 2, tzinfo=UTC))
    last = start_manual_tour(datetime(2026, 9, 3, tzinfo=UTC))
    retour(rules)  # the second September mission moves into the middle tour

    _remove(cfg, middle)

    first.refresh_from_db()
    assert first.ended_at == last.started_at
    assert Tour.objects.filter(pk=middle.pk).count() == 0


def test_an_empty_manual_tour_can_be_deleted(tmp_path: Path) -> None:
    cfg = _config(tmp_path, tours=TourRules(mode="manual"))
    _import(tmp_path, cfg, SEPTEMBER)
    empty = start_manual_tour()

    assert _remove(cfg, empty) == 0
    assert Tour.objects.get().ended_at is None


def test_the_only_tour_can_be_deleted(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)

    _remove(cfg, Tour.objects.get())

    assert not Tour.objects.exists()
    assert not Mission.objects.exists()
    assert not Player.objects.exists()


def test_a_failed_backup_deletes_nothing(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)
    before = canonical_dump()

    def broken(_cfg: Config) -> Path:
        raise OSError("disk full")

    with pytest.raises(RemovalError, match="disk full"):
        remove_tour(cfg, Tour.objects.get().pk, "boss", backup=broken)

    assert diff_dumps(before, canonical_dump()) == []
    assert not RemovedMission.objects.exists()


def test_a_running_mission_stops_it(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)
    Mission.objects.filter(mission_uid=SEPTEMBER["typical"]).update(is_live=True)

    with pytest.raises(RemovalError, match="still running"):
        _remove(cfg, Tour.objects.get())

    assert Mission.objects.count() == len(SEPTEMBER)


def test_the_watch_loop_runs_the_request_and_reports_a_failure(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)
    tour = Tour.objects.get()
    calls: list[tuple[int, str, bool]] = []

    def fake(
        _cfg: Config, tour_id: int, removed_by: str, /, *, keep_missions: bool, on_phase: Callable[[RemovalPhase], None]
    ) -> int:
        calls.append((tour_id, removed_by, keep_missions))
        on_phase(RemovalPhase.DELETE)
        return 2

    request_tour_removal("boss", timezone.now(), tour, keep_missions=True)
    done = run_pending_request(cfg, lambda: default_pipeline(cfg), remove_tour_fn=fake)
    assert done is not None
    assert calls == [(tour.pk, "boss", True)]
    assert (done.status, done.missions_ok, done.phase) == (ReprocessStatus.DONE, 2, "")
    assert done.remove_tour_title == tour.title

    def refused(
        _cfg: Config, _tour_id: int, _by: str, /, *, keep_missions: bool, on_phase: Callable[[RemovalPhase], None]
    ) -> int:
        raise RemovalError("nope")

    request_tour_removal("boss", timezone.now(), tour, keep_missions=False)
    failed = run_pending_request(cfg, lambda: default_pipeline(cfg), remove_tour_fn=refused)
    assert failed is not None
    assert (failed.status, failed.error) == (ReprocessStatus.FAILED, "nope")


def test_the_watch_loop_runs_a_mission_request(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _import(tmp_path, cfg, SEPTEMBER)
    ids = list(Mission.objects.values_list("pk", flat=True))[:1]
    calls: list[list[int]] = []

    def fake(_cfg: Config, mission_ids: Sequence[int], _by: str, /, *, on_phase: Callable[[RemovalPhase], None]) -> int:
        calls.append(list(mission_ids))
        return 1

    request_mission_removal("boss", timezone.now(), ids)
    done = run_pending_request(cfg, lambda: default_pipeline(cfg), remove_missions_fn=fake)

    assert done is not None
    assert calls == [ids]
    assert (done.status, done.missions_ok) == (ReprocessStatus.DONE, 1)


# --- the admin ---


@pytest.fixture
def boss(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def _tour() -> Tour:
    return Tour.objects.create(
        title="Dynamic Campaign Test", started_at=datetime(2026, 10, 30, tzinfo=UTC), mode="manual"
    )


def _mission(uid: str, tour: Tour) -> Mission:
    mission = put(datetime.strptime(uid, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=UTC))
    move_missions([mission.pk], tour.pk)
    return mission


def test_the_page_needs_a_superuser(client: Client) -> None:
    url = f"/admin/il2ks_db/tour/{_tour().pk}/remove/"
    assert Client().get(url).status_code == 302  # to the login
    clerk = User.objects.create_user("clerk", is_staff=True, password="x")
    clerk.user_permissions.add(*Permission.objects.all())
    client.force_login(clerk)
    assert client.get(url).status_code == 403
    assert client.post(url, {"confirmation": "DELETE"}).status_code == 403
    assert "/remove/" not in client.get("/admin/il2ks_db/tour/").content.decode()
    assert not ReprocessRequest.objects.exists()


def test_the_list_links_to_the_confirmation_and_the_typed_word_files_the_request(boss: Client) -> None:
    tour = _tour()
    url = f"/admin/il2ks_db/tour/{tour.pk}/remove/"
    assert url in boss.get("/admin/il2ks_db/tour/").content.decode()
    page = boss.get(url).content.decode()
    assert "Dynamic Campaign Test" in page
    assert 'name="confirmation"' in page

    boss.post(url, {"confirmation": "delete it"})
    assert not ReprocessRequest.objects.exists()

    assert boss.post(url, {"confirmation": "DELETE"}).status_code == 302
    request = ReprocessRequest.objects.get()
    assert (request.remove_tour_id, request.remove_tour_title, request.wipe) == (tour.pk, tour.title, False)
    assert Tour.objects.filter(pk=tour.pk).exists()  # the watch loop does the work
    status = boss.get("/admin/ingestion/").content.decode()
    assert "Dynamic Campaign Test" in status


def test_a_waiting_request_stops_a_second_one(boss: Client) -> None:
    tour = _tour()
    request_tour_removal("boss", timezone.now(), tour, keep_missions=False)

    response = boss.post(f"/admin/il2ks_db/tour/{tour.pk}/remove/", {"confirmation": "DELETE"})

    assert response.status_code == 302
    assert ReprocessRequest.objects.count() == 1


def test_a_removed_mission_can_be_let_back_but_not_edited(boss: Client) -> None:
    row = RemovedMission.objects.create(
        mission_uid="2026-10-30_10-00-00", tour_title="Test", removed_at=timezone.now(), removed_by="boss"
    )
    assert "2026-10-30_10-00-00" in boss.get("/admin/il2ks_db/removedmission/").content.decode()

    response = boss.post(f"/admin/il2ks_db/removedmission/{row.pk}/delete/", {"post": "yes"})

    assert response.status_code == 302
    assert not RemovedMission.objects.exists()


@override_settings(IL2KS_TOUR_MODE="manual")
def test_a_manual_tour_with_a_neighbour_asks_what_happens_to_its_missions(boss: Client) -> None:
    Tour.objects.create(title="October 2026", started_at=datetime(2026, 10, 1, tzinfo=UTC), mode="manual")
    tour = _tour()
    _mission("2026-10-30_10-00-00", tour)
    url = f"/admin/il2ks_db/tour/{tour.pk}/remove/"
    page = boss.get(url).content.decode()
    assert 'name="missions" value="delete"' in page
    assert 'name="missions" value="keep"' in page
    assert "October 2026" in page

    boss.post(url, {"confirmation": "DELETE"})  # no answer: nothing is filed
    assert not ReprocessRequest.objects.exists()

    boss.post(url, {"confirmation": "DELETE", "missions": "keep"})
    assert ReprocessRequest.objects.get().remove_tour_missions is False


def test_a_calendar_tour_says_its_missions_go_too(boss: Client) -> None:
    tour = _tour()
    _mission("2026-10-30_10-00-00", tour)
    url = f"/admin/il2ks_db/tour/{tour.pk}/remove/"
    assert 'name="missions"' not in boss.get(url).content.decode()

    boss.post(url, {"confirmation": "DELETE", "missions": "keep"})  # a forged answer is ignored

    assert ReprocessRequest.objects.get().remove_tour_missions is True


def test_the_mission_action_asks_and_files_the_request(boss: Client) -> None:
    tour = _tour()
    gone = _mission("2026-10-30_10-00-00", tour)
    _mission("2026-10-30_11-00-00", tour)
    page = boss.post(
        "/admin/il2ks_db/mission/", {"action": "remove_selected", "_selected_action": [str(gone.pk)]}
    ).content.decode()
    assert "2026-10-30_10-00-00" in page
    assert "2026-10-30_11-00-00" not in page
    assert not ReprocessRequest.objects.exists()

    boss.post("/admin/il2ks_db/mission/remove/", {"ids": [str(gone.pk)], "confirmation": "nope"})
    assert not ReprocessRequest.objects.exists()

    boss.post("/admin/il2ks_db/mission/remove/", {"ids": [str(gone.pk), "x"], "confirmation": "DELETE"})
    assert ReprocessRequest.objects.get().remove_mission_ids == [gone.pk]
    assert Mission.objects.filter(pk=gone.pk).exists()  # the watch loop does the work
    assert "deleting 1 mission" in boss.get("/admin/ingestion/").content.decode()


def test_the_mission_action_needs_a_superuser(client: Client) -> None:
    gone = _mission("2026-10-30_10-00-00", _tour())
    clerk = User.objects.create_user("clerk", is_staff=True, password="x")
    clerk.user_permissions.add(*Permission.objects.all())
    client.force_login(clerk)

    assert "remove_selected" not in client.get("/admin/il2ks_db/mission/").content.decode()
    response = client.post("/admin/il2ks_db/mission/remove/", {"ids": [str(gone.pk)], "confirmation": "DELETE"})

    assert response.status_code == 403
    assert not ReprocessRequest.objects.exists()
