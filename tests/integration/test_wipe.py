""" "Delete all data and reprocess" (0.2.0, `il2ks.ingest.wipe`): the wipe keeps the admin's settings, the result
equals a fresh ingest of the archive, a failed backup or a missing archive deletes nothing, the admin asks for a typed
word, and a new model has to be classified (kept or wiped) before the tests pass again."""

import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from django.apps import apps
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from il2ks.config import Config
from il2ks.core.tours import TourRules
from il2ks.db.models import (
    Country,
    GameObject,
    IngestRun,
    Mission,
    NavLink,
    Player,
    ReprocessRequest,
    ReprocessStatus,
    SiteSettings,
    Tour,
)
from il2ks.db.reprocess_requests import request_reprocess
from il2ks.db.site import get_site_settings
from il2ks.ingest import wipe
from il2ks.ingest.reprocess_requests import run_pending_request
from il2ks.ingest.runner import IngestOptions, default_pipeline, ingest_once
from il2ks.ingest.tours import retour, start_manual_tour
from il2ks.ingest.wipe import KEEP_MODELS, WipeError, WipePhase, wipe_and_reprocess
from tests.conftest import FIXTURE_LOGS
from tests.db_canon import canonical_dump, diff_dumps
from tests.ingest_fakes import make_config
from tests.integration.test_ingest_runner import threads

pytestmark = pytest.mark.django_db

FIXTURES = {
    "typical": "2026-09-01_10-00-00",
    "most_bailouts": "2026-09-02_10-00-00",
    "two_mission_ends": "2026-09-03_10-00-00",
    "no_mission_end": "2026-09-04_10-00-00",
    "bailout_ejection_stale_wheels": "2026-09-05_10-00-00",
}

WIPED_MODELS = frozenset(
    {
        "PlayerName", "Tour", "PlayerTourName", "Mission", "PlayerSortie", "Kill", "PlayerMission",
        "MissionAircraftAmmo", "MissionAircraftAmmoMix", "PlayerAircraft", "PlayerTour", "PlayerTourAircraft",
        "PlayerAircraftScope", "PlayerAircraftBuild", "PlayerPool", "PlayerTourPool", "PlayerRole",
        "AircraftAmmoStats", "StatThreshold", "SortieThreshold", "AircraftStats", "TourAircraftStats",
        "AircraftMatchup", "AircraftPayload", "AircraftMods", "PlayerKillboard", "PlayerTourKillboard",
        "PlayerTypeKillboard", "PlayerBestStreak", "PlayerStreakRun", "PlayerStreak", "PlayerAchievement",
        "AchievementHolders", "ActivityDay", "AircraftAmmoMixStats", "Player",
    }
)  # fmt: skip
"""Every model that is ingested or derived data. A new model must be added here (wiped) or to `wipe.KEEP_MODELS`."""


def _import_dir(tmp_path: Path) -> Path:
    src = tmp_path / "import"
    src.mkdir(exist_ok=True)
    for name, uid in FIXTURES.items():
        target = src / f"missionReport({uid})[0].txt.zip"
        if not target.exists():
            shutil.copy(FIXTURE_LOGS / f"{name}.txt.zip", target)
    return src


def _ingest(tmp_path: Path, tours: TourRules | None = None) -> Config:
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    if tours is not None:
        cfg = replace(cfg, tours=tours)
    summary = ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
    assert summary.failed == []
    return cfg


def _wipe(cfg: Config, on_phase: Callable[[WipePhase], None] | None = None) -> None:
    summary = wipe_and_reprocess(
        cfg,
        default_pipeline(cfg, defer_ratings=True),
        backup=lambda _cfg: cfg.data_dir / "fake-backup.zip",
        executor_factory=threads,
        workers=2,
        on_phase=on_phase,
    )
    assert summary.failed == []
    assert summary.missing == []
    assert len(summary.ok) == len(FIXTURES)


# --- which models are kept ---


def test_every_model_is_either_kept_or_wiped() -> None:
    """A new model fails this test until someone decides: admin setting (`KEEP_MODELS`) or ingested data."""
    names = {m.__name__ for m in apps.get_app_config("il2ks_db").get_models()}

    assert names == set(KEEP_MODELS) | WIPED_MODELS
    assert set(KEEP_MODELS).isdisjoint(WIPED_MODELS)
    assert {m.__name__ for m in wipe.wiped_models()} == WIPED_MODELS


# --- the result ---


def test_the_result_equals_a_fresh_ingest(tmp_path: Path) -> None:
    cfg = _ingest(tmp_path)
    fresh = canonical_dump()
    assert fresh["PlayerSortie"]

    _wipe(cfg)

    assert diff_dumps(fresh, canonical_dump()) == []
    assert not IngestRun.objects.filter(mission__isnull=True, status="ok").exists()  # the history is linked again


def test_the_wipe_really_deletes_before_the_reprocess(tmp_path: Path) -> None:
    cfg = _ingest(tmp_path)
    seen: list[tuple[WipePhase, int]] = []

    def phase(step: WipePhase) -> None:
        seen.append((step, Mission.objects.count()))

    _wipe(cfg, on_phase=phase)

    assert [p for p, _ in seen] == [WipePhase.CHECK, WipePhase.BACKUP, WipePhase.WIPE, WipePhase.REPROCESS]
    assert seen[-1] == (WipePhase.REPROCESS, 0)
    assert Mission.objects.count() == len(FIXTURES)


# --- the settings survive ---


def test_the_admin_settings_survive(tmp_path: Path) -> None:
    cfg = _ingest(tmp_path)
    # hidden player and mission (by account UUID / mission UID)
    player = Player.objects.order_by("account_uuid").first()
    assert player is not None
    mission = Mission.objects.get(mission_uid=FIXTURES["typical"])
    Player.objects.filter(pk=player.pk).update(is_hidden=True)
    Mission.objects.filter(pk=mission.pk).update(is_hidden=True)
    # a renamed tour
    tour = Tour.objects.get()
    Tour.objects.filter(pk=tour.pk).update(title="The September tour")
    # site settings, rules, quips, achievements, flight-time option, nav links, pictures
    site = get_site_settings()
    site.site_title = "Korea Night Shift"
    site.logo = "branding/logo-abc.webp"
    site.home_bg_image = "branding/bg-home-abc.webp"
    site.quips = {
        "modes": {"home": "off"},
        "custom": [{"spot": "home", "text": "Hi", "language": "en", "enabled": True}],
    }
    site.quips_enabled = False
    site.achievements = {"names": {"some_medal": {"de": "Medaille"}}}
    site.score_flight = {"enabled": True, "per_hour": 7.0}
    site.rule_settings = {"score.air_kill_pvp": 12.0}
    site.rule_settings_applied = {"score.air_kill_pvp": 12.0}
    site.tour_on_win = True
    site.save()
    NavLink.objects.create(site=site, label="Discord", url="https://example.org/d", position=0)
    # an overridden object name and country name
    GameObject.objects.filter(pk=GameObject.objects.filter(is_playable=True).first().pk).update(  # pyright: ignore[reportOptionalMemberAccess]
        display_name="Renamed by admin", name_overridden=True
    )
    renamed = GameObject.objects.get(name_overridden=True)
    Country.objects.filter(code=Country.objects.first().code).update(display_name="Renamed land")  # pyright: ignore[reportOptionalMemberAccess]
    admin = User.objects.create_superuser("boss", "b@example.org", "pw")

    _wipe(cfg)

    assert Player.objects.get(account_uuid=player.account_uuid).is_hidden
    assert Player.objects.filter(is_hidden=True).count() == 1
    assert list(Mission.objects.filter(is_hidden=True).values_list("mission_uid", flat=True)) == [FIXTURES["typical"]]
    assert "The September tour" in [t.title for t in Tour.objects.all()]  # (the on-win option cut it in parts)
    site = SiteSettings.objects.get()
    assert (site.site_title, site.logo, site.home_bg_image) == (
        "Korea Night Shift",
        "branding/logo-abc.webp",
        "branding/bg-home-abc.webp",
    )
    assert site.quips_enabled is False
    assert cast(list[dict[str, str]], site.quips["custom"])[0]["text"] == "Hi"
    assert site.achievements == {"names": {"some_medal": {"de": "Medaille"}}}
    assert site.score_flight == {"enabled": True, "per_hour": 7.0}
    assert site.rule_settings == {"score.air_kill_pvp": 12.0}
    assert site.tour_on_win is True
    assert site.tour_on_win_applied is True  # the new tours were cut with the chosen option
    assert list(NavLink.objects.values_list("label", flat=True)) == ["Discord"]
    assert GameObject.objects.get(pk=renamed.pk).display_name == "Renamed by admin"
    assert Country.objects.filter(display_name="Renamed land").count() == 1
    assert User.objects.filter(pk=admin.pk).exists()
    # the hidden mission stays out of the day's activity numbers, like the admin's own hide action
    assert not Mission.objects.visible().filter(mission_uid=FIXTURES["typical"]).exists()


def test_manual_tour_boundaries_and_names_survive(tmp_path: Path) -> None:
    cfg = _ingest(tmp_path, TourRules(mode="manual"))
    second = start_manual_tour(datetime(2026, 9, 3, 0, 0, tzinfo=UTC), title="Second tour")
    Tour.objects.filter(started_at__lt=second.started_at).update(title="Opening tour")
    retour(cfg.tours)  # the missions go to the boundaries
    before = sorted(Tour.objects.values_list("title", "started_at", "ended_at"))
    placed = dict(Mission.objects.values_list("mission_uid", "tour__title"))
    assert [t[0] for t in before] == ["Opening tour", "Second tour"]
    assert set(placed.values()) == {"Opening tour", "Second tour"}

    _wipe(cfg)

    assert sorted(Tour.objects.values_list("title", "started_at", "ended_at")) == before
    assert dict(Mission.objects.values_list("mission_uid", "tour__title")) == placed


# --- nothing is deleted when it cannot be done safely ---


def test_a_failed_backup_aborts_and_deletes_nothing(tmp_path: Path) -> None:
    cfg = _ingest(tmp_path)
    before = canonical_dump()

    def broken(_cfg: Config) -> Path:
        raise OSError("disk full")

    with pytest.raises(WipeError, match="backup failed"):
        wipe_and_reprocess(
            cfg, default_pipeline(cfg, defer_ratings=True), backup=broken, executor_factory=threads, workers=2
        )

    assert diff_dumps(before, canonical_dump()) == []


def test_a_missing_archive_aborts_and_deletes_nothing(tmp_path: Path) -> None:
    cfg = _ingest(tmp_path)
    before = canonical_dump()
    next((cfg.data_dir / "archive").rglob("*.txt.zip")).unlink()
    backed_up: list[bool] = []

    with pytest.raises(WipeError, match="no archive"):
        wipe_and_reprocess(
            cfg,
            default_pipeline(cfg, defer_ratings=True),
            backup=lambda _cfg: backed_up.append(True) or Path("x"),
            executor_factory=threads,
            workers=2,
        )

    assert backed_up == []
    assert diff_dumps(before, canonical_dump()) == []


# --- the request and the admin ---


def _request_runner(cfg: Config, **kwargs: object) -> ReprocessRequest | None:
    return run_pending_request(cfg, lambda: default_pipeline(cfg, defer_ratings=True), **kwargs)  # pyright: ignore[reportArgumentType]


def test_a_wipe_request_runs_with_its_phases_and_ends_done(tmp_path: Path) -> None:
    cfg = _ingest(tmp_path)
    request = request_reprocess("boss", timezone.now(), wipe=True)
    phases: list[str] = []

    def fake(
        cfg: Config,
        pipeline: object,
        /,
        *,
        on_phase: Callable[[WipePhase], None],
        on_start: Callable[[int], None],
        on_progress: Callable[..., None],
    ) -> object:
        for step in WipePhase:
            on_phase(step)
            phases.append(ReprocessRequest.objects.get(pk=request.pk).phase)
        on_start(3)
        from il2ks.ingest.reprocess import ReprocessSummary

        return ReprocessSummary(ok=["a", "b", "c"])

    done = _request_runner(cfg, wipe_fn=fake)

    assert done is not None
    assert done.status == ReprocessStatus.DONE
    assert phases == ["check", "backup", "wipe", "reprocess"]
    assert (done.phase, done.wipe, done.missions_ok) == ("", True, 3)


def test_a_wipe_that_cannot_start_fails_the_request_with_the_reason(tmp_path: Path) -> None:
    cfg = _ingest(tmp_path)
    request_reprocess("boss", timezone.now(), wipe=True)

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise WipeError("the backup failed, so nothing was deleted: disk full")

    done = _request_runner(cfg, wipe_fn=refuse)

    assert done is not None
    assert done.status == ReprocessStatus.FAILED
    assert "disk full" in done.error
    assert Mission.objects.count() == len(FIXTURES)


def test_a_wipe_cannot_have_a_span() -> None:
    with pytest.raises(ValueError, match="every mission"):
        request_reprocess("boss", timezone.now(), since=timezone.now().date(), wipe=True)


def _login(superuser: bool = True) -> Client:
    User.objects.create_user("boss", "b@example.org", "pw", is_staff=True, is_superuser=superuser)
    client = Client()
    assert client.login(username="boss", password="pw")
    return client


def test_the_admin_needs_the_typed_word(tmp_path: Path) -> None:
    client = _login()

    page = client.get("/admin/ingestion/wipe/")
    assert page.status_code == 200
    assert b'name="confirmation"' in page.content

    wrong = client.post("/admin/ingestion/wipe/", {"confirmation": "delete please"})
    assert wrong.status_code == 200
    assert ReprocessRequest.objects.count() == 0

    right = client.post("/admin/ingestion/wipe/", {"confirmation": "DELETE"})
    assert right.status_code == 302
    (request,) = ReprocessRequest.objects.all()
    assert request.wipe is True
    assert request.status == ReprocessStatus.PENDING
    assert request.requested_by == "boss"


def test_only_a_superuser_can_ask_for_a_wipe() -> None:
    client = _login(superuser=False)

    assert client.get("/admin/ingestion/wipe/").status_code == 403
    assert client.post("/admin/ingestion/wipe/", {"confirmation": "DELETE"}).status_code == 403
    assert ReprocessRequest.objects.count() == 0


def test_the_status_page_offers_the_wipe_button_to_superusers() -> None:
    client = _login()

    assert b"/admin/ingestion/wipe/" in client.get("/admin/ingestion/").content


def test_the_cli_asks_for_a_confirmation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from il2ks.cli import main

    assert main(["reprocess", "--wipe"]) == 2
    assert "--wipe needs --all" in capsys.readouterr().err
    assert main(["reprocess", "--all", "--wipe"]) == 2  # no terminal, no --yes
    assert "--yes" in capsys.readouterr().err
