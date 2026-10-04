"""The player sortie list and the sortie detail page (FR-WEB-5, FR-WEB-6, FR-WEB-13, FR-ADM-3, TD-22).

Synthetic data only (tests/factories.py): never real player names."""

import re
from dataclasses import replace

import pytest
from django.test import Client

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.result import (
    AmmoCounts,
    AmmoHits,
    Counterpart,
    DamageExchange,
    MissionResult,
    SortieResult,
    TimelineEntry,
)
from il2ks.db.models import HEAVY_SORTIE_COLUMNS, GameObject, Mission, Player, PlayerSortie
from il2ks.queries.missions import mission_sorties
from il2ks.queries.paging import ROW_PAGE_SIZE
from il2ks.queries.players import recent_sorties
from il2ks.queries.sorties import SortieFilters, sortie_page
from tests.factories import kill, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

DETAIL_BUDGET = (
    2 + 7
)  # site context + sortie, kills made, kills suffered, counterparts, game objects, medals, their rarity
LIST_BUDGET = 2 + 5  # site context + player, aircraft choices, tours (selector), count, page


def pk_of(player: int, spawn_tick: int | None = None) -> int:
    rows = PlayerSortie.objects.filter(player__account_uuid__endswith=f"{player:012d}")
    return (rows.get(spawn_tick=spawn_tick) if spawn_tick is not None else rows.get()).pk


def detail(client: Client, pk: int) -> str:
    response = client.get(f"/sorties/{pk}/")
    assert response.status_code == 200
    return response.content.decode()


def duel() -> MissionResult:
    """Player 1 (red) kills player 2 (blue) with an assist from player 3; player 1 also killed an AI MiG-15bis."""
    killer = replace(
        sortie(0, 1, name="Alpha", kills_air=2, kills_ground=3, ground_by_category={"tank": 2, "other": 1}),
        timeline=(
            TimelineEntry(1000, "spawn", "parking", Pos(1, 2, 3)),
            TimelineEntry(1500, "takeoff", pos=Pos(1, 2, 3)),
            TimelineEntry(3000, "kill", "MiG-15bis", Pos(1, 2, 3), Counterpart("MiG-15bis")),
            TimelineEntry(4000, "kill", "F-86A-5", Pos(1, 2, 3), Counterpart("F-86A-5", 1, 2)),
            TimelineEntry(5000, "sortie_end", "landed", Pos(1, 2, 3)),
        ),
        damage=(
            DamageExchange(Counterpart("F-86A-5", 1, 2), damage_dealt=0.8, hits_dealt=9),
            DamageExchange(Counterpart("M46 Patton"), damage_dealt=2.4, hits_dealt=4),
        ),
        ammo_hits=(AmmoHits("BULLET_12-7_USA_API", hits_given=9),),
    )
    victim = sortie(1, 2, name="Bravo", coalition=2, aircraft_type="F-86A-5", outcome="shot_down", is_plane_lost=True)
    helper = sortie(2, 3, name="Charlie", assists=1)
    return mission(
        (killer, victim, helper),
        (
            kill(4000, 0, 1, victim_type="F-86A-5"),
            kill(4000, 2, 1, victim_type="F-86A-5", killer_type="MiG-15bis", credit="assist"),
        ),
    )


# --- detail: content, hiding, budget -------------------------------------------------------------------------------
def test_detail_shows_kills_victims_and_the_ground_breakdown_within_budget(client: Client) -> None:
    save(duel())
    url = f"/sorties/{pk_of(1)}/"

    assert_simple_reads(client, url, max_queries=DETAIL_BUDGET)

    html = client.get(url).content.decode()
    assert "Alpha" in html
    assert "MiG-15bis" in html
    assert "Bravo" in html  # the player victim, linked
    assert f'href="/sorties/{pk_of(2)}/"' in html  # to the victim's own sortie
    assert "Breakdown by target type" in html
    assert "Tanks" in html
    assert "Ground kills: 3" in html


def test_detail_of_the_victim_lists_who_shot_it_down(client: Client) -> None:
    save(duel())

    html = detail(client, pk_of(2))

    assert "Shot down by" in html
    assert "Alpha" in html
    assert "Charlie" in html
    assert "Assist" in html


def test_hidden_player_and_hidden_mission_are_404(client: Client) -> None:
    save(duel())
    pk = pk_of(1)

    Player.objects.filter(account_uuid__endswith="000000000001").update(is_hidden=True)
    assert client.get(f"/sorties/{pk}/").status_code == 404
    assert client.get(f"/sorties/{pk_of(2)}/").status_code == 200

    Player.objects.update(is_hidden=False)
    Mission.objects.update(is_hidden=True)
    assert client.get(f"/sorties/{pk_of(2)}/").status_code == 404
    assert client.get("/sorties/999999/").status_code == 404


def test_a_hidden_counterpart_is_anonymous_and_has_no_link(client: Client) -> None:
    save(duel())
    Player.objects.filter(account_uuid__endswith="000000000002").update(is_hidden=True)

    html = detail(client, pk_of(1))

    assert "Hidden player" in html
    assert "Bravo" not in html
    assert f"/sorties/{pk_of(2)}/" not in html
    assert f"/players/{Player.objects.get(account_uuid__endswith='000000000002').pk}/" not in html


def test_damage_lists_players_first_and_sums_ai_targets_in_health_units(client: Client) -> None:
    save(duel())

    html = detail(client, pk_of(1))

    assert html.index("Bravo", html.index("Damage dealt")) < html.index("M46 Patton", html.index("Damage dealt"))
    assert "80%" in html  # player: percent
    assert "2.4" in html  # ground object type: health units


def test_friendly_fire_is_marked_and_not_counted(client: Client) -> None:
    result = mission(
        (
            sortie(0, 1, name="Alpha", friendly_kills=1),
            sortie(1, 2, name="Bravo", aircraft_type="F-86A-5", outcome="shot_down", is_plane_lost=True),
        ),
        (kill(3000, 0, 1, victim_type="F-86A-5", is_friendly=True),),
    )
    save(result)

    assert "Friendly fire" in detail(client, pk_of(1))
    assert "Friendly fire" in detail(client, pk_of(2))  # as the credit against the victim, marked


# --- detail: flags, payload, ammo, gunners ------------------------------------------------------------------------
def test_unknown_payload_shows_its_raw_id(client: Client) -> None:
    save(mission((sortie(0, 1, payload_id=-1),)))

    assert "Unknown payload (id -1)" in detail(client, pk_of(1))


def test_known_payload_name_and_ammo_used_are_shown(client: Client) -> None:
    save(mission((sortie(0, 1, payload_id=3, ammo_loaded=AmmoCounts(bullets=400), ammo_left=AmmoCounts(bullets=150)),)))

    html = detail(client, pk_of(1))

    assert "Payload 3" in html
    assert re.search(r"<td class=\"num\">400</td>\s*<td class=\"num\">150</td>\s*<td class=\"num\">250</td>", html)
    assert "Used is unknown" not in html


def test_resupplied_sortie_says_ammo_used_is_unknown(client: Client) -> None:
    save(mission((sortie(0, 1, resupplied=True),)))

    html = detail(client, pk_of(1))

    assert "Resupplied during the sortie" in html
    assert "Used is unknown" in html
    assert re.search(r"<td class=\"num\">200</td>\s*<td class=\"num\"><span[^>]*>—</span>", html)


def test_ammo_left_after_loss_and_missing_end_record_have_their_own_reasons(client: Client) -> None:
    save(
        mission(
            (
                sortie(0, 1, ammo_left_after_loss=True, is_plane_lost=True, outcome="shot_down"),
                sortie(1, 2, ammo_left=None),
            )
        )
    )

    after_loss = detail(client, pk_of(1))
    no_record = detail(client, pk_of(2))

    assert "Ammunition left not recorded reliably" in after_loss
    assert "after the aircraft was lost" in after_loss
    assert "no end-of-sortie ammunition record" in no_record


def test_after_a_loss_unreleased_bombs_show_zero_used_and_the_left_column_is_dashed(client: Client) -> None:
    save(
        mission(
            (
                sortie(
                    0,
                    1,
                    ammo_loaded=AmmoCounts(bullets=400, bombs=4),
                    ammo_left=AmmoCounts(bullets=150),
                    ammo_left_after_loss=True,
                    is_plane_lost=True,
                    outcome="shot_down",
                ),
            )
        )
    )

    html = detail(client, pk_of(1))

    assert re.search(r"<td class=\"num\">4</td>\s*<td class=\"num\">—</td>\s*<td class=\"num\">0</td>", html)  # bombs
    assert re.search(
        r"<td class=\"num\">400</td>\s*<td class=\"num\">—</td>\s*<td class=\"num\"><span[^>]*>—</span>", html
    )


def test_after_a_loss_released_bombs_show_a_marked_estimate_and_guns_stay_unknown(client: Client) -> None:
    """OQ-101: "~4" with the estimate tooltip for bombs that were released; guns keep the dash; no plain count."""
    save(
        mission(
            (
                sortie(
                    0,
                    1,
                    ammo_loaded=AmmoCounts(bullets=400, bombs=4),
                    ammo_left=AmmoCounts(bullets=150),
                    ammo_left_after_loss=True,
                    store_releases=1,
                    is_plane_lost=True,
                    outcome="shot_down",
                ),
            )
        )
    )

    html = detail(client, pk_of(1))

    assert re.search(
        r"<td class=\"num\">4</td>\s*<td class=\"num\">—</td>\s*<td class=\"num\"><span title=\"Estimate:[^>]*>~4</span>",  # noqa: E501
        html,
    )
    assert re.search(
        r"<td class=\"num\">400</td>\s*<td class=\"num\">—</td>\s*<td class=\"num\"><span[^>]*>—</span>", html
    )
    assert "estimated as everything loaded" in html


def test_gunner_sortie_has_a_notice_and_no_combat_role(client: Client) -> None:
    save(mission((sortie(0, 1, aircraft_type="Turret_IL10", role="gunner", combat_role=None),)))

    html = detail(client, pk_of(1))

    assert "Gunner sortie" in html
    assert "Air superiority" not in html


def test_flag_notices_are_neutral(client: Client) -> None:
    save(mission((sortie(0, 1),)))
    PlayerSortie.objects.update(suspected_early_bailout=True, suspected_structural_failure=True)

    html = detail(client, pk_of(1))

    assert "Possible early bailout" in html
    assert "can be wrong" in html
    assert "Possible structural failure" in html


def test_destroyed_status_is_not_derived_from_damage_taken(client: Client) -> None:
    save(mission((sortie(0, 1, damage_taken=1.0),)))  # unharmed status with full damage taken: not "destroyed"

    html = detail(client, pk_of(1))

    assert "Unharmed" in html
    assert "Destroyed" not in html


# --- detail: timeline ----------------------------------------------------------------------------------------------
def long_timeline(ground_kills: int, singles: int) -> SortieResult:
    entries: list[TimelineEntry] = [TimelineEntry(1000, "spawn", "parking", Pos(1, 2, 3))]
    tick = 1100
    for _ in range(ground_kills):
        entries.append(TimelineEntry(tick, "kill", "GAZ_63", None, Counterpart("GAZ_63")))
        tick += 10
    for index in range(singles):  # alternating events never fold
        entries.append(TimelineEntry(tick, "takeoff" if index % 2 == 0 else "landing", pos=Pos(1, 2, 3)))
        tick += 10
    entries.append(TimelineEntry(tick, "sortie_end", "landed"))
    return replace(
        sortie(0, 1, kills_ground=ground_kills, ground_by_category={"vehicle": ground_kills}), timeline=tuple(entries)
    )


def test_consecutive_ground_kills_fold_into_one_row(client: Client) -> None:
    save(mission((long_timeline(ground_kills=300, singles=0),), extra_types=frozenset({"GAZ_63"})))
    pk = pk_of(1)

    html = detail(client, pk)

    assert "300 ground targets" in html
    assert "300\N{MULTIPLICATION SIGN} GAZ-63" in html
    assert "Show the remaining" not in html
    assert len(html) < 100_000
    assert_simple_reads(client, f"/sorties/{pk}/", max_queries=DETAIL_BUDGET)


def test_a_long_timeline_is_paginated_and_keeps_the_other_parameters(client: Client) -> None:
    """OQ-96: 20 timeline rows a page (`?page_timeline=`); the links keep every other parameter, repeats too."""
    save(mission((long_timeline(ground_kills=0, singles=ROW_PAGE_SIZE * 2 + 5),)))
    pk = pk_of(1)

    first = detail(client, pk)
    second = client.get(f"/sorties/{pk}/?page_timeline=2&cols=a&cols=b").content.decode()

    assert first.count('class="timeline__row') == ROW_PAGE_SIZE
    assert second.count('class="timeline__row') == ROW_PAGE_SIZE
    assert "Show the remaining" not in first
    link = re.search(r'<a href="([^"]*)" hx-get="[^"]*" rel="next"', second)
    assert link is not None
    assert "cols=a&amp;cols=b" in link.group(1) or "cols=a&cols=b" in link.group(1)
    assert "page_timeline=3" in link.group(1)


def test_timeline_rows_carry_icons_and_both_clocks(client: Client) -> None:
    save(duel())

    html = detail(client, pk_of(1))

    assert "+1:00" in html  # one minute after the spawn (tick 4000 is 60 s after tick 1000)
    assert "event/" not in html  # icons are inlined, never referenced by path
    assert html.count("<svg") > 10


def test_hit_rows_show_damage_given_and_taken_with_the_ammo(client: Client) -> None:
    entries = (
        TimelineEntry(1000, "spawn", "parking", Pos(1, 2, 3)),
        TimelineEntry(
            2000,
            "hit_given",
            counterpart=Counterpart("MiG-15bis"),
            damage=0.125,
            lines=6,
            ammo="BULLET_12-7_USA_API",
            ammo_kind="gun",
        ),
        TimelineEntry(2500, "hit_taken", counterpart=Counterpart("MiG-15bis"), damage=0.0035, lines=3),
        TimelineEntry(
            3000,
            "hit_given",
            counterpart=Counterpart("M46 Patton"),
            damage=0.6,
            lines=2,
            ammo="M64",
            ammo_kind="ordnance",
        ),
        TimelineEntry(5000, "sortie_end", "landed"),
    )
    save(mission((replace(sortie(0, 1), timeline=entries),), extra_types=frozenset({"M46 Patton"})))

    html = detail(client, pk_of(1))

    assert "+12.5%" in html
    assert "\N{MINUS SIGN}0.35%" in html
    assert ".50 BMG API" in html
    assert "M64" in html
    assert html.count("timeline__damage--given") == 2
    assert html.count("timeline__damage--taken") == 1
    assert "Hit (dealt)" in html
    assert "Hit (taken)" in html


def test_a_timeline_stored_before_the_hit_rows_still_renders(client: Client) -> None:
    """No hit rows and no `damage` / `ammo` keys in the stored JSON (as written before this feature)."""
    save(duel())
    pk = pk_of(1)
    row = PlayerSortie.objects.get(pk=pk)
    assert all("damage" not in e and "ammo" not in e for e in row.timeline)

    html = detail(client, pk)

    assert "timeline__damage" not in html
    assert "Kill" in html


# --- detail: link preview ------------------------------------------------------------------------------------------
def test_open_graph_and_twitter_tags_for_discord(client: Client) -> None:
    save(mission((sortie(0, 1, name="Alpha", kills_air=2, kills_ground=5, assists=1, flight_time_s=600),)))

    html = detail(client, pk_of(1))

    assert '<meta property="og:title" content="Alpha — MiG-15bis — Landed">' in html
    description = re.search(r'<meta property="og:description" content="([^"]*)">', html)
    assert description is not None
    assert "2 air kills" in description.group(1)
    assert "5 ground kills" in description.group(1)
    assert "flight time 10 min" in description.group(1)
    assert '<meta name="twitter:card" content="summary">' in html  # no brand/og-default.png: no image tags
    assert "og:image" not in html
    assert '<meta property="og:url" content="http://testserver/sorties/' in html
    assert "<title>Alpha — MiG-15bis — Landed" in html


# --- list ----------------------------------------------------------------------------------------------------------
def many_sorties() -> MissionResult:
    return mission(
        (
            sortie(0, 1, kills_air=3, outcome="landed"),
            sortie(1, 1, kills_air=1, outcome="shot_down", is_plane_lost=True, aircraft_type="F-86A-5", coalition=2),
            sortie(2, 1, aircraft_type="Turret_IL10", role="gunner", combat_role=None),
            sortie(3, 1, aircraft_type="Il-10", combat_role="attack", kills_air=0),
            sortie(4, 2, kills_air=9),
        )
    )


def list_url(player: int, query: str = "") -> str:
    return f"/players/{Player.objects.get(account_uuid__endswith=f'{player:012d}').pk}/sorties/{query}"


def test_list_renders_within_budget_newest_first(client: Client) -> None:
    save(many_sorties())
    url = list_url(1)

    assert_simple_reads(client, url, max_queries=LIST_BUDGET)

    html = client.get(url).content.decode()
    assert html.count("/sorties/") >= 4 * 2  # a date link and an arrow per sortie
    assert 'aria-sort="descending"' in html  # the date column
    rows = [int(n) for n in re.findall(r'href="/sorties/(\d+)/" title', html)]
    assert rows == sorted(rows, reverse=True)  # same mission, spawn order: newest first


def test_list_queries_leave_the_big_json_columns_unloaded() -> None:
    """The lists never render ammo, damage breakdown or timeline: they must not be read from the database."""
    save(many_sorties())
    player = Player.objects.get(account_uuid__endswith=f"{1:012d}")
    mission_row = Mission.objects.get()

    pages = [
        sortie_page(player, SortieFilters(), "-date", "1").object_list,
        recent_sorties(player),
        mission_sorties(mission_row),
    ]

    for rows in pages:
        assert rows
        for row in rows:
            assert set(HEAVY_SORTIE_COLUMNS) <= row.get_deferred_fields()


def test_list_filters_by_aircraft_outcome_role_and_combat_role(client: Client) -> None:
    save(many_sorties())
    f86 = GameObject.objects.get(log_name="F-86A-5").pk

    def rows(query: str) -> int:
        response = client.get(list_url(1, query))
        assert response.status_code == 200
        return response.content.decode().count('href="/sorties/') // 2

    assert rows("") == 4
    assert rows(f"?aircraft={f86}") == 1
    assert rows("?outcome=shot_down") == 1
    assert rows("?role=gunner") == 1
    assert rows("?combat_role=attack") == 1
    assert rows("?outcome=landed&role=pilot") == 2
    assert rows("?outcome=bogus&role=bogus&combat_role=bogus&aircraft=abc") == 4  # unknown values are ignored


def test_list_aircraft_filter_offers_only_this_players_aircraft(client: Client) -> None:
    save(many_sorties())

    html = client.get(list_url(2)).content.decode()
    options = re.search(r'<select name="aircraft".*?</select>', html, re.DOTALL)

    assert options is not None
    assert "MiG-15bis" in options.group(0)
    assert "F-86A Sabre" not in options.group(0)


def test_list_sort_is_whitelisted(client: Client) -> None:
    save(many_sorties())

    def order(query: str) -> list[int]:
        html = client.get(list_url(1, query)).content.decode()
        return [int(n) for n in re.findall(r'href="/sorties/(\d+)/" title', html)]

    by_kills = client.get(list_url(1, "?sort=-kills_air")).content.decode()
    assert 'aria-sort="descending"' in by_kills
    ascending = order("?sort=date")
    assert ascending == sorted(ascending)
    assert order("?sort=-date") == sorted(ascending, reverse=True)
    for bad in ("?sort=name_at_time", "?sort=-password", "?sort=;drop", "?sort=mission__is_hidden"):
        assert order(bad) == sorted(ascending, reverse=True)  # falls back to newest first


def test_list_leaves_out_hidden_missions_and_404s_for_hidden_players(client: Client) -> None:
    save(many_sorties())
    other = mission((sortie(0, 1),), end_tick=9999)
    from tests.factories import meta  # a second mission of the same player

    save(other, meta("2026-09-20_10-00-00"))
    assert client.get(list_url(1)).content.decode().count('href="/sorties/') // 2 == 5

    Mission.objects.filter(mission_uid="2026-09-20_10-00-00").update(is_hidden=True)
    assert client.get(list_url(1)).content.decode().count('href="/sorties/') // 2 == 4

    url = list_url(2)
    Player.objects.filter(account_uuid__endswith="000000000002").update(is_hidden=True)
    assert client.get(url).status_code == 404
    assert client.get("/players/999999/sorties/").status_code == 404


def test_list_paginates_and_survives_a_bad_page(client: Client) -> None:
    save(mission(tuple(sortie(i, 1) for i in range(30))))

    first = client.get(list_url(1)).content.decode()
    assert "Showing 1\N{EN DASH}20 of 30" in first
    assert "Showing 21\N{EN DASH}30 of 30" in client.get(list_url(1, "?page=2")).content.decode()
    assert client.get(list_url(1, "?page=junk")).status_code == 200
    assert client.get(list_url(1, "?page=99")).status_code == 200


def test_modifications_are_shown_by_name(client: Client) -> None:
    """WM 0b101011 = base + mods 1, 3, 5 (MiG-15bis); base alone is "None"; an unlisted bit keeps its id."""
    save(
        mission((sortie(0, 1, weapon_mods=0b101011), sortie(1, 2, weapon_mods=1), sortie(2, 3, weapon_mods=1 | 1 << 9)))
    )

    named = detail(client, pk_of(1))
    assert "NR-23 cannons · Warning system · Anti-G suit" in named
    assert "Unknown modification" not in named
    assert re.search(r"Modifications</dt><dd><span class=\"muted\">None</span>", detail(client, pk_of(2)))
    assert "Unknown modification (id 9)" in detail(client, pk_of(3))


def test_upgrade_backfill_rederives_payload_names_from_the_stored_ids() -> None:
    """The loadout table was replaced: stored names follow the stored payload ids (IL-10 id 25 was renumbered); an id
    the table lacks gets no name (OQ-25)."""
    from il2ks.ops import migrate

    save(
        mission(
            (
                sortie(0, 1, aircraft_type="IL-10", payload_id=25),
                sortie(1, 2, aircraft_type="F-86A-5", payload_id=1),
                sortie(2, 3, aircraft_type="IL-10", payload_id=9999),
            )
        )
    )
    PlayerSortie.objects.update(payload_name="stale")

    assert migrate._check_payload_names()  # pyright: ignore[reportPrivateUsage]

    names = {s.name_at_time: s.payload_name for s in PlayerSortie.objects.all()}
    assert names["Player-1"].startswith("60 x PTAB-10-2.5 HEAT submunitions + 2 x FAB-100")
    assert names["Player-2"] not in ("", "stale")
    assert names["Player-3"] == ""
    assert not migrate._check_payload_names()  # pyright: ignore[reportPrivateUsage]
