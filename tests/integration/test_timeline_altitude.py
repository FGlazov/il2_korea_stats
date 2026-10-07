"""Altitude in the sortie timeline (roadmap 0.2.0, OQ-135, OQ-39).

Every row with a stored position shows the game's `y` (above sea level) in the sortie side's unit: BLUFOR 'Angels N'
(thousands of feet, rounded, exact feet in the tooltip), REDFOR metres with the locale's grouping. Rows without a
position stay blank. Synthetic data only."""

import re
from dataclasses import replace

import pytest
from django.test import Client
from django.utils import translation

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.result import TimelineEntry
from il2ks.db.models import PlayerSortie
from il2ks.web.sortie_view import altitude
from tests.factories import mission, save, sortie

pytestmark = pytest.mark.django_db

NBSP = "\N{NO-BREAK SPACE}"
ENTRIES = (
    TimelineEntry(1000, "spawn", "parking", Pos(10, 4500.4, 30)),
    TimelineEntry(1500, "takeoff", pos=Pos(10, 4572.0, 30)),
    TimelineEntry(3000, "kill", "MiG-15bis", None),
)


def page(client: Client, coalition: int, language: str = "en") -> str:
    save(mission((replace(sortie(0, 1, coalition=coalition), timeline=ENTRIES),)))
    pk = PlayerSortie.objects.get().pk
    response = client.get(f"/sorties/{pk}/", headers={"accept-language": language})
    assert response.status_code == 200
    html = response.content.decode()
    return html[html.index('<table class="data-table data-table--compact timeline"') :]


def altitude_cells(html: str) -> list[str]:
    cells = re.findall(r'<td class="num nowrap timeline__alt">(.*?)</td>', html, flags=re.S)
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in cells]


def test_a_redfor_sortie_shows_metres_with_grouping_and_blanks_a_row_without_position(client: Client) -> None:
    html = page(client, coalition=1)

    assert altitude_cells(html) == [f"4,500{NBSP}m", f"4,572{NBSP}m", ""]
    assert "Angels 1" not in html  # the column hint mentions Angels, no row shows them


def test_a_blufor_sortie_shows_angels_with_the_exact_feet_in_the_tooltip(client: Client) -> None:
    html = page(client, coalition=2)

    # 4500.4 m = 14,765 ft -> Angels 15; 4572 m = 15,000 ft -> Angels 15
    assert altitude_cells(html) == ["Angels 15", "Angels 15", ""]
    assert f'title="14,765{NBSP}ft"' in html
    assert f'title="15,000{NBSP}ft"' in html


def test_the_altitude_column_header_says_above_sea_level(client: Client) -> None:
    html = page(client, coalition=1)

    header = html[: html.index("</thead>")]
    assert "Altitude" in header
    assert "above sea level" in header


def test_metres_group_by_the_viewers_language(client: Client) -> None:
    german = altitude_cells(page(client, coalition=1, language="de"))

    assert german[0] == f"4.500{NBSP}m"


@pytest.mark.parametrize(
    ("metres", "side", "text", "title"),
    [
        (4500.0, "redfor", f"4,500{NBSP}m", ""),
        (-12.0, "redfor", f"-12{NBSP}m", ""),
        (4500.0, None, f"4,500{NBSP}m", ""),  # a side that is neither: the game's own metres
        (0.0, "blufor", "Angels 0", f"0{NBSP}ft"),
        (3048.0, "blufor", "Angels 10", f"10,000{NBSP}ft"),
        (9144.0, "blufor", "Angels 30", f"30,000{NBSP}ft"),
    ],
)
def test_altitude_units_follow_the_side(metres: float, side: str | None, text: str, title: str) -> None:
    with translation.override("en"):
        assert altitude([1, metres, 2], side) == (text, title)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("bad", [None, [], [1, 2], "x"])
def test_no_position_means_no_altitude(bad: object) -> None:
    assert altitude(bad, "blufor") == ("", "")
