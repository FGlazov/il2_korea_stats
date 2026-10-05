"""The admin's overrides of the game rules in action (maintainer decision 2026-10-05, FR-ADM-7): the effective rules,
pending vs applied, `watch` and `rebuild-aggregates` applying them in one rebuild, display-only settings at once, and
the admin pages (permission, CSRF, validation)."""

from pathlib import Path

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client

from il2ks.db.models import Player, PlayerSortie, SiteSettings, StatThreshold, Tour
from il2ks.db.site import current_data_version, get_site_settings
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.reprocess import rebuild_all
from il2ks.ingest.rule_store import (
    applied_overrides,
    effective_config,
    pending_effects,
    pending_fields,
    wanted_overrides,
)
from il2ks.ingest.runner import default_pipeline
from il2ks.ingest.score_apply import rescore_with_wanted
from il2ks.ops.checks import admin_rules_check
from il2ks.ops.doctor import Level
from il2ks.queries.leaderboards import rules as board_rules
from tests.factories import account, meta, mission, save, sortie
from tests.ops_helpers import make_instance

pytestmark = pytest.mark.django_db


def choose(**overrides: object) -> None:
    """What the admin page stores for a rescore/retour field: the wanted side only."""
    row = get_site_settings()
    row.rule_settings = {k.replace("__", "."): v for k, v in overrides.items()}
    row.save()


def seed() -> None:
    killer = sortie(0, 1, combat_role="air_superiority", flight_time_s=1800.0, kills_air_pvp=1, kills_air_ai=0)
    save(mission((killer,)))


def air(player: int) -> float:
    return PlayerSortie.objects.get(account_uuid=account(player)).air_points


def totals() -> list[tuple[object, ...]]:
    players = Player.objects.order_by("account_uuid").values_list("account_uuid", "score_air", "score_ground")
    sorties = PlayerSortie.objects.order_by("account_uuid").values_list("account_uuid", "air_points")
    return [tuple(r) for r in players] + [tuple(r) for r in sorties]


def test_the_effective_rules_are_the_file_overlaid_with_the_applied_values(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False, extra_toml="[score]\nair_kill_pvp = 7\nair_kill_ai = 1\n")
    assert effective_config(cfg).score.air_kill_pvp == 7.0
    row = get_site_settings()
    row.rule_settings = {"score.air_kill_pvp": 20.0}  # wanted only: not in force yet
    row.rule_settings_applied = {"score.air_kill_ai": 4.0}
    row.save()
    rules = effective_config(cfg)
    assert (rules.score.air_kill_pvp, rules.score.air_kill_ai) == (7.0, 4.0)  # applied wins over the file
    assert rules.data_dir == cfg.data_dir
    row.rule_settings_applied = {}
    row.save()
    assert effective_config(cfg).score.air_kill_ai == 1.0  # nothing applied: the file again


def test_a_score_change_is_pending_then_applied_by_watch_and_incremental_equals_rebuild(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    seed()
    assert air(1) == 10.0
    version = current_data_version()
    choose(score__air_kill_pvp=20.0)
    assert [f.key for f in pending_fields()] == ["score.air_kill_pvp"]
    assert pending_effects() == {"rescore"}
    assert air(1) == 10.0  # nothing moves before the apply
    assert effective_config(cfg).score.air_kill_pvp == 10.0

    assert rescore_with_wanted(cfg)
    assert air(1) == 20.0
    assert applied_overrides() == wanted_overrides() == {"score.air_kill_pvp": 20.0}
    assert pending_effects() == frozenset()
    assert current_data_version() > version
    assert not rescore_with_wanted(cfg)  # nothing pending

    # a new mission is scored with the applied value, and a full rebuild agrees with the incremental result
    other = sortie(0, 2, combat_role="air_superiority", flight_time_s=1800.0, kills_air_pvp=1, kills_air_ai=0)
    default_pipeline(cfg).save(mission((other,)), meta("2026-09-20_22-34-13"))
    assert air(2) == 20.0
    incremental = totals()
    run = effective_config(cfg)
    rebuild_aggregates(run.ratings, run.tours, marks=run.marks, score=run.score, board=run.board)
    assert totals() == incremental


def test_several_pending_changes_are_one_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    seed()
    rebuilds: list[int] = []
    from il2ks.ingest import score_apply

    real = score_apply.rebuild_aggregates

    def counting(*args: object, **kwargs: object) -> None:
        rebuilds.append(1)
        real(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(score_apply, "rebuild_aggregates", counting)
    row = get_site_settings()
    row.rule_settings = {"score.air_kill_pvp": 20.0, "killboard.assists": True, "ratings.k": 16.0}
    row.score_flight = {"enabled": True, "per_hour": 2.0}
    row.save()
    assert rescore_with_wanted(cfg)
    assert rebuilds == [1]
    assert pending_effects() == frozenset()
    assert air(1) == 20.0 + 1.0  # half an hour at 2 points
    assert get_site_settings().killboard_assists is True


def test_a_tour_mode_change_retours_in_the_same_apply(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    seed()
    assert Tour.objects.get().mode == "monthly"
    choose(tours__mode="manual", score__air_kill_pvp=20.0)
    assert pending_effects() == {"rescore", "retour"}
    assert effective_config(cfg).tours.mode == "monthly"

    assert rescore_with_wanted(cfg)

    assert Tour.objects.get().mode == "manual"
    assert effective_config(cfg).tours.mode == "manual"
    assert air(1) == 20.0
    assert pending_effects() == frozenset()


def test_a_failed_rebuild_leaves_the_change_pending_and_level_two_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    seed()
    before = totals()
    choose(score__air_kill_pvp=20.0)

    def explode(*args: object, **kwargs: object) -> None:
        # writes first, then dies: the writes must roll back with the applied values
        PlayerSortie.objects.update(air_points=999.0)
        raise RuntimeError("boom")

    from il2ks.ingest import score_apply

    monkeypatch.setattr(score_apply, "rebuild_aggregates", explode)
    with pytest.raises(RuntimeError):
        rescore_with_wanted(cfg)
    assert totals() == before
    assert applied_overrides() == {}
    assert pending_effects() == {"rescore"}


def test_rebuild_aggregates_command_applies_pending_changes_and_retour_applies_the_tours(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    seed()
    choose(score__air_kill_pvp=20.0, tours__mode="manual")

    rebuild_all(cfg)  # without --retour the tour setting waits
    assert air(1) == 20.0
    assert applied_overrides() == {"score.air_kill_pvp": 20.0}
    assert pending_effects() == {"retour"}
    assert Tour.objects.get().mode == "monthly"

    rebuild_all(cfg, reassign_tours=True)
    assert Tour.objects.get().mode == "manual"
    assert pending_effects() == frozenset()


def test_a_reprocess_rule_applies_at_once_to_new_missions(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    row = get_site_settings()
    row.rule_settings = row.rule_settings_applied = {"rules.credit_rams": False, "replay.assist_min_damage": 0.5}
    row.save()
    rules = effective_config(cfg)
    assert rules.replay.toggles.credit_rams is False
    assert rules.replay.assist_min_damage == 0.5
    assert pending_effects() == frozenset()  # nothing to apply: older missions need a reprocess (the page says)


def test_doctor_says_which_rules_the_admin_set_and_warns_while_one_is_pending(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    findings = list(admin_rules_check(cfg))
    assert [f.level for f in findings] == [Level.OK]
    assert "il2ks.toml" in findings[0].title

    choose(score__air_kill_pvp=20.0)
    levels = [f.level for f in admin_rules_check(cfg)]
    assert levels == [Level.OK, Level.WARN]  # nothing applied yet: no admin rule in force, but one waits
    row = get_site_settings()
    row.rule_settings_applied = {"score.air_kill_pvp": 20.0}
    row.save()
    findings = list(admin_rules_check(cfg))
    assert "1 game rule(s) are set in the admin" in findings[0].title
    assert "score.air_kill_pvp = 20.0" in findings[0].detail


def test_the_tour_list_follows_the_applied_tour_mode(admin: Client) -> None:
    """The "Start a new tour now" button belongs to manual mode: the applied admin mode decides, not the file."""
    url = "/admin/il2ks_db/tour/"
    assert "start/" not in admin.get(url).content.decode()
    row = get_site_settings()
    row.rule_settings = {"tours.mode": "manual"}  # chosen, not applied yet
    row.save()
    assert "start/" not in admin.get(url).content.decode()
    row.rule_settings_applied = {"tours.mode": "manual"}
    row.save()
    assert "start/" in admin.get(url).content.decode()


def test_the_boards_follow_a_saved_minimum_at_once() -> None:
    row = get_site_settings()
    assert board_rules(row).min_sorties == 5
    row.rule_settings_applied = {"score.min_sorties": 1}
    assert board_rules(row).min_sorties == 1
    assert board_rules(None).min_sorties == 5


# --- the admin pages -------------------------------------------------------------------------------------------------

SCORING = "/admin/score/"
TOURS = "/admin/tours/"
RULES = "/admin/rules/"
BOARDS = "/admin/leaderboards/"


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def test_the_scoring_page_saves_the_wanted_values_and_shows_pending(admin: Client) -> None:
    page = admin.get(SCORING).content.decode()
    assert 'name="score.air_kill_pvp"' in page
    version = current_data_version()
    assert (
        admin.post(SCORING, {"score.air_kill_pvp": "12,5", "per_hour": "1", "score.air_kill_ai": ""}).status_code == 302
    )
    assert wanted_overrides() == {"score.air_kill_pvp": 12.5}
    assert applied_overrides() == {}  # a scoring change waits for watch
    assert current_data_version() > version
    page = admin.get(SCORING).content.decode()
    assert 'value="12.5"' in page
    assert "pending" in page.lower()


def test_blank_resets_a_field_to_the_file(admin: Client) -> None:
    admin.post(SCORING, {"score.air_kill_pvp": "12", "per_hour": "1"})
    assert wanted_overrides() == {"score.air_kill_pvp": 12.0}
    admin.post(SCORING, {"score.air_kill_pvp": "", "per_hour": "1"})
    assert wanted_overrides() == {}


def test_the_page_shows_the_file_value_as_the_default(admin: Client) -> None:
    page = admin.get(SCORING).content.decode()
    assert 'placeholder="10"' in page  # the built-in air_kill_pvp
    assert "il2ks.toml" in page


def test_invalid_input_saves_nothing_and_comes_back_as_typed(admin: Client) -> None:
    response = admin.post(SCORING, {"score.air_kill_pvp": "-4", "score.air_kill_ai": "3", "per_hour": "1"})
    assert response.status_code == 200
    assert wanted_overrides() == {}
    html = response.content.decode()
    assert 'value="-4"' in html
    assert 'value="3"' in html
    assert "score.air_kill_pvp must not be negative" in html


def test_the_flight_time_option_lives_on_the_scoring_page(admin: Client) -> None:
    assert admin.post(SCORING, {"enabled": "on", "per_hour": "2.5"}).status_code == 302
    row = SiteSettings.objects.get()
    assert row.score_flight == {"enabled": True, "per_hour": 2.5}
    bad = admin.post(SCORING, {"enabled": "on", "per_hour": "-1"})
    assert bad.status_code == 200


def test_the_rules_page_applies_at_once_and_says_reprocess(admin: Client) -> None:
    page = admin.get(RULES).content.decode()
    assert 'name="rules.credit_rams"' in page
    assert admin.post(RULES, {"rules.credit_rams": "off", "replay.assist_min_damage": "0.2"}).status_code == 302
    assert applied_overrides() == wanted_overrides() == {"rules.credit_rams": False, "replay.assist_min_damage": 0.2}
    assert "reprocess" in admin.get(RULES).content.decode().lower()


def test_the_leaderboards_page_applies_at_once_and_refreshes_the_marks(admin: Client) -> None:
    StatThreshold.objects.create(
        tour=None, metric="kd", min_sorties=20, population=30, p10=1, p25=1, p50=1, p75=1, p90=1
    )
    version = current_data_version()
    assert admin.post(BOARDS, {"score.min_sorties": "2", "marks.min_sorties": "7"}).status_code == 302
    assert applied_overrides() == {"score.min_sorties": 2, "marks.min_sorties": 7}
    assert board_rules(get_site_settings()).min_sorties == 2
    assert current_data_version() > version
    assert not StatThreshold.objects.filter(min_sorties=20).exists()  # the marks were recomputed under the new minimum


def test_the_tours_page_validates_the_mode_with_its_start_date(admin: Client) -> None:
    bad = admin.post(TOURS, {"tours.mode": "days:14", "tours.start": "", "tour_on_win": "on"})
    assert bad.status_code == 200
    assert wanted_overrides() == {}
    assert not SiteSettings.objects.get().tour_on_win
    ok = admin.post(TOURS, {"tours.mode": "days:14", "tours.start": "2026-09-01", "tour_on_win": "on"})
    assert ok.status_code == 302
    assert wanted_overrides() == {"tours.mode": "days:14", "tours.start": "2026-09-01"}
    assert SiteSettings.objects.get().tour_on_win
    assert pending_effects() == {"retour"}


@pytest.mark.parametrize("url", [SCORING, TOURS, RULES, BOARDS])
def test_every_page_needs_the_change_permission(client: Client, url: str) -> None:
    assert client.get(url).status_code in (302, 403)
    staff = User.objects.create_user("staff", "s@example.org", "x", is_staff=True)
    client.force_login(staff)
    assert client.get(url).status_code == 403
    assert client.post(url, {}).status_code == 403
    staff.user_permissions.add(Permission.objects.get(codename="change_sitesettings"))
    assert Client().get(url).status_code in (302, 403)
    client.force_login(User.objects.get(pk=staff.pk))
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", [SCORING, TOURS, RULES, BOARDS])
def test_every_page_checks_the_csrf_token(url: str) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    assert client.post(url, {"score.min_sorties": "1"}).status_code == 403
    assert wanted_overrides() == {}


def test_the_admin_index_lists_the_rule_pages(admin: Client) -> None:
    html = admin.get("/admin/").content.decode()
    for url in (SCORING, TOURS, RULES, BOARDS):
        assert f'href="{url}"' in html
