"""Live sorties (FR-ING-15): the running mission is saved provisionally by the live tracker and replaced by the final
save. Real anonymized fixture missions are cut into raw parts that grow tick by tick in a temp log folder; the final
ingest runs the real pipeline, and its rows must be exactly those of a one-shot ingest of the complete mission."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import transaction
from django.db.models import Sum

from il2ks.config import Config, LiveConfig
from il2ks.db.models import (
    IngestRun,
    IngestStatus,
    Kill,
    KillCredit,
    LiveMission,
    LivePlayer,
    Mission,
    Player,
    PlayerAircraft,
    PlayerSortie,
    SiteSettings,
)
from il2ks.db.site import current_data_version
from il2ks.ingest import live as live_module
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.live import LiveTracker, discard_stale_provisional
from il2ks.ingest.lock import WriterLock
from il2ks.ingest.ratings import recompute_ratings
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.runner import default_pipeline, ingest_once
from il2ks.ingest.watch import watch
from tests.db_canon import canonical_dump, diff_dumps
from tests.ingest_fakes import T0, FakeSteps, make_config, make_pipeline
from tests.live_helpers import (
    LIVE_UID,
    SteppingClock,
    fixture_lines,
    free_clock,
    mission_end_index,
    split_into_parts,
    write_part,
)

pytestmark = pytest.mark.django_db

INTERVAL = timedelta(seconds=150)  # past `sorties_interval_s` x 0.8 even after a slow pass


class Scenario:
    """A temp log folder with a fixture mission written part by part, a tracker and a controllable clock."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        fixture: str = "typical",
        uid: str = LIVE_UID,
        parts: int = 4,
        live: LiveConfig | None = None,
    ) -> None:
        self.logs = tmp_path / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.uid = uid
        base = make_config(tmp_path / "data", self.logs, after_archive="keep")
        self.cfg: Config = replace(
            base,
            live=live or LiveConfig(enabled=True, interval_s=30.0, sorties_interval_s=120.0, aggregates_interval_s=1.0),
        )
        self.lines = fixture_lines(fixture)
        self.end_at = mission_end_index(self.lines) if any(" AType:7" in line for line in self.lines) else None
        self.parts = split_into_parts(self.lines, parts)
        self.tracker = LiveTracker(self.cfg, cost_clock=free_clock)
        self.clock = T0

    def write(self, count: int, *, last_half: bool = False) -> None:
        """Parts 0..count-1 as DServer has them: the last one cut in half (an unfinished line) when `last_half`."""
        for index in range(count):
            lines = self.parts[index]
            if index == count - 1 and last_half:
                write_part(self.logs, self.uid, index, lines[: len(lines) // 2], self.clock, newline_at_end=False)
            else:
                write_part(self.logs, self.uid, index, lines, self.clock)

    def tick(self, gap: timedelta = INTERVAL) -> None:
        self.clock += gap
        self.tracker.tick(self.clock)

    def finish(self) -> None:
        """All parts complete (AType 7 included) and old enough: the normal ingest takes the mission."""
        self.clock += INTERVAL
        for index in range(len(self.parts)):
            write_part(self.logs, self.uid, index, self.parts[index], self.clock - timedelta(hours=1))
        self.clock += timedelta(minutes=30)
        self.ingest()

    def ingest(self) -> None:
        ingest_once(self.cfg, default_pipeline(self.cfg), now=lambda: self.clock)

    def mission(self) -> Mission:
        return Mission.objects.get(mission_uid=self.uid)


def one_shot_dump(tmp_path: Path, **kwargs: object) -> dict[str, list[str]]:
    """What a plain ingest of the complete mission writes, left out of the database again afterwards."""
    with transaction.atomic():
        reference = Scenario(tmp_path / "reference", **kwargs)  # type: ignore[arg-type]
        reference.write(len(reference.parts))
        reference.finish()
        dump = canonical_dump()
        transaction.set_rollback(True)
    assert not Mission.objects.exists()
    return dump


def elo_state() -> list[tuple[object, ...]]:
    players = list(
        Player.objects.order_by("account_uuid").values_list(
            "account_uuid", "elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games"
        )
    )
    types = list(
        PlayerAircraft.objects.order_by("player__account_uuid", "aircraft__log_name").values_list("elo", "elo_games")
    )
    return [*players, *types]


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    return Scenario(tmp_path / "live")


# --- the running mission is saved provisionally --------------------------------------------------------------------


def test_a_pass_saves_the_running_mission_provisionally(scenario: Scenario) -> None:
    scenario.write(3)
    scenario.tick()

    mission = scenario.mission()
    assert mission.is_live
    assert not mission.completed_cleanly
    assert PlayerSortie.objects.filter(mission=mission).exists()
    assert mission.sorties_total > 0
    assert Player.objects.filter(sorties__gt=0).exists()  # level 2 moved right away
    assert LivePlayer.objects.exists()  # and online now comes from the same pass


def test_final_save_equals_a_one_shot_ingest_and_urls_stay(tmp_path: Path) -> None:
    reference = one_shot_dump(tmp_path)
    scenario = Scenario(tmp_path / "live")
    scenario.write(1, last_half=True)
    scenario.tick()
    first_pk = scenario.mission().pk
    scenario.write(2)
    scenario.tick()
    scenario.write(3, last_half=True)
    scenario.tick()
    before = {(s.account_uuid, s.spawn_tick): s.pk for s in PlayerSortie.objects.filter(mission=scenario.mission())}
    assert before
    scenario.write(4, last_half=True)
    scenario.tick()
    before = {(s.account_uuid, s.spawn_tick): s.pk for s in PlayerSortie.objects.filter(mission=scenario.mission())}

    scenario.finish()

    mission = scenario.mission()
    assert mission.pk == first_pk  # (b) the mission URL never changed
    assert not mission.is_live
    assert mission.completed_cleanly
    after = {(s.account_uuid, s.spawn_tick): s.pk for s in PlayerSortie.objects.filter(mission=mission)}
    assert set(before) <= set(after)  # (b) no sortie of the last provisional pass is lost ...
    assert all(after[key] == pk for key, pk in before.items())  # ... and every one keeps its URL
    assert Mission.objects.count() == 1
    assert diff_dumps(canonical_dump(), reference) == []  # (a)


def test_passes_with_nothing_new_change_nothing(scenario: Scenario) -> None:
    scenario.write(3)
    scenario.tick()
    first = canonical_dump()
    pks = sorted(PlayerSortie.objects.values_list("pk", flat=True))
    version = current_data_version()

    scenario.tick()  # (c) a second pass over the same lines writes nothing and keeps the caches valid (TD-28)
    assert current_data_version() == version

    scenario.tracker = LiveTracker(scenario.cfg, cost_clock=free_clock)  # a restart re-reads and saves once more ...
    scenario.tick()

    assert diff_dumps(canonical_dump(), first) == []  # ... with exactly the same rows
    assert sorted(PlayerSortie.objects.values_list("pk", flat=True)) == pks


def test_level2_after_the_final_save_equals_a_rebuild(scenario: Scenario) -> None:
    scenario.write(2)
    scenario.tick()
    scenario.write(4, last_half=True)
    scenario.tick()
    scenario.finish()
    incremental = canonical_dump()

    cfg = scenario.cfg
    rebuild_aggregates(cfg.ratings, cfg.tours, marks=cfg.marks, score=cfg.score, board=cfg.board)

    assert diff_dumps(canonical_dump(), incremental) == []  # (d)


def test_elo_is_untouched_by_provisional_passes_and_applied_at_the_final_save(tmp_path: Path) -> None:
    earlier = Scenario(tmp_path / "earlier", fixture="most_bailouts", uid="2026-09-18_20-00-00")
    earlier.write(len(earlier.parts))
    earlier.finish()
    live = Scenario(tmp_path / "live", fixture="most_bailouts", uid="2026-09-19_21-00-00")
    live.cfg = replace(live.cfg, data_dir=earlier.cfg.data_dir)
    live.tracker = LiveTracker(live.cfg, cost_clock=free_clock)
    before = elo_state()

    live.write(len(live.parts), last_half=True)
    live.tick()
    assert live.mission().is_live
    assert Kill.objects.filter(mission__is_live=True, credit=KillCredit.KILL).exists(), "the fixture must have kills"
    assert elo_state() == before  # (e)

    # an ordinary ingest of another mission recomputes all ratings: the live kills must not enter them
    other = Scenario(tmp_path / "other", fixture="two_mission_ends", uid="2026-09-17_20-00-00")
    other.cfg = replace(other.cfg, data_dir=earlier.cfg.data_dir)
    other.write(len(other.parts))
    other.finish()
    assert live.mission().is_live
    with_live = elo_state()
    # the reference: the same database without the running mission (earlier + other only), ratings replayed
    SiteSettings.objects.update_or_create(pk=1, defaults={"show_live_sorties": False})
    discard_stale_provisional(live.cfg, keep="")
    assert not Mission.objects.filter(is_live=True).exists()
    recompute_ratings(live.cfg.ratings)
    assert elo_state() == with_live  # the live kills never entered the ratings another ingest worked out
    SiteSettings.objects.update_or_create(pk=1, defaults={"show_live_sorties": True})

    live.tick()  # saved again
    live.finish()
    assert not live.mission().is_live
    assert elo_state() != before  # the final save applied the ratings


def test_rebuild_and_reprocess_leave_a_running_mission_and_the_ratings_alone(tmp_path: Path) -> None:
    earlier = Scenario(tmp_path / "earlier", fixture="most_bailouts", uid="2026-09-18_20-00-00")
    earlier.write(len(earlier.parts))
    earlier.finish()
    live = Scenario(tmp_path / "live", fixture="most_bailouts", uid="2026-09-19_21-00-00")
    live.cfg = replace(live.cfg, data_dir=earlier.cfg.data_dir)
    live.tracker = LiveTracker(live.cfg, cost_clock=free_clock)
    ratings = elo_state()
    live.write(len(live.parts), last_half=True)
    live.tick()
    live.tracker._flush_pending(live.tracker._running)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    cfg = live.cfg

    rebuild_aggregates(cfg.ratings, cfg.tours, marks=cfg.marks, score=cfg.score, board=cfg.board)
    assert live.mission().is_live
    assert elo_state() == ratings

    reprocess(
        cfg,
        default_pipeline(cfg, defer_ratings=True),
        workers=1,
        executor_factory=ThreadPoolExecutor,
    )  # every archived mission: the running one has no archive and is not touched
    assert live.mission().is_live
    assert Mission.objects.get(mission_uid=earlier.uid).pk
    assert elo_state() == ratings


def test_level_two_in_the_middle_of_a_live_mission_equals_a_rebuild(tmp_path: Path) -> None:
    """On top of an earlier mission, with level 2 slower than level 1 (pending work merges between its passes). The
    stat thresholds are the one thing a rebuild adds: they wait for the final save by design."""
    earlier = Scenario(tmp_path / "earlier", fixture="most_bailouts", uid="2026-09-18_20-00-00")
    earlier.write(len(earlier.parts))
    earlier.finish()
    live = Scenario(
        tmp_path / "live",
        fixture="most_bailouts",
        uid="2026-09-19_21-00-00",
        live=LiveConfig(enabled=True, interval_s=30.0, sorties_interval_s=100.0, aggregates_interval_s=100000.0),
    )
    live.cfg = replace(live.cfg, data_dir=earlier.cfg.data_dir)
    live.tracker = LiveTracker(live.cfg, cost_clock=free_clock)
    for count in (1, 2, 3, 4):
        live.write(count, last_half=True)
        live.tick()
    running = live.tracker._running  # pyright: ignore[reportPrivateUsage]
    assert running is not None
    assert live.tracker._flush_pending(running)  # pyright: ignore[reportPrivateUsage]
    incremental = canonical_dump()

    cfg = live.cfg
    rebuild_aggregates(cfg.ratings, cfg.tours, marks=cfg.marks, score=cfg.score, board=cfg.board)

    rebuilt = canonical_dump()
    differing = diff_dumps(incremental, rebuilt)
    assert [d for d in differing if not d.startswith(("StatThreshold", "TourStatThreshold"))] == [], differing


def test_toggle_off_behaves_as_before(scenario: Scenario) -> None:
    SiteSettings.objects.update_or_create(pk=1, defaults={"show_live_sorties": False})
    version = current_data_version()
    scenario.write(3)
    scenario.tick()

    assert not Mission.objects.exists()  # (f) nothing saved provisionally
    assert not PlayerSortie.objects.exists()
    assert LivePlayer.objects.exists()  # online now still works
    assert LiveMission.objects.get().is_running
    assert current_data_version() == version


def test_switching_the_toggle_off_removes_the_provisional_mission(scenario: Scenario) -> None:
    scenario.write(3)
    scenario.tick()
    assert scenario.mission().is_live
    assert Player.objects.filter(sorties__gt=0).exists()

    SiteSettings.objects.update_or_create(pk=1, defaults={"show_live_sorties": False})
    scenario.tick()

    assert not Mission.objects.exists()
    assert not Player.objects.filter(sorties__gt=0).exists()  # their counters went back to zero
    assert LivePlayer.objects.exists()

    SiteSettings.objects.update_or_create(pk=1, defaults={"show_live_sorties": True})
    scenario.tick()
    assert scenario.mission().is_live  # and back on: saved again


def test_level_one_and_level_two_run_on_their_own_intervals(tmp_path: Path) -> None:
    scenario = Scenario(
        tmp_path / "live",
        live=LiveConfig(enabled=True, interval_s=30.0, sorties_interval_s=120.0, aggregates_interval_s=400.0),
    )
    scenario.write(2)
    scenario.tick()  # the first pass does both
    first_total = Player.objects.aggregate(n=Sum("sorties"))["n"]
    assert first_total

    scenario.write(4, last_half=True)
    scenario.tick(timedelta(seconds=150))  # level 1 is due, level 2 is not
    level1 = PlayerSortie.objects.filter(role="pilot").count()
    assert level1 > first_total
    assert Player.objects.aggregate(n=Sum("sorties"))["n"] == first_total
    assert scenario.mission().sorties_total == level1  # the mission page's own counters follow level 1

    scenario.tick(
        timedelta(seconds=300)
    )  # 450 s since level 2 last ran (the mission is not idle yet: 10 min): it picks up everything touched since
    assert Player.objects.aggregate(n=Sum("sorties"))["n"] == level1


def test_level_two_only_at_the_end_when_the_interval_is_zero(tmp_path: Path) -> None:
    scenario = Scenario(
        tmp_path / "live",
        live=LiveConfig(enabled=True, interval_s=30.0, sorties_interval_s=120.0, aggregates_interval_s=0.0),
    )
    scenario.write(3)
    scenario.tick()
    scenario.tick()
    assert PlayerSortie.objects.exists()
    assert not Player.objects.filter(sorties__gt=0).exists()
    scenario.write(4)
    scenario.finish()
    assert Player.objects.filter(sorties__gt=0).exists()


# --- robustness ----------------------------------------------------------------------------------------------------


def test_a_watch_restart_in_the_middle_resumes_without_duplicates(tmp_path: Path) -> None:
    reference = one_shot_dump(tmp_path)
    scenario = Scenario(tmp_path / "live")
    scenario.write(2)
    scenario.tick()
    sorties = PlayerSortie.objects.count()

    scenario.tracker = LiveTracker(scenario.cfg, cost_clock=free_clock)  # crash and restart: no memory at all
    scenario.write(3)
    scenario.tick()
    assert Mission.objects.count() == 1
    assert PlayerSortie.objects.count() >= sorties
    scenario.tracker = LiveTracker(scenario.cfg, cost_clock=free_clock)
    scenario.write(4, last_half=True)
    scenario.tick()
    scenario.finish()

    assert diff_dumps(canonical_dump(), reference) == []


def test_a_mission_that_never_logs_the_end_is_finished_by_the_idle_rule(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path / "live", fixture="most_bailouts", parts=3)
    scenario.lines = [line for line in scenario.lines if " AType:7" not in line]
    scenario.parts = split_into_parts(scenario.lines, 3)
    reference_parts = scenario.parts

    with transaction.atomic():
        reference = Scenario(tmp_path / "reference", fixture="most_bailouts", parts=3)
        reference.parts = reference_parts
        reference.write(3)
        reference.finish()
        expected = canonical_dump()
        transaction.set_rollback(True)

    scenario.write(3)
    scenario.tick()
    assert scenario.mission().is_live
    scenario.tick()
    assert scenario.mission().is_live  # no end in sight: still provisional, however often it is looked at

    scenario.finish()  # an hour of silence: complete by the idle rule
    assert not scenario.mission().is_live
    assert diff_dumps(canonical_dump(), expected) == []


def test_the_old_mission_is_finalised_before_the_next_ones_provisional_passes_write(tmp_path: Path) -> None:
    """The maintainer's rule: A's final save (full level 2 and Elo) goes first; B's passes write nothing before it."""
    reference = one_shot_dump(tmp_path)
    scenario = Scenario(tmp_path / "live")
    scenario.write(3)
    scenario.tick()
    old = scenario.mission()
    assert old.is_live

    other = "2026-09-19_23-30-00"  # the server restarted: a newer mission's first part appears
    scenario.write(len(scenario.parts))
    for index in range(len(scenario.parts)):  # A is over (AType 7 and old), but ingest has not run yet
        write_part(scenario.logs, scenario.uid, index, scenario.parts[index], scenario.clock - timedelta(hours=1))
    write_part(scenario.logs, other, 0, scenario.parts[0], scenario.clock + timedelta(minutes=5))
    scenario.clock += timedelta(minutes=10)
    scenario.tracker.tick(scenario.clock)
    assert scenario.tracker.following == other
    assert not Mission.objects.filter(mission_uid=other).exists()  # B waits for A's final save
    assert Mission.objects.get(mission_uid=scenario.uid).is_live

    scenario.ingest()
    final = Mission.objects.get(mission_uid=scenario.uid)
    assert final.pk == old.pk
    assert not final.is_live
    assert diff_dumps(canonical_dump(), reference) == []  # A's rows, level 2 and Elo are complete before B writes

    scenario.tick()
    assert Mission.objects.get(mission_uid=other).is_live


def test_a_provisional_mission_whose_files_vanished_is_deleted(scenario: Scenario) -> None:
    scenario.write(3)
    scenario.tick()
    assert scenario.mission().is_live
    for path in scenario.logs.iterdir():
        path.unlink()  # the admin cleaned the folder; ingest never saw the mission end

    scenario.tick()

    assert not Mission.objects.exists()
    assert not Player.objects.filter(sorties__gt=0).exists()
    assert not LiveMission.objects.get().is_running


def test_a_new_part_file_is_followed(scenario: Scenario) -> None:
    scenario.write(2)
    scenario.tick()
    before = scenario.mission().sorties_total
    scenario.write(4)  # the log rotated into new part files while we were not looking
    scenario.tick()
    assert scenario.mission().sorties_total >= before
    scenario.finish()
    assert Mission.objects.count() == 1


def test_a_busy_writer_lock_skips_the_pass_and_the_next_one_catches_up(scenario: Scenario) -> None:
    scenario.write(3)
    with WriterLock(scenario.cfg.data_dir, "reprocess"):
        scenario.tick()
        assert not Mission.objects.exists()  # a reprocess is running: no provisional write
        assert LivePlayer.objects.exists()  # online now needs no lock
    scenario.tick()
    assert scenario.mission().is_live


def test_a_final_save_already_there_is_never_turned_back_into_a_provisional_one(scenario: Scenario) -> None:
    scenario.write(3)
    scenario.tick()
    other_process = Scenario(scenario.logs.parent / "x", uid=scenario.uid)
    other_process.cfg = scenario.cfg
    other_process.parts = scenario.parts
    other_process.logs = scenario.logs
    other_process.clock = scenario.clock
    other_process.finish()  # the CLI `ingest` took the finished mission while watch's tracker still follows it
    assert not scenario.mission().is_live

    scenario.write(3)
    scenario.tick()  # the files are complete now; the tracker must not re-open the mission
    assert not scenario.mission().is_live


def test_hidden_players_and_missions_stay_hidden(scenario: Scenario) -> None:
    scenario.write(2)
    scenario.tick()
    Mission.objects.update(is_hidden=True)
    hidden = Player.objects.filter(sorties__gt=0).first()
    assert hidden is not None
    Player.objects.filter(pk=hidden.pk).update(is_hidden=True)

    scenario.write(3)
    scenario.tick()
    scenario.finish()

    assert scenario.mission().is_hidden
    assert Player.objects.get(pk=hidden.pk).is_hidden


# --- cost and the CPU cap ------------------------------------------------------------------------------------------


def test_the_cpu_cap_follows_the_measured_cost_not_the_speed_of_the_machine(scenario: Scenario) -> None:
    """NFR-INS-5 with a fake cost clock: a pass costs about 4 s, so the next waits >= 160 s."""
    scenario.tracker = LiveTracker(scenario.cfg, cost_clock=SteppingClock(2.0))
    scenario.write(2)
    scenario.tick()
    saved_at = scenario.mission().ended_at
    scenario.write(4, last_half=True)

    scenario.tick(timedelta(seconds=150))
    assert scenario.mission().ended_at == saved_at  # past the 120 s interval, inside the backed-off one
    scenario.tick(timedelta(seconds=300))
    assert scenario.mission().ended_at != saved_at


def test_the_sorties_interval_is_not_the_online_interval(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path / "live", live=LiveConfig(enabled=True, interval_s=30.0, sorties_interval_s=600.0))
    scenario.write(3)
    scenario.tick()
    saved_at = scenario.mission().ended_at
    updated = LiveMission.objects.get().updated_at
    scenario.write(4, last_half=True)
    scenario.tick(timedelta(seconds=40))
    assert LiveMission.objects.get().updated_at > updated  # online now every 30 s
    assert scenario.mission().ended_at == saved_at  # sorties every 10 minutes
    scenario.tick(timedelta(seconds=500))
    assert scenario.mission().ended_at != saved_at


# --- watch ---------------------------------------------------------------------------------------------------------


def test_watch_saves_the_running_mission_and_the_next_tick_after_the_end_finalizes_it(scenario: Scenario) -> None:
    scenario.write(3)
    cfg = replace(scenario.cfg, ingest=replace(scenario.cfg.ingest, watch_interval_s=0.01))
    pipeline = default_pipeline(cfg)
    assert watch(cfg, pipeline, max_ticks=1, now=lambda: scenario.clock, stop=threading.Event()) == 1
    assert scenario.mission().is_live

    for index in range(len(scenario.parts)):
        write_part(scenario.logs, scenario.uid, index, scenario.parts[index], scenario.clock - timedelta(hours=1))
    scenario.clock += timedelta(minutes=30)
    watch(cfg, pipeline, max_ticks=1, now=lambda: scenario.clock)
    assert not scenario.mission().is_live


def test_live_disabled_in_the_config_saves_nothing(tmp_path: Path) -> None:
    scenario = Scenario(tmp_path / "live", live=LiveConfig(enabled=False))
    scenario.write(3)
    cfg = replace(scenario.cfg, ingest=replace(scenario.cfg.ingest, watch_interval_s=0.01))
    watch(cfg, make_pipeline(FakeSteps()), max_ticks=1, now=lambda: scenario.clock)
    assert not Mission.objects.exists()
    assert not LiveMission.objects.exists()


# --- review round: a failed final ingest, races, pending level 2, quiet passes, one duty budget --------------------


def test_a_failed_final_ingest_does_not_block_the_next_mission_for_ever(scenario: Scenario) -> None:
    scenario.write(3)
    scenario.tick()
    old = scenario.mission()
    assert old.is_live
    IngestRun.objects.create(
        mission_uid=scenario.uid, status=IngestStatus.FAILED, fingerprint="x", started_at=scenario.clock, files=[]
    )  # the normal ingest of the first mission failed and will never succeed

    other = "2026-09-19_23-30-00"
    write_part(scenario.logs, other, 0, scenario.parts[0], scenario.clock + timedelta(minutes=5))
    scenario.clock += timedelta(minutes=10)
    scenario.tracker.tick(scenario.clock)

    assert Mission.objects.get(mission_uid=other).is_live  # not stuck "waiting"
    assert not Mission.objects.filter(mission_uid=scenario.uid).exists()  # no stale "Live" mission left behind


def test_waiting_for_the_old_mission_does_not_retry_at_every_tick(scenario: Scenario) -> None:
    scenario.write(3)
    scenario.tick()
    other = "2026-09-19_23-30-00"
    write_part(scenario.logs, other, 0, scenario.parts[0], scenario.clock + timedelta(minutes=5))
    scenario.clock += timedelta(minutes=10)
    scenario.tracker.tick(scenario.clock)  # waits: the old mission has no failed run, it is just not finalised yet
    running = scenario.tracker._running  # pyright: ignore[reportPrivateUsage]
    assert running is not None
    assert running.last_persist == scenario.clock  # asked again after an interval, not at the very next tick


class _RacingLock(WriterLock):
    """Takes the lock, then does what a concurrent `il2ks ingest` would have done just before: the final save."""

    def __enter__(self) -> "_RacingLock":
        super().__enter__()
        Mission.objects.update(is_live=False)
        return self


def test_discarding_stale_missions_rechecks_under_the_lock(scenario: Scenario, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario.write(3)
    scenario.tick()
    for path in scenario.logs.iterdir():
        path.unlink()  # looks stale ...
    monkeypatch.setattr(live_module, "WriterLock", _RacingLock)  # ... but the final save lands before we get the lock

    discard_stale_provisional(scenario.cfg, keep=None)

    assert Mission.objects.filter(mission_uid=scenario.uid).exists()  # a final-saved mission is never discarded


def test_a_final_save_by_another_process_stops_the_saves_but_not_online_now(scenario: Scenario) -> None:
    scenario.write(2)
    scenario.tick()
    Mission.objects.update(is_live=False)  # `il2ks ingest` finalised it while the mission kept going
    updated = LiveMission.objects.get().updated_at
    scenario.write(4, last_half=True)
    scenario.tick()
    scenario.tick()

    assert not scenario.mission().is_live  # never turned back
    assert LiveMission.objects.get().updated_at > updated  # online now keeps following
    assert scenario.tracker.following == scenario.uid


def test_pending_level_two_work_is_applied_when_the_mission_ends(tmp_path: Path) -> None:
    scenario = Scenario(
        tmp_path / "live",
        live=LiveConfig(enabled=True, interval_s=30.0, sorties_interval_s=120.0, aggregates_interval_s=100000.0),
    )
    scenario.write(2)
    scenario.tick()
    scenario.write(3)
    scenario.tick()  # level 1 only: level 2 is not due for a long time
    assert Player.objects.aggregate(n=Sum("sorties"))["n"] < PlayerSortie.objects.filter(role="pilot").count()
    scenario.write(len(scenario.parts))  # the whole log, AType 7 included, but not settled yet
    scenario.tick()
    assert scenario.tracker.following is None  # the end was seen

    level1 = PlayerSortie.objects.filter(role="pilot").count()
    assert Player.objects.aggregate(n=Sum("sorties"))["n"] == level1  # nothing owed to level 2 is dropped


def test_a_pass_with_no_new_lines_writes_nothing_and_leaves_the_caches_alone(scenario: Scenario) -> None:
    scenario.write(3)
    scenario.tick()
    version = current_data_version()
    scenario.tick()
    scenario.tick()
    assert current_data_version() == version


def test_all_three_kinds_of_pass_share_one_cpu_budget(scenario: Scenario) -> None:
    """NFR-INS-5: online snapshot, level-1 save and level-2 recompute together stay below 2.5 % of a core."""
    scenario.tracker = LiveTracker(scenario.cfg, cost_clock=SteppingClock(2.0))
    scenario.write(2)
    scenario.tick()  # every kind runs once: about 6 s of work, so the next one is 240 s away
    updated = LiveMission.objects.get().updated_at
    scenario.write(4, last_half=True)

    scenario.tick(timedelta(seconds=100))
    assert LiveMission.objects.get().updated_at == updated
    scenario.tick(timedelta(seconds=200))
    assert LiveMission.objects.get().updated_at > updated


# --- a log that holds the same mission twice -----------------------------------------------------------------------


def test_a_log_with_the_same_mission_twice_fails_with_a_clear_message_and_writes_nothing(tmp_path: Path) -> None:
    """Found by a real run whose feeder appended the mission twice: the replay yields every sortie twice and the save
    died with a raw `IntegrityError` (UNIQUE playersortie.mission_id, account_uuid, spawn_tick) and a traceback."""
    scenario = Scenario(tmp_path / "live", parts=1)
    scenario.parts = [scenario.lines + scenario.lines]
    scenario.write(1)
    scenario.clock += timedelta(hours=1)
    write_part(scenario.logs, scenario.uid, 0, scenario.parts[0], scenario.clock - timedelta(hours=1))

    scenario.ingest()

    run = IngestRun.objects.get(mission_uid=scenario.uid)
    assert run.status == IngestStatus.FAILED
    assert "same sortie twice" in run.error
    assert "duplicated" in run.error
    assert "Traceback" not in run.error
    assert "IntegrityError" not in run.error
    assert not Mission.objects.exists()
    assert not PlayerSortie.objects.exists()


def test_a_doubled_log_stops_the_provisional_saves_but_not_online_now(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    scenario = Scenario(tmp_path / "live", parts=1)
    scenario.parts = [scenario.lines[: scenario.end_at] * 2 if scenario.end_at else scenario.lines * 2]
    scenario.write(1)

    scenario.tick()
    scenario.tick()

    assert not Mission.objects.exists()
    assert LivePlayer.objects.exists()
    assert sum("same sortie twice" in r.getMessage() for r in caplog.records) == 1  # said once, not every pass
