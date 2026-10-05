"""Review #12 regressions: the rules the admin applied reach every path (live discard, the end of a long run, the
parser's limits, the effect texts, the wanted-vs-applied race). FR-ADM-7."""

import shutil
from collections.abc import Generator, Iterable
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.db import transaction
from django.test import Client
from django.utils import translation

from il2ks.config import Config, RuleSet
from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.core.stat_marks import DEFAULT_MARK_RULES, MarkRules
from il2ks.db.models import Player, SiteSettings, Tour
from il2ks.db.site import get_site_settings
from il2ks.ingest import aggregates, persist, score_apply
from il2ks.ingest import batch as batch_mod
from il2ks.ingest.batch import Level2Batch
from il2ks.ingest.live import discard_stale_provisional
from il2ks.ingest.persist import save_mission
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.rule_store import applied_overrides, save_overrides, wanted_overrides
from il2ks.ingest.runner import IngestOptions, default_pipeline, ingest_once
from il2ks.ingest.score_apply import rescore_with_wanted
from il2ks.ingest.stat_marks import recompute_thresholds
from il2ks.ingest.tours import start_manual_tour
from il2ks.ops import migrate
from il2ks.rule_settings import BY_KEY, validate
from il2ks.web.admin_rules import build_groups
from tests.conftest import FIXTURE_LOGS
from tests.factories import SERVER_UID, FakeCatalog, meta
from tests.ingest_fakes import make_config
from tests.integration.test_batched_level2 import FIXTURES
from tests.integration.test_elo_alltime_minimum import OCT, OCTOBER, SEP, SEPTEMBER, jet, put
from tests.integration.test_elo_alltime_minimum import RULES as ELO_RULES
from tests.integration.test_tours import MONTHLY
from tests.integration.test_tours_decisive import at
from tests.integration.test_tours_review11 import result
from tests.ops_helpers import make_instance

pytestmark = pytest.mark.django_db


class _Rollback(Exception):
    pass


@contextmanager
def _scratch() -> Generator[None]:
    """Whatever happens inside is rolled back afterwards."""
    try:
        with transaction.atomic():
            yield
            raise _Rollback
    except _Rollback:
        pass


def _import_dir(tmp_path: Path) -> Path:
    src = tmp_path / "import"
    if not src.exists():
        src.mkdir()
        for name, uid in FIXTURES.items():
            shutil.copy(FIXTURE_LOGS / f"{name}.txt.zip", src / f"missionReport({uid})[0].txt.zip")
    return src


def _thread_pool(workers: int) -> Executor:
    return ThreadPoolExecutor(max_workers=workers)


def test_a_live_discard_uses_the_applied_tour_mode_not_the_files(tmp_path: Path) -> None:
    """Admin applied `tours.mode = manual` over a monthly file: discarding a stale provisional mission must not delete
    the admin's manual tour (FR-ADM-7: every path reads `effective_config`)."""
    get_site_settings()
    SiteSettings.objects.filter(pk=1).update(
        rule_settings={"tours.mode": "manual"}, rule_settings_applied={"tours.mode": "manual"}
    )
    manual = replace(MONTHLY, mode="manual")
    t0 = at(2026, 10, 1, 8)
    with transaction.atomic():
        save_mission(result(0, None), meta("m-1", t0), FakeCatalog(), DEFAULT_RULES, manual)
    start_manual_tour(t0 + timedelta(days=2), "Tour 2")
    live_meta = replace(meta("live-1", t0 + timedelta(days=3)), live=True)
    with transaction.atomic():
        save_mission(result(1, None), live_meta, FakeCatalog(), None, manual)
    assert Tour.objects.count() == 2
    cfg = replace(make_config(tmp_path, None), server_uid=SERVER_UID)  # the file says monthly (the default)
    assert discard_stale_provisional(cfg, keep="") == 1
    assert Tour.objects.count() == 2, "the admin's manual tour was deleted"


# --- a long run reads the marks in force when it computes the thresholds -------------------------------------------


def _spy_on_the_thresholds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The marks minimum of every threshold pass (the fixtures have too few pilots for a stored row to show it)."""
    minimums: list[int] = []

    def spy(rules: MarkRules = DEFAULT_MARK_RULES, tour_ids: Iterable[int] | None = None) -> None:
        minimums.append(rules.min_sorties)
        recompute_thresholds(rules, tour_ids)

    monkeypatch.setattr(persist, "recompute_thresholds", spy)
    monkeypatch.setattr(aggregates, "recompute_thresholds", spy)
    return minimums


def _admin_saves_marks_minimum(value: int) -> None:
    """What the Leaderboards page does for a display field: wanted and applied at once."""
    SiteSettings.objects.update_or_create(
        pk=1,
        defaults={"rule_settings": {"marks.min_sorties": value}, "rule_settings_applied": {"marks.min_sorties": value}},
    )


def test_a_batched_ingest_uses_the_marks_minimum_saved_during_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin saves a new marks minimum between two missions of a long run: the thresholds written at the end use it,
    not the minimum the run started with (FR-ADM-7)."""
    monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
    original = Level2Batch.mission_done
    seen: list[int] = []

    def saves_once_the_first_is_done(self: Level2Batch) -> None:
        original(self)
        if not seen:
            seen.append(1)
            _admin_saves_marks_minimum(1)

    monkeypatch.setattr(Level2Batch, "mission_done", saves_once_the_first_is_done)
    minimums = _spy_on_the_thresholds(monkeypatch)
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
    assert minimums[-1] == 1, minimums


def test_a_batched_reprocess_uses_the_marks_minimum_saved_during_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(tmp_path / "data", None, after_archive="keep")
    with _scratch():
        ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=_import_dir(tmp_path)))
        monkeypatch.setattr(batch_mod, "BATCH_MIN", 2)
        saved: list[int] = []

        def saves_after_the_first(_summary: object) -> None:
            if not saved:
                saved.append(1)
                _admin_saves_marks_minimum(1)

        minimums = _spy_on_the_thresholds(monkeypatch)
        reprocess(
            cfg,
            default_pipeline(cfg, defer_ratings=True),
            workers=1,
            executor_factory=_thread_pool,
            on_progress=saves_after_the_first,
        )
    assert minimums[-1] == 1, minimums


def test_a_display_change_saved_during_the_rebuild_survives_the_adopt_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review #12 item 5 (a lost update on Postgres): the watch rebuild read the wanted values at its start and wrote
    `rule_settings_applied` at its end; a display value the admin saved in between was overwritten there, and as a
    display field it is never pending again. The adopt step keeps the display and reprocess keys of the current row."""
    cfg = make_instance(tmp_path, with_db=False)
    row = get_site_settings()
    row.rule_settings = {"score.air_kill_pvp": 20.0}
    row.save()
    real = score_apply.rebuild_aggregates

    def rebuild_while_the_admin_saves(*args: object, **kwargs: object) -> None:
        real(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        save_overrides([BY_KEY["marks.min_sorties"]], {"marks.min_sorties": 7}, RuleSet())

    monkeypatch.setattr(score_apply, "rebuild_aggregates", rebuild_while_the_admin_saves)
    assert rescore_with_wanted(cfg)
    assert applied_overrides() == {"score.air_kill_pvp": 20.0, "marks.min_sorties": 7}
    assert wanted_overrides() == applied_overrides()


def test_an_upgrade_recomputes_the_all_time_elo_once_with_the_minimum_of_games(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review #12 item 6: a database that already ran `player_rollup` kept the old "best of any tour" all-time Elo. One
    marker-gated recompute, folded into the single upgrade rebuild (FR-OPS-3)."""
    put(SEP, SEPTEMBER)
    put(OCT, OCTOBER)
    september = jet(1).elo_jet
    Player.objects.filter(pk=jet(1).pk).update(elo_jet=1516.0)  # what the old rule stored: the lucky October game
    names = [v for k, v in vars(migrate).items() if k.startswith("BACKFILL_") and v != "elo_min_games"]
    SiteSettings.objects.filter(pk=1).update(backfills_done=names)
    migrate._record_catalog_fingerprint(migrate.catalog_fingerprint())  # pyright: ignore[reportPrivateUsage]
    rebuilds: list[str] = []
    real = migrate._rebuild_all  # pyright: ignore[reportPrivateUsage]

    def counted(cfg: Config) -> None:
        rebuilds.append("rebuild")
        real(cfg)

    monkeypatch.setattr(migrate, "_rebuild_all", counted)
    cfg = replace(make_instance(tmp_path, with_db=False), ratings=ELO_RULES)

    migrate._run_backfills(cfg)  # pyright: ignore[reportPrivateUsage]

    assert rebuilds == ["rebuild"]
    assert jet(1).elo_jet == september != 1516.0
    migrate._run_backfills(cfg)  # pyright: ignore[reportPrivateUsage]
    assert rebuilds == ["rebuild"]  # marked: not again


def test_the_elo_minimum_says_it_is_recomputed_by_a_rebuild_not_that_every_sortie_is_re_scored() -> None:
    """Review #12 item 7: `min_elo_games` is applied by a rebuild; its effect text is not the scoring one."""
    rows = {row.key: row for group in build_groups("leaderboards", get_site_settings()) for row in group.rows}
    scoring = {row.key: row for group in build_groups("scoring", get_site_settings()) for row in group.rows}
    assert "Re-scores" in scoring["score.air_kill_pvp"].effect
    elo = rows["score.min_elo_games"]
    assert "Re-scores" not in elo.effect
    assert "rebuild" in elo.effect
    assert rows["score.min_sorties"].effect == "Applies at once"


def test_the_leaderboards_page_says_which_fields_apply_at_once_and_which_after_the_rebuild(admin: Client) -> None:
    page = admin.get("/admin/leaderboards/").content.decode()
    assert "A change shows at once" not in page
    assert "rebuild" in page


def test_the_scoring_hint_shows_the_applied_kill_points_not_the_built_in_ones(admin: Client) -> None:
    SiteSettings.objects.update_or_create(
        pk=1,
        defaults={
            "rule_settings": {"score.air_kill_pvp": 12.0},
            "rule_settings_applied": {"score.air_kill_pvp": 12.0},
        },
    )
    page = admin.get("/admin/score/").content.decode()
    assert "a player&#x27;s 12)" in page or "a player's 12)" in page


def test_the_problems_of_the_parser_reach_the_admin_in_the_active_language() -> None:
    _, english = validate(RuleSet(), {"score.min_sorties": "inf"})
    with translation.override("de"):
        _, german = validate(RuleSet(), {"score.min_sorties": "inf"})
    assert english == ["score.min_sorties must be a finite number"]  # the toml path keeps the English text
    assert german == ["score.min_sorties muss eine endliche Zahl sein"]


def test_the_effect_texts_follow_the_active_language() -> None:
    """Review #12 item 4: the effect texts were translated once at import, so every admin saw English."""
    english = [row.effect for group in build_groups("leaderboards", get_site_settings()) for row in group.rows]
    with translation.override("de"):
        german = [row.effect for group in build_groups("leaderboards", get_site_settings()) for row in group.rows]
    assert "Applies at once" in english
    assert "Applies at once" not in german
    assert german != english


# --- the admin form never raises on a number the parser cannot take --------------------------------------------------


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


@pytest.mark.parametrize(
    ("url", "key", "text"),
    [
        ("/admin/leaderboards/", "score.min_sorties", "inf"),
        ("/admin/leaderboards/", "score.min_sorties", "1e400"),
        ("/admin/leaderboards/", "marks.min_sorties", "nan"),
        ("/admin/score/", "score.air_kill_pvp", "nan"),
        ("/admin/score/", "ratings.k", "inf"),
        ("/admin/tours/", "tours.mode", "days:999999999"),
    ],
)
def test_the_admin_form_rejects_non_finite_numbers_with_a_message(admin: Client, url: str, key: str, text: str) -> None:
    """Review #12 item 3: a 500 (OverflowError), or a nan stored and the JSON CHECK shown as "database is busy"."""
    posted = {key: text, "tours.start": "2026-10-01"} if key == "tours.mode" else {key: text}
    response = admin.post(url, posted)
    assert response.status_code == 200  # the form again, not a redirect after a save
    assert wanted_overrides() == {}
    assert "Not saved" in response.content.decode()
