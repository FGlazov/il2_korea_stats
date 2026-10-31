"""What the pages show of a provisional (still running) mission (FR-ING-15): a notice on the mission and the sortie
page, a "Live" badge in the lists, and no extra queries (TD-22). Synthetic data only."""

import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from il2ks.db.models import Mission, PlayerSortie
from il2ks.db.site import bump_data_version
from tests.factories import mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

NOTICE = "The numbers may still change until it ends."
DELAY_NOTICE = "Sorties appear here with a delay"


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


def test_a_delayed_mission_says_so_without_saying_how_long(client: Client) -> None:
    """FR-ING-25: the pages say there is a delay, never its length; a running mission without one stays silent."""
    mission_pk, sortie_pk = seed(live=True)
    urls = [reverse("web:mission-detail", args=[mission_pk]), reverse("web:sortie-detail", args=[sortie_pk])]
    assert all(DELAY_NOTICE not in client.get(url).content.decode() for url in urls)

    Mission.objects.filter(pk=mission_pk).update(live_delayed=True)
    bump_data_version()
    for url in urls:
        html = client.get(url).content.decode()
        line = next(s for s in html.splitlines() if DELAY_NOTICE in s)
        assert not re.search(r"\d|minute", line)


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


def last_mission_block(client: Client) -> str:
    html = client.get(reverse("web:home")).content.decode()
    return html[html.index('class="last-mission"') : html.index("last-mission__pilots")]


def test_the_home_page_calls_a_running_mission_current_and_marks_it_live(client: Client) -> None:
    """A tester read "Last mission" over the running one as the previous mission shown twice."""
    seed(live=True)
    block = last_mission_block(client)
    assert "Current mission" in block
    assert "Last mission" not in block
    assert ">Live<" in block
    assert "Winner" not in block  # no winner before it ends


def test_the_home_page_shows_a_finished_mission_as_the_last_one(client: Client) -> None:
    seed(live=False)
    block = last_mission_block(client)
    assert "Last mission" in block
    assert ">Live<" not in block
    assert "Winner" in block


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


ENDED = "<dt>Sortie ended</dt>"


def test_a_sortie_still_going_shows_no_end(client: Client) -> None:
    """stats.yalu.info/sorties/267/: the snapshot's time was shown as the end of a sortie still in the air."""
    _, sortie_pk = seed(live=True)
    PlayerSortie.objects.filter(pk=sortie_pk).update(timeline=[])
    assert ENDED not in client.get(reverse("web:sortie-detail", args=[sortie_pk])).content.decode()


def test_a_sortie_that_ended_in_a_running_mission_shows_its_end(client: Client) -> None:
    _, sortie_pk = seed(live=True)
    PlayerSortie.objects.filter(pk=sortie_pk).update(
        timeline=[{"tick": 5000, "kind": "sortie_end", "detail": "landed"}]
    )
    assert ENDED in client.get(reverse("web:sortie-detail", args=[sortie_pk])).content.decode()
