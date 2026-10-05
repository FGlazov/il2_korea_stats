"""What the pages show of a provisional (still running) mission (FR-ING-15): a notice on the mission and the sortie
page, a "Live" badge in the lists, and no extra queries (TD-22). Synthetic data only."""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from il2ks.db.models import Mission, PlayerSortie
from tests.factories import mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

NOTICE = "The numbers may still change until it ends."


def seed(*, live: bool) -> tuple[int, int]:
    result = save(mission((sortie(0, 1, kills_air=2), sortie(1, 2, name="abe"))))
    if live:
        Mission.objects.filter(pk=result.pk).update(is_live=True, completed_cleanly=False)
    return result.pk, PlayerSortie.objects.order_by("pk").first().pk  # pyright: ignore[reportOptionalMemberAccess]


def count_queries(client: Client, url: str) -> int:
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url).status_code == 200
    return len(ctx.captured_queries)


def test_the_mission_and_sortie_pages_of_a_running_mission_show_the_notice(client: Client) -> None:
    mission_pk, sortie_pk = seed(live=True)

    mission_html = client.get(reverse("web:mission-detail", args=[mission_pk])).content.decode()
    sortie_html = client.get(reverse("web:sortie-detail", args=[sortie_pk])).content.decode()

    assert NOTICE in mission_html
    assert NOTICE in sortie_html
    assert sortie_html.index("Mission still running") < sortie_html.index(
        'class="stat-tiles"'
    )  # warned before the numbers
    header_html = sortie_html[
        sortie_html.index('class="sortie-head__badges"') : sortie_html.index("sortie-head__where")
    ]
    assert ">Live<" in header_html
    assert "Log incomplete" not in mission_html  # a running mission is not "incomplete"
    assert "badge--green" in mission_html


def test_a_finished_mission_has_no_notice_and_no_badge(client: Client) -> None:
    mission_pk, sortie_pk = seed(live=False)

    assert NOTICE not in client.get(reverse("web:mission-detail", args=[mission_pk])).content.decode()
    finished_sortie_html = client.get(reverse("web:sortie-detail", args=[sortie_pk])).content.decode()
    assert NOTICE not in finished_sortie_html
    assert ">Live<" not in finished_sortie_html
    assert ">Live<" not in client.get(reverse("web:mission-list")).content.decode()


def test_the_lists_mark_a_running_mission_live(client: Client) -> None:
    seed(live=True)
    assert ">Live<" in client.get(reverse("web:mission-list")).content.decode()
    assert ">Live<" in client.get(reverse("web:home")).content.decode()


def test_a_running_mission_costs_no_extra_queries(client: Client) -> None:
    mission_pk, sortie_pk = seed(live=False)
    urls = [
        reverse("web:mission-detail", args=[mission_pk]),
        reverse("web:sortie-detail", args=[sortie_pk]),
        reverse("web:mission-list"),
    ]
    normal = [count_queries(client, url) for url in urls]
    Mission.objects.filter(pk=mission_pk).update(is_live=True)
    assert [count_queries(client, url) for url in urls] == normal
    for url, budget in zip(urls, normal, strict=True):
        assert_simple_reads(client, url, max_queries=budget)
