"""Every page survives a tour in which the player, aircraft or pair being viewed has no rows (maintainer, 2026-10-05:
"you create a new player every time you see that player for the first time in a tour, but make sure your views still
work when switching tours and suddenly the player is missing in that tour"). Tours are about to be a clean slate, so
this is the normal case, not an edge case.

Three tours: August (1), September (2, empty: a tour row nobody flew in) and October (3). Pilot A flies only in tour 1
(an Il-10, a type nobody else flies), pilot B only in tour 3, pilot C in tours 1 and 3. The pair B vs C exists only in
tour 3, the pair A vs C only in tour 1. Every page is requested with each tour, with `all` and without a tour."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client

from il2ks.db.models import GameObject, Mission, Player, PlayerSortie, PlayerTour, Tour, TourAircraftStats
from tests.factories import account, kill, meta, mission, save, sortie
from tests.simple_reads import PROFILE_READS_TOUR, assert_simple_reads

pytestmark = pytest.mark.django_db

AUGUST = datetime(2026, 8, 20, 20, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 1, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 3, 20, tzinfo=UTC)
AIR = "air_superiority"
ATTACK = "attack"
BOARDS = ["air", "ground", "ground-hour", "interception", "tank-busting", "elo-prop", "elo-jet", "play-time"]
LINK = re.compile(r'href="(/[^"#]*)"')
SKIPPED_LINKS = ("/sprite.svg", "/static/", "/media/", "/admin", "/language/", "/live/", "/setup/", "/_")


class World:
    def __init__(self) -> None:
        self.tour1, self.tour2, self.tour3 = Tour.objects.order_by("started_at")
        self.a, self.b, self.c = (Player.objects.get(account_uuid=account(n)) for n in (1, 2, 3))
        self.il10 = GameObject.objects.get(log_name="IL-10")
        self.mig = GameObject.objects.get(log_name="MiG-15bis")
        self.sabre = GameObject.objects.get(log_name="F-86A-5")
        self.mission1, self.mission3 = Mission.objects.order_by("started_at")

    @property
    def tour_params(self) -> list[str]:
        return ["", f"?tour={self.tour1.pk}", f"?tour={self.tour2.pk}", f"?tour={self.tour3.pk}", "?tour=all"]


@pytest.fixture
def world() -> World:
    save(
        mission(
            (
                sortie(
                    0, 1, name="Alpha", aircraft_type="IL-10", combat_role=ATTACK, kills_ground=2, flight_time_s=900
                ),
                sortie(1, 3, name="Charlie", coalition=2, aircraft_type="F-86A-5", combat_role=AIR, is_death=True),
            ),
            (kill(100, 0, 1),),
        ),
        meta("2026-08-20_20-00-00", AUGUST),
    )
    save(
        mission(
            (
                sortie(0, 2, name="Bravo", aircraft_type="MiG-15bis", combat_role=AIR, kills_air_pvp=1, kills_air_ai=0),
                sortie(1, 3, name="Charlie", coalition=2, aircraft_type="F-86A-5", combat_role=AIR, is_death=True),
            ),
            (kill(100, 0, 1),),
        ),
        meta("2026-10-03_20-00-00", OCTOBER),
    )
    Tour.objects.create(title="September 2026", started_at=SEPTEMBER, ended_at=OCTOBER, mode="monthly")
    return World()


def urls(w: World) -> Iterator[str]:
    """Every public page that takes a tour; the caller appends each tour parameter."""
    for p in (w.a, w.b, w.c):
        for sub in ("", "sorties/", "killboard/", "streaks/", "streaks/history/", "achievements/"):
            yield f"/players/{p.pk}/{sub}"
    yield "/leaderboards/"
    for board in BOARDS:
        yield f"/leaderboards/{board}/"
    yield "/streaks/"
    yield "/aircraft/"
    for aircraft in (w.il10, w.mig, w.sabre):
        yield f"/aircraft/{aircraft.pk}/"
    yield "/achievements/"
    yield "/missions/"
    yield "/"


def internal_links(html: str) -> set[str]:
    return {unescape(m) for m in LINK.findall(html) if not m.startswith(SKIPPED_LINKS)}


def test_every_page_answers_200_in_every_tour_with_the_selector(client: Client, world: World) -> None:
    failures: list[str] = []
    for path in urls(world):
        for params in world.tour_params:
            response = client.get(path + params)
            if response.status_code != 200:
                failures.append(f"{path}{params}: {response.status_code}")
            elif 'name="tour"' not in response.content.decode() and not path.startswith("/leaderboards/elo"):
                failures.append(f"{path}{params}: no tour selector")
    assert not failures, "\n".join(failures)


def test_no_link_on_any_page_is_broken(client: Client, world: World) -> None:
    """Links carry `?tour=` from page to page: the target must answer 200 whatever the tour."""
    failures: list[str] = []
    seen: set[str] = set()
    for path in urls(world):
        for params in world.tour_params:
            html = client.get(path + params).content.decode()
            for link in internal_links(html):
                if link in seen:
                    continue
                seen.add(link)
                status = client.get(link).status_code
                if status != 200 and not (status == 301 and link.startswith("/leaderboards/kills")):
                    failures.append(f"{path}{params} -> {link}: {status}")
    assert not failures, "\n".join(failures)


def test_sortie_and_mission_pages_with_a_foreign_tour(client: Client, world: World) -> None:
    failures: list[str] = []
    for tour in (world.tour1, world.tour2, world.tour3):
        for sortie_row in PlayerSortie.objects.all():
            response = client.get(f"/sorties/{sortie_row.pk}/?tour={tour.pk}")
            if response.status_code != 200:
                failures.append(f"sortie {sortie_row.pk} tour {tour.pk}: {response.status_code}")
        for m in (world.mission1, world.mission3):
            response = client.get(f"/missions/{m.pk}/?tour={tour.pk}")
            if response.status_code != 200:
                failures.append(f"mission {m.pk} tour {tour.pk}: {response.status_code}")
    assert not failures, "\n".join(failures)


# --- the empty state ---
PILOT_PAGES = ("", "sorties/", "killboard/", "streaks/", "streaks/history/", "achievements/")
# The budgets of the populated pages (doc 16, simple_reads). The empty state reads one more table, the tours the pilot
# (or aircraft type) has rows in, to offer them; pages that had room left (sorties list, killboard, history, profile:
# it reuses its tour-history read) stay within the old budget, the best-streaks page (5 -> 6) and the aircraft page
# (11 -> 12) need that one read.
PILOT_BUDGETS = {"": PROFILE_READS_TOUR, "sorties/": 8, "killboard/": 8, "streaks/": 5 + 1, "streaks/history/": 6}


def hrefs(html: str) -> set[str]:
    return {unescape(m) for m in LINK.findall(html)}


@pytest.mark.parametrize("sub", PILOT_PAGES)
def test_a_pilot_absent_from_a_tour_is_told_so_and_offered_the_tours_he_flew_in(
    client: Client,
    world: World,
    sub: str,
) -> None:
    """Alpha flew in August only: in the empty September tour every page of his says so and offers All time and
    August (not October, where he did not fly either), on the same sub-page."""
    base = f"/players/{world.a.pk}/{sub}"

    html = client.get(f"{base}?tour={world.tour2.pk}").content.decode()

    assert "Alpha did not fly in September 2026" in html
    links = hrefs(html)
    assert f"{base}?tour=all" in links
    assert f"{base}?tour={world.tour1.pk}" in links
    assert f"{base}?tour={world.tour3.pk}" not in links


@pytest.mark.parametrize("sub", PILOT_PAGES)
def test_a_pilot_who_flew_in_the_tour_gets_no_absence_notice(client: Client, world: World, sub: str) -> None:
    html = client.get(f"/players/{world.a.pk}/{sub}?tour={world.tour1.pk}").content.decode()

    assert "did not fly in" not in html


def test_the_default_tour_is_the_current_one_and_says_so_for_a_pilot_who_left(client: Client, world: World) -> None:
    html = client.get(f"/players/{world.a.pk}/").content.decode()  # no tour: October, where Alpha did not fly

    assert "Alpha did not fly in October 2026" in html
    assert f"/players/{world.a.pk}/?tour={world.tour1.pk}" in hrefs(html)


@pytest.mark.parametrize("sub", ["", "sorties/", "killboard/", "streaks/", "streaks/history/"])
def test_the_absence_notice_stays_within_the_query_budget(client: Client, world: World, sub: str) -> None:
    assert_simple_reads(client, f"/players/{world.a.pk}/{sub}?tour={world.tour2.pk}", PILOT_BUDGETS[sub])


def test_aircraft_nobody_flew_in_the_tour_is_told_so_and_offers_the_tours_it_was_flown_in(
    client: Client,
    world: World,
) -> None:
    """The Il-10 flew in August only."""
    base = f"/aircraft/{world.il10.pk}/"

    html = client.get(f"{base}?tour={world.tour3.pk}").content.decode()

    assert "was not flown in October 2026" in html
    links = hrefs(html)
    assert f"{base}?tour=all" in links
    assert f"{base}?tour={world.tour1.pk}" in links
    assert "was not flown in" not in client.get(f"{base}?tour={world.tour1.pk}").content.decode()
    assert_simple_reads(client, f"{base}?tour={world.tour3.pk}", 11 + 1)


# --- links keep the scope: no link lands on a page where its target has nothing ------------------------------
PLAYER_LINK = re.compile(r"^/players/(\d+)/(?:[a-z/]*)$")
AIRCRAFT_LINK = re.compile(r"^/aircraft/(\d+)/$")


def effective_tour(query: str) -> int | None:
    """The tour a link opens: its `tour` parameter (`all` = None), else the current (newest) tour."""
    raw = parse_qs(query).get("tour", [""])[0]
    if raw == "all":
        return None
    return int(raw) if raw.isdecimal() else Tour.objects.order_by("-started_at")[0].pk


def has_rows(target: str, tour: int | None) -> bool:
    if tour is None:
        return True
    if match := PLAYER_LINK.match(target):
        return PlayerTour.objects.filter(player_id=int(match[1]), tour_id=tour).exists()
    if match := AIRCRAFT_LINK.match(target):
        return TourAircraftStats.objects.filter(aircraft_id=int(match[1]), tour_id=tour).exists()
    return True


def pages_with_links(w: World) -> Iterator[str]:
    yield from urls(w)
    for row in PlayerSortie.objects.all():
        yield f"/sorties/{row.pk}/"
    for m in (w.mission1, w.mission3):
        yield f"/missions/{m.pk}/"
    for key in ("first_blood", "career_kills", "regular"):
        yield f"/achievements/{key}/"


def test_no_link_leads_to_a_page_where_its_target_has_no_rows_in_the_tour(client: Client, world: World) -> None:
    """A link to a pilot or an aircraft carries the tour of the page it sits on (`?tour=<id>`, or `?tour=all` from an
    all-time context): a bare link would silently mean the current tour, where the target may not have flown. A link
    that stays on the page's own (target, tour), such as a breadcrumb, is allowed: that page explains itself."""
    dead: set[str] = set()
    for path in pages_with_links(world):
        for params in world.tour_params:
            page_url = path + params
            page = urlsplit(page_url)
            page_tour = effective_tour(page.query)
            for link in hrefs(client.get(page_url).content.decode()):
                parts = urlsplit(link)
                if not (PLAYER_LINK.match(parts.path) or AIRCRAFT_LINK.match(parts.path)):
                    continue
                tour = effective_tour(parts.query)
                same_scope = tour == page_tour and parts.path.split("/")[:3] == page.path.split("/")[:3]
                if not same_scope and not has_rows(parts.path, tour):
                    dead.add(f"{page_url} -> {link}")
    assert not dead, "\n".join(sorted(dead))
