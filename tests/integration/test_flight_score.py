"""Optional score for flight time (maintainer request, 2026-10-05): off by default, an admin switch and a rate, applied
to the stored sorties by `watch` (or `rebuild-aggregates`), and incremental == rebuild after a change."""

from dataclasses import replace
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.http import QueryDict
from django.test import Client

from il2ks.core.ratings.score import MAX_FLIGHT_POINTS_PER_HOUR, FlightScore
from il2ks.db.models import Player, PlayerSortie, PlayerTour
from il2ks.db.site import current_data_version, get_site_settings
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.flight_score import flight_score_pending, wanted_flight_score
from il2ks.ingest.score_apply import rescore_with_wanted
from il2ks.web.admin_score import parse_form
from tests.factories import account, meta, mission, save, sortie
from tests.ops_helpers import make_instance

pytestmark = pytest.mark.django_db

URL = "/admin/score/"


def configure(enabled: bool, per_hour: float) -> None:
    row = get_site_settings()
    row.score_flight = {"enabled": enabled, "per_hour": per_hour}
    row.save()


def seed() -> None:
    patrol = sortie(0, 1, combat_role="air_superiority", flight_time_s=3600.0)
    killer = sortie(1, 2, combat_role="air_superiority", flight_time_s=1800.0, kills_air_pvp=1, kills_air_ai=0)
    dead = replace(
        sortie(2, 3, combat_role="air_superiority", flight_time_s=3600.0, is_death=True, is_plane_lost=True),
        outcome="shot_down",
    )
    gunner = sortie(3, 4, aircraft_type="Turret_IL10", role="gunner", flight_time_s=3600.0, combat_role=None)
    save(mission((patrol, killer, dead, gunner)))


def air(player: int) -> float:
    return PlayerSortie.objects.get(account_uuid=account(player)).air_points


def totals() -> list[tuple[object, ...]]:
    players = Player.objects.order_by("account_uuid").values_list("account_uuid", "score_air", "score_ground")
    tours = PlayerTour.objects.order_by("player__account_uuid", "tour_id").values_list(
        "player__account_uuid", "score_air"
    )
    sorties = PlayerSortie.objects.order_by("account_uuid").values_list("account_uuid", "air_points")
    return [tuple(r) for r in players] + [tuple(r) for r in tours] + [tuple(r) for r in sorties]


def test_off_by_default_scores_are_unchanged() -> None:
    seed()
    assert [air(n) for n in (1, 2, 3, 4)] == [0.0, 10.0, 0.0, 0.0]
    assert not flight_score_pending()


def test_a_change_is_pending_until_applied_then_rescores_every_sortie(tmp_path: Path) -> None:
    seed()
    cfg = make_instance(tmp_path, with_db=False)
    configure(True, 2.0)
    assert flight_score_pending()
    assert air(1) == 0.0  # nothing moves before the apply
    version = current_data_version()

    assert rescore_with_wanted(cfg)

    assert current_data_version() > version
    assert not flight_score_pending()
    assert air(1) == 2.0  # one hour
    assert air(2) == 10.0 + 1.0  # half an hour
    assert air(3) == pytest.approx(2.0 * 0.2)  # a death costs 80% of them
    assert air(4) == 0.0  # gunners get nothing
    assert Player.objects.get(account_uuid=account(1)).score_air == 2.0
    assert not rescore_with_wanted(cfg)  # nothing pending


def test_turning_it_off_again_restores_the_scores(tmp_path: Path) -> None:
    seed()
    cfg = make_instance(tmp_path, with_db=False)
    before = totals()
    configure(True, 2.0)
    assert rescore_with_wanted(cfg)
    assert totals() != before
    configure(False, 2.0)
    assert rescore_with_wanted(cfg)
    assert totals() == before


def test_new_missions_use_the_applied_settings_and_incremental_equals_rebuild(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    configure(True, 3.0)
    assert rescore_with_wanted(cfg)
    seed()
    assert air(1) == 3.0
    incremental = totals()
    rebuild_aggregates()
    assert totals() == incremental

    configure(True, 6.0)  # wanted but not applied: a mission saved now still uses the applied rate
    other = sortie(0, 5, combat_role="air_superiority", flight_time_s=3600.0)
    save(mission((other,)), meta("2026-09-20_22-34-13"))
    assert air(5) == 3.0
    assert rescore_with_wanted(cfg)
    assert air(5) == 6.0
    incremental = totals()
    rebuild_aggregates()
    assert totals() == incremental


def test_rebuild_aggregates_applies_a_pending_change() -> None:
    seed()
    configure(True, 2.0)
    rebuild_aggregates()
    assert air(1) == 2.0
    assert not flight_score_pending()


def _post(**data: str) -> QueryDict:
    q = QueryDict(mutable=True)
    for key, value in data.items():
        q[key] = value
    return q


def test_the_form_validates() -> None:
    assert parse_form(_post(enabled="on", per_hour="1.5")) == (FlightScore(True, 1.5), [])
    assert parse_form(_post(per_hour="1.5"))[0] == FlightScore(False, 1.5)
    assert parse_form(_post(enabled="on", per_hour="0"))[1] == []
    for bad in ("-1", "abc", "", "nan", "inf", str(MAX_FLIGHT_POINTS_PER_HOUR + 1)):
        assert parse_form(_post(enabled="on", per_hour=bad))[1], bad


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def test_the_admin_page_saves_the_wanted_settings_and_says_when_they_apply(admin: Client) -> None:
    html = admin.get(URL).content.decode()
    assert 'name="per_hour"' in html
    version = current_data_version()
    assert admin.post(URL, {"enabled": "on", "per_hour": "2.5"}).status_code == 302
    assert wanted_flight_score() == FlightScore(True, 2.5)
    assert current_data_version() > version
    assert flight_score_pending()
    assert "within a minute" in admin.get(URL).content.decode()


def test_the_admin_page_rejects_bad_input_and_saves_nothing(admin: Client) -> None:
    for bad in ("-3", "x", str(MAX_FLIGHT_POINTS_PER_HOUR * 2)):
        response = admin.post(URL, {"enabled": "on", "per_hour": bad})
        assert response.status_code == 200, bad
        assert wanted_flight_score() == FlightScore()
        assert f'value="{bad}"' in response.content.decode()  # the form comes back as typed


def test_the_admin_page_needs_the_permission(client: Client) -> None:
    assert client.get(URL).status_code in (302, 403)
    client.force_login(User.objects.create_user("staff", "s@example.org", "x", is_staff=True))
    assert client.get(URL).status_code == 403
