"""Admin-configurable achievements (FR-WEB-26): defaults unchanged, off hides everywhere, own names per language and
escaping, threshold validation, a threshold change plus recompute (holders, rarity, incremental == rebuild), the
page."""

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.http import QueryDict
from django.test import Client

from il2ks.core.achievement_rules import Rules, threshold_problem
from il2ks.core.achievements import BY_KEY
from il2ks.db.models import AchievementHolders, PlayerAchievement
from il2ks.db.site import current_data_version, get_site_settings
from il2ks.ingest.achievements import recompute_pending, recompute_with_wanted_rules
from il2ks.ingest.aggregates import rebuild_aggregates, recompute_players
from il2ks.web.achievement_config import AchievementConfig
from il2ks.web.admin_achievements import parse_form
from tests.integration.test_achievements import held, pk, seed, snapshot
from tests.ops_helpers import make_instance
from tests.simple_reads import PROFILE_READS_ALL_TIME, assert_simple_reads

pytestmark = pytest.mark.django_db

URL = "/admin/achievements/"


def configure(**choice: object) -> None:
    row = get_site_settings()
    row.achievements = dict(choice)
    row.save()


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def profile(client: Client, language: str = "en") -> str:
    return client.get(f"/players/{pk(1)}/?tour=all", headers={"Accept-Language": language}).content.decode()


def test_without_configuration_everything_is_as_before(client: Client) -> None:
    seed()
    assert not recompute_pending()
    html = profile(client)
    assert "Charmed Life" in html
    assert "Target-Rich" in html
    assert_simple_reads(client, f"/players/{pk(1)}/?tour=all", max_queries=PROFILE_READS_ALL_TIME)


def test_a_switched_off_achievement_disappears_everywhere(client: Client) -> None:
    seed()
    for url in (f"/players/{pk(1)}/?tour=all", f"/players/{pk(1)}/achievements/?tour=all", "/achievements/?tour=all"):
        assert "Charmed Life" in client.get(url).content.decode(), url
    configure(off=["life_kills", "ground_sortie"])
    for url in (
        "/?tour=all",
        f"/players/{pk(1)}/?tour=all",
        f"/players/{pk(1)}/achievements/?tour=all",
        "/achievements/?tour=all",
    ):
        body = client.get(url).content.decode()
        assert "Charmed Life" not in body, url
        assert "Target-Rich" not in body, url
    assert client.get("/achievements/life_kills/").status_code == 404
    assert client.get("/achievements/career_kills/").status_code == 200  # the others stay
    sortie = PlayerAchievement.objects.filter(key="life_kills").first()
    assert sortie is not None
    assert "Charmed Life" not in client.get(f"/sorties/{sortie.sortie_id}/").content.decode()
    assert_simple_reads(client, f"/players/{pk(1)}/?tour=all", max_queries=PROFILE_READS_ALL_TIME)


def test_a_custom_name_applies_in_its_language_only_and_is_escaped(client: Client) -> None:
    seed()
    configure(names={"ground_sortie": {"de": "Panzer <b>jagd</b>"}}, descriptions={"ground_sortie": {"de": "A&B"}})
    assert "Panzer &lt;b&gt;jagd&lt;/b&gt;" in profile(client, "de")
    assert "<b>jagd</b>" not in profile(client, "de")
    english = profile(client)
    assert "Target-Rich" in english
    assert "Panzer" not in english


def test_a_save_bumps_the_data_version_and_the_page_follows(admin: Client) -> None:
    seed()
    before = current_data_version()
    post = {f"on-{key}": "on" for key in BY_KEY} | {"on-life_kills": ""}
    response = admin.post(URL, post)
    assert response.status_code == 302
    assert current_data_version() > before
    assert "Charmed Life" not in admin.get(f"/players/{pk(1)}/?tour=all").content.decode()


def test_threshold_rules() -> None:
    a = BY_KEY["career_kills"]  # four tiers
    assert threshold_problem(a, (1, 10, 50, 250)) is None
    assert threshold_problem(a, (1, 10, 50)) == "count"
    assert threshold_problem(a, (0, 10, 50, 250)) == "positive"
    assert threshold_problem(a, (1, 10, 10, 250)) == "increasing"
    assert threshold_problem(a, (1, 10, 50, 10**7)) == "large"


def post_of(**fields: str) -> QueryDict:
    data = QueryDict(mutable=True)
    for key in BY_KEY:
        data[f"on-{key}"] = "on"
    for k, v in fields.items():
        data[k.replace("__", "-")] = v
    return data


def test_the_form_rejects_bad_thresholds_and_long_texts_and_saves_nothing_wrong() -> None:
    current = AchievementConfig()
    post = post_of()
    for n, v in enumerate(("5", "3", "9", "20"), 1):
        post[f"thr-career_kills-{n}"] = v
    _, errors, typed = parse_form(post, current)
    assert len(errors) == 1
    assert typed["career_kills"] == ("5", "3", "9", "20")
    post = post_of()
    post["name-career_kills-en"] = "x" * 61
    assert parse_form(post, current)[1]
    post = post_of()
    for n, v in enumerate(("1", "2", "x", "4"), 1):
        post[f"thr-career_kills-{n}"] = v
    assert parse_form(post, current)[1]


def test_the_admin_page_needs_the_permission_and_shows_pending(admin: Client) -> None:
    assert Client().get(URL).status_code == 302  # to the login
    staff = User.objects.create_user("clerk", password="x", is_staff=True)
    clerk = Client()
    clerk.force_login(staff)
    assert clerk.get(URL).status_code == 403
    page = admin.get(URL).content.decode()
    assert "No recompute is pending" in page
    configure(thresholds={"career_kills": [2, 11, 51, 251]})
    assert "A recompute is pending" in admin.get(URL).content.decode()


def test_save_and_reset_per_achievement(admin: Client) -> None:
    post = {f"on-{key}": "on" for key in BY_KEY}
    post |= {f"thr-career_kills-{n}": v for n, v in enumerate(("2", "11", "51", "251"), 1)}
    post["name-career_kills-fr"] = "Chasseur"
    assert admin.post(URL, post).status_code == 302
    stored = get_site_settings().achievements
    assert stored["thresholds"] == {"career_kills": [2, 11, 51, 251]}
    assert stored["names"] == {"career_kills": {"fr": "Chasseur"}}
    assert recompute_pending()
    assert admin.post(URL, post | {"reset": "career_kills"}).status_code == 302
    stored = get_site_settings().achievements
    assert stored["thresholds"] == {}
    assert stored["names"] == {}
    assert not recompute_pending()  # back to what the rows were computed with


def test_a_malformed_stored_row_never_breaks_a_page(client: Client) -> None:
    seed()
    configure(off="nonsense", thresholds={"career_kills": [3, 2], "nope": [1]}, names=[1, 2])
    assert "Sky Hunter" in profile(client)
    assert Rules.from_json({"thresholds": {"career_kills": [1, 10, 50, 250]}}) == Rules()


def test_a_threshold_change_recomputes_holders_and_rarity(tmp_path: Path, client: Client) -> None:
    seed()
    cfg = make_instance(tmp_path, with_db=False)
    assert held(1)["ground_sortie"] == 2  # 60 ground kills: 20 and 50 reached
    before = AchievementHolders.objects.filter(key="ground_sortie", tour=None, tier=2).get()
    assert before.holders == 1
    configure(thresholds={"ground_sortie": [70, 80, 90, 100]})
    assert recompute_pending()
    assert held(1)["ground_sortie"] == 2  # nothing moves before the recompute
    assert "Silver · 50" in client.get(f"/players/{pk(1)}/?tour=all").content.decode()  # old thresholds still shown

    version = current_data_version()
    assert recompute_with_wanted_rules(cfg)
    assert current_data_version() > version
    assert not recompute_pending()
    assert "ground_sortie" not in held(1)
    assert not AchievementHolders.objects.filter(key="ground_sortie").exists()

    incremental = snapshot()
    rebuild_aggregates()
    assert snapshot() == incremental
    recompute_players([pk(1), pk(2)])
    assert snapshot() == incremental
    assert not recompute_with_wanted_rules(cfg)  # nothing pending: nothing to do

    configure(thresholds={"ground_sortie": [10, 30, 90, 100]})
    assert recompute_with_wanted_rules(cfg)
    assert held(1)["ground_sortie"] == 2
    assert "Silver · 30" in client.get(f"/players/{pk(1)}/?tour=all").content.decode()
    again = snapshot()
    rebuild_aggregates()
    assert snapshot() == again


def test_re_enabling_needs_the_recompute(tmp_path: Path) -> None:
    seed()
    cfg = make_instance(tmp_path, with_db=False)
    configure(off=["ground_sortie"])
    assert recompute_with_wanted_rules(cfg)
    assert "ground_sortie" not in held(1)  # a switched-off achievement has no rows (and no rarity)
    configure()
    assert recompute_pending()
    assert recompute_with_wanted_rules(cfg)
    assert held(1)["ground_sortie"] == 2


def test_a_crash_midway_leaves_the_old_rows_and_the_old_rules_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A medal never claims a threshold its holder did not meet, not even after a crash in the middle of a recompute."""
    seed()
    cfg = make_instance(tmp_path, with_db=False)
    before = snapshot()
    configure(thresholds={"ground_sortie": [70, 80, 90, 100]})
    monkeypatch.setattr("il2ks.ingest.achievements.CHUNK", 1)  # one player per batch: the first is done at the crash

    def crash() -> None:
        raise RuntimeError("crash after the rows were rewritten")

    monkeypatch.setattr("il2ks.ingest.achievements.recompute_holders", crash)
    with pytest.raises(RuntimeError):
        recompute_with_wanted_rules(cfg)

    assert snapshot() == before  # the rows still follow the applied thresholds ...
    assert recompute_pending()  # ... and the change is still pending
