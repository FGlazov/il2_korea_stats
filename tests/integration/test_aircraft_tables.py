"""The breakdown tables of the aircraft page (maintainer's view pass, 2026-10-05): loadouts and modification sets are
listed from `MIN_EVENTS_LISTED` sorties on, page by 20 with their own parameters, and the loadouts table has an air
superiority / attack tab with the columns of each mode (the ammunition mixes: `test_ammo_mixes.py`)."""

import pytest
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from il2ks.core.replay.result import CombatRole, SortieResult
from il2ks.db.models import GameObject
from il2ks.queries.paging import MIN_EVENTS_LISTED
from tests.factories import meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

AIR: CombatRole = "air_superiority"
ATTACK: CombatRole = "attack"


def seed(*groups: tuple[int, int, CombatRole, int]) -> None:
    """MiG-15bis sorties in one mission: (count, payload id, combat role, weapon mods) per group, one pilot each."""
    sorties: list[SortieResult] = []
    player = 0
    for count, payload, role, mods in groups:
        for _ in range(count):
            player += 1
            sorties.append(sortie(player - 1, player, payload_id=payload, combat_role=role, weapon_mods=mods))
    save(mission(tuple(sorties)), meta("2026-09-19_22-34-13"))


def page(client: Client, query: str = "") -> HttpResponse:
    pk = GameObject.objects.get(log_name="MiG-15bis").pk
    response = client.get(reverse("web:aircraft-detail", args=[pk]) + query)
    assert response.status_code == 200
    return response


def loadout_names(response: HttpResponse) -> list[str]:
    return [row.payload.payload_name for row in response.context["loadouts"]]


def section(response: HttpResponse, heading: str, next_heading: str) -> str:
    """The HTML between two `<h2>` headings (a section of the page)."""
    body = response.content.decode()
    start = body.index(f"<h2>{heading}</h2>")
    return body[start : body.index(f"<h2>{next_heading}</h2>", start)]


def test_the_listing_minimum_is_ten_events() -> None:
    assert MIN_EVENTS_LISTED == 10


def test_loadouts_and_modification_sets_need_ten_sorties_to_be_listed(client: Client) -> None:
    seed((10, 1, AIR, 0), (9, 2, AIR, 4))

    response = page(client, "?tour=all")

    assert loadout_names(response) == ["Payload 1"]  # Payload 2 was flown nine times
    assert [row.stats.sorties for row in response.context["mod_sets"]] == [10]  # so were the modifications of those
    assert response.context["tile"].sorties == 19  # the totals count every sortie


def test_a_type_without_a_listed_loadout_says_so(client: Client) -> None:
    seed((9, 1, AIR, 0))

    body = page(client, "?tour=all").content.decode()

    assert "No loadout with at least 10 sorties yet." in body
    assert "No modification set with at least 10 sorties yet." in body


@pytest.mark.usefixtures("list_every_row")
def test_loadouts_and_modification_sets_page_by_twenty_with_their_own_parameters(client: Client) -> None:
    groups: list[tuple[int, int, CombatRole, int]] = [(1, payload, AIR, payload) for payload in range(1, 26)]
    seed(*groups)

    first = page(client, "?tour=all")
    second = page(client, "?tour=all&page_loadouts=2&page_mods=2")

    assert (len(first.context["loadouts"]), len(first.context["mod_sets"])) == (20, 20)
    assert (len(second.context["loadouts"]), len(second.context["mod_sets"])) == (5, 5)
    assert (second.context["loadouts"].number, second.context["mod_sets"].number) == (2, 2)
    body = second.content.decode()
    assert body.count("Showing 21\N{EN DASH}25 of 25") == 2
    assert 'aria-label="Pagination: Loadouts"' in body
    assert 'aria-label="Pagination: Modifications"' in body
    # each table pages apart: page 2 of the loadouts keeps the (absent) other tables on page 1
    only_loadouts = page(client, "?tour=all&page_loadouts=2")
    assert (only_loadouts.context["loadouts"].number, only_loadouts.context["mod_sets"].number) == (2, 1)


@pytest.mark.usefixtures("list_every_row")
def test_a_new_sort_starts_the_loadouts_at_page_one(client: Client) -> None:
    groups: list[tuple[int, int, CombatRole, int]] = [(1, payload, AIR, 0) for payload in range(1, 26)]
    seed(*groups)

    body = page(client, "?tour=all&page_loadouts=2").content.decode()

    sort_links = [line for line in body.splitlines() if "lsort=" in line and "<th" in line]
    assert sort_links
    assert all("page_loadouts" not in line for line in sort_links)


@pytest.mark.usefixtures("list_every_row")
def test_the_loadouts_tab_picks_the_role_under_all_roles(client: Client) -> None:
    seed((3, 1, AIR, 0), (2, 2, ATTACK, 0))

    default = page(client, "?tour=all")  # most sorties are air superiority
    attack = page(client, "?tour=all&lrole=attack")
    air = page(client, "?tour=all&lrole=air_superiority")

    assert [(t.label, t.current) for t in default.context["loadout_tabs"]] == [
        ("Air superiority", True),
        ("Attack", False),
    ]
    assert (default.context["loadout_role"], loadout_names(default)) == (AIR, ["Payload 1"])
    assert (attack.context["loadout_role"], loadout_names(attack)) == (ATTACK, ["Payload 2"])
    assert loadout_names(air) == ["Payload 1"]
    assert "lrole=attack" in default.content.decode()
    assert any("tour=all" in t.href and "lrole=attack" in t.href for t in default.context["loadout_tabs"])
    assert [t.current for t in attack.context["loadout_tabs"]] == [False, True]


@pytest.mark.usefixtures("list_every_row")
def test_an_attack_type_opens_its_loadouts_on_the_attack_tab(client: Client) -> None:
    seed((1, 1, AIR, 0), (3, 2, ATTACK, 0))

    response = page(client, "?tour=all")

    assert response.context["loadout_role"] == ATTACK
    assert loadout_names(response) == ["Payload 2"]


@pytest.mark.usefixtures("list_every_row")
def test_a_single_role_page_has_no_tab_and_follows_the_role(client: Client) -> None:
    seed((3, 1, AIR, 0), (2, 2, ATTACK, 0))

    air = page(client, "?tour=all&role=air_superiority")
    attack = page(client, "?tour=all&role=attack&lrole=air_superiority")  # the page's own role wins over the tab

    assert air.context["loadout_tabs"] == ()
    assert attack.context["loadout_tabs"] == ()
    assert (air.context["loadout_role"], loadout_names(air)) == (AIR, ["Payload 1"])
    assert (attack.context["loadout_role"], loadout_names(attack)) == (ATTACK, ["Payload 2"])
    assert "Which loadouts to show" not in air.content.decode()


@pytest.mark.usefixtures("list_every_row")
def test_each_mode_shows_only_its_columns(client: Client) -> None:
    seed((3, 1, AIR, 0), (2, 2, ATTACK, 0))
    air_only = ("Air kills", "Average pilot Elo", "Air kills per sortie", "PvP K/D")
    attack_only = ("Ground kills", "Attack proficiency")

    air = section(page(client, "?tour=all&lrole=air_superiority"), "Loadouts", "Modifications")
    attack = section(page(client, "?tour=all&lrole=attack"), "Loadouts", "Modifications")

    for header in air_only:
        assert f">{header}</a>" in air
        assert f">{header}</a>" not in attack
    for header in attack_only:
        assert f">{header}</a>" in attack
        assert f">{header}</a>" not in air
    for shared in ("Loadout", "Sorties", "Deaths"):
        assert f">{shared}</a>" in air
        assert f">{shared}</a>" in attack


@pytest.mark.usefixtures("list_every_row")
def test_the_loadouts_help_is_one_short_line_per_mode(client: Client) -> None:
    seed((3, 1, AIR, 0), (2, 2, ATTACK, 0))

    air = section(page(client, "?tour=all&lrole=air_superiority"), "Loadouts", "Modifications")
    attack = section(page(client, "?tour=all&lrole=attack"), "Loadouts", "Modifications")

    assert air.count('<p class="muted">') == 1
    assert attack.count('<p class="muted">') == 1
    assert "Average Elo shows the pilots" in air
    assert "Attack proficiency needs at least" in attack


def test_the_removed_help_texts_are_gone(client: Client) -> None:
    seed((12, 1, AIR, 0))

    body = page(client, "?tour=all").content.decode()

    for text in (
        "Best exchange",
        "K/L is kills per loss against that aircraft",
        "How many gun hits it takes to shoot this aircraft down",
        "The same kills grouped by which ammunition types hit together",
        "Which loadouts work best, in the selected tour",
    ):
        assert text not in body


@pytest.mark.usefixtures("list_every_row")
def test_the_detail_page_stays_within_its_read_budget_with_all_tables(client: Client) -> None:
    seed((3, 1, AIR, 0), (2, 2, ATTACK, 4))
    pk = GameObject.objects.get(log_name="MiG-15bis").pk

    # the tables page lists in Python: no count queries (the same budget as before the tabs and the paging)
    assert_simple_reads(client, f"/aircraft/{pk}/?tour=all&lrole=attack&page_loadouts=1&page_mods=1", max_queries=10)
