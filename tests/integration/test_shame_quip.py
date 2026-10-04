"""The profile's hall of shame (doc 13): the two tiles, the quip by incident kind, the p90 variant, tours.

September: 25 pilots, one sortie each. Pilots 1-4 have a taxi accident, 3, 5, 6 and 8 killed a friendly. October: pilots
4 and 8 fly again, pilot 4 killing a friendly. All-time, pilots 1-3 have a taxi rate of 1 against a p90 of 0.8, pilot 4
one of 0.5; in September all four taxi pilots are at 1 = the p90, so none is above it."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils.html import escape

from il2ks.core.stat_marks import MarkRules
from il2ks.db.models import Player, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.web import flavor
from tests.factories import STARTED_AT, account, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

ONE = MarkRules(min_sorties=1)


def seed() -> None:
    september = tuple(
        sortie(
            i,
            i + 1,
            taxi_accident=i + 1 in {1, 2, 3, 4},
            friendly_kills=1 if i + 1 in {3, 5, 6, 8} else 0,
        )
        for i in range(25)
    )
    save(mission(september), meta("2026-09-19_22-34-13", STARTED_AT), marks=ONE)
    october = (sortie(0, 4, friendly_kills=1), sortie(1, 8))
    save(mission(october), meta("2026-10-02_10-00-00", STARTED_AT + timedelta(days=13)), marks=ONE)


def pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def page(client: Client, n: int, query: str = "?tour=all") -> str:
    return client.get(f"/players/{pk(n)}/{query}").content.decode()


def quip(spot: str, n: int) -> str:
    return escape(str(flavor.pick(spot, pk(n))))


@pytest.mark.parametrize(
    ("pilot", "spot"),
    [
        (1, "shame_taxi_p90"),
        (3, "shame_both_p90"),
        (5, "shame_friendly_p90"),
        (4, "shame_both"),  # one taxi accident and one friendly kill in two sorties: 50% each, under the p90
        (8, "shame_friendly"),
        (9, "shame_clean"),
    ],
)
def test_the_quip_follows_the_incidents_all_time(client: Client, pilot: int, spot: str) -> None:
    seed()
    body = page(client, pilot)
    assert quip(spot, pilot) in body
    assert '<p class="shame__quip">' in body


def test_tiles_show_taxi_accidents_and_friendly_fire_not_strafings(client: Client) -> None:
    seed()
    body = page(client, 3)
    start = body.index('class="shame"')
    shame = body[start : body.index("</section>", start)]
    assert "Taxi accidents" in shame
    assert "Friendly-fire incidents" in shame
    assert "Strafed on the ground" not in shame
    assert "Strafed on the ground" in body  # now under "Other totals"


def test_friendly_fire_incidents_count_sorties_not_kills() -> None:
    sorties = (sortie(0, 1, friendly_kills=3), sortie(1, 1, friendly_kills=1), sortie(2, 1))
    save(mission(sorties), meta("2026-09-19_22-34-13", STARTED_AT))
    p = Player.objects.get()
    assert (p.friendly_kills, p.friendly_fire_incidents) == (4, 2)


def test_the_quip_follows_the_selected_tour(client: Client) -> None:
    seed()
    september = f"?tour={Tour.objects.get(title='September 2026').pk}"
    # September: four taxi pilots at rate 1 = the p90, so nobody is above it and pilot 1 gets the plain taxi line
    assert quip("shame_taxi", 1) in page(client, 1, september)
    assert quip("shame_friendly", 5) in page(client, 5, september)
    october = f"?tour={Tour.objects.get(title='October 2026').pk}"
    assert quip("shame_friendly", 4) in page(client, 4, october)  # the taxi accident was in September
    assert quip("shame_clean", 8) in page(client, 8, october)


def test_rebuild_keeps_the_counter() -> None:
    seed()
    before = list(Player.objects.order_by("pk").values_list("friendly_fire_incidents", flat=True))
    rebuild_aggregates(marks=ONE)
    assert list(Player.objects.order_by("pk").values_list("friendly_fire_incidents", flat=True)) == before
    assert sum(before) == 5
