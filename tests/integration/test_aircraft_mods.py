"""Weapon-mod tables and the significant-modifications filter of the aircraft page (FR-WEB-8, doc 12): the
`AircraftMods` rows, the level-2 rows per filter pattern (incremental == rebuild per pattern, role and tour), the
with / without / any semantics against hand-counted sortie sets, the page (tiles, tables, links), the simple-reads
budgets (TD-22) and the profile's mod names."""

from datetime import timedelta
from itertools import product

import pytest
from django.db import models
from django.test import Client
from django.urls import reverse

from il2ks.db.models import (
    AircraftMatchup,
    AircraftMods,
    AircraftPayload,
    AircraftStats,
    GameObject,
    Player,
    PlayerAircraftScope,
    PlayerSortie,
    Tour,
    TourAircraftStats,
)
from il2ks.ingest.aggregates import rebuild_aggregates
from tests.aircraft_pages import all_loadouts
from tests.factories import STARTED_AT, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("list_every_row")]

AIR = "air_superiority"
ATTACK = "attack"
OCTOBER = STARTED_AT + timedelta(days=40)

# MiG-15bis weapon mods (`weapon_mods.csv`): 1 NR-23 cannons, 2 improved air brakes, 5 Anti-G suit; the first three are
# significant, in this order, so a pattern reads [NR-23][air brakes][Anti-G]. The WM bitmask has bit 0 always set.
BASE = 1
NR23 = BASE | 1 << 1
ANTI_G = BASE | 1 << 5
NR23_ANTI_G = BASE | 1 << 1 | 1 << 5
SIGNIFICANT = (1, 2, 5)


def snapshot() -> dict[str, list[dict[str, object]]]:
    def rows(model: type[models.Model], *order: str) -> list[dict[str, object]]:
        found = list(model.objects.order_by(*order).values())
        for row in found:
            row.pop("id")
        return found

    return {
        "stats": rows(AircraftStats, "aircraft_id"),
        "tour_stats": rows(TourAircraftStats, "aircraft_id", "tour_id", "role", "mod_pattern"),
        "payloads": rows(AircraftPayload, "aircraft_id", "tour_id", "payload_name", "combat_role", "mod_pattern"),
        "matchups": rows(
            AircraftMatchup,
            "killer_aircraft_id",
            "victim_aircraft_id",
            "tour_id",
            "intercept",
            "scoped_side",
            "combat_role",
            "mod_pattern",
        ),
        "player_scopes": rows(PlayerAircraftScope, "aircraft_id", "player_id", "tour_id", "role", "mod_pattern"),
        "mods": rows(AircraftMods, "aircraft_id", "tour_id", "weapon_mods", "combat_role", "mod_pattern"),
    }


def history() -> tuple[Tour, Tour]:
    """September (MiG): player 1 flies with the Anti-G suit (2 air kills) and without any mod; player 2 attacks with
    the NR-23 cannons and the Anti-G suit and dies; player 4 flies with the Anti-G suit and dies. October: player 1
    with the Anti-G suit dies; player 4 without mods gets one kill. Plus a Sabre (no significant mods)."""
    save(
        mission(
            (
                sortie(0, 1, kills_air=2, kills_air_pvp=2, combat_role=AIR, weapon_mods=ANTI_G),
                sortie(1, 1, combat_role=AIR, weapon_mods=BASE),
                sortie(
                    2,
                    2,
                    combat_role=ATTACK,
                    ground_by_category={"tank": 1},
                    time_on_target_s=300.0,
                    is_death=True,
                    is_plane_lost=True,
                    weapon_mods=NR23_ANTI_G,
                ),
                sortie(3, 4, combat_role=AIR, is_death=True, weapon_mods=ANTI_G),
                sortie(4, 5, aircraft_type="F-86A-5", coalition=2, combat_role=AIR, weapon_mods=3),
            ),
            (),
        )
    )
    save(
        mission(
            (
                sortie(0, 1, combat_role=AIR, is_death=True, is_plane_lost=True, weapon_mods=ANTI_G),
                sortie(1, 4, kills_air=1, kills_air_pvp=1, combat_role=AIR, weapon_mods=BASE),
            ),
            (),
        ),
        meta("2026-10-29_22-00-00", OCTOBER),
    )
    september, october = Tour.objects.order_by("started_at")
    return september, october


def mig() -> GameObject:
    return GameObject.objects.get(log_name="MiG-15bis")


def pattern_row(pattern: str, tour: Tour | None = None, role: str = "all") -> TourAircraftStats | None:
    return TourAircraftStats.objects.filter(aircraft=mig(), tour=tour, role=role, mod_pattern=pattern).first()


def matches(pattern: str, weapon_mods: int) -> bool:
    """The semantics of a pattern, from its definition: `+` has the mod, `-` has not, `*` either."""
    return all(
        c == "*" or (c == "+") == bool(weapon_mods >> mod_id & 1)
        for c, mod_id in zip(pattern, SIGNIFICANT, strict=True)
    )


def test_hand_counted_pattern_sets() -> None:
    september, october = history()

    anti_g = pattern_row("**+")  # all time, every role: S0, S2, S3 and the October sortie of player 1
    assert anti_g is not None
    assert (anti_g.sorties, anti_g.pilots, anti_g.deaths, anti_g.kills_air) == (4, 3, 3, 2)
    without = pattern_row("**-")  # the two sorties without the suit
    assert without is not None
    assert (without.sorties, without.pilots, without.deaths, without.kills_air) == (2, 2, 0, 1)
    assert (pattern_row("+**") or AircraftStats()).sorties == 1  # NR-23: only player 2's attack sortie
    assert (pattern_row("+*+") or AircraftStats()).sorties == 1
    assert pattern_row("+*-") is None  # NR-23 without the suit: nobody (no empty rows)
    assert (pattern_row("-*+") or AircraftStats()).sorties == 3  # the suit without the cannons
    # per tour and role
    assert (pattern_row("**+", september) or AircraftStats()).sorties == 3
    assert (pattern_row("**+", october) or AircraftStats()).sorties == 1
    in_air = pattern_row("**+", None, AIR)
    assert in_air is not None
    assert (in_air.sorties, in_air.pilots) == (3, 2)  # players 1 and 4
    assert (pattern_row("**+", None, ATTACK) or AircraftStats()).sorties == 1
    # every sortie is in exactly one of with / without the suit
    assert anti_g.sorties + without.sorties == AircraftStats.objects.get(aircraft=mig()).sorties


def test_every_pattern_equals_a_direct_count_of_the_sorties() -> None:
    september, _ = history()
    sorties = list(PlayerSortie.objects.filter(aircraft=mig(), role="pilot").select_related("mission__tour"))
    for states in product("*+-", repeat=3):
        pattern = "".join(states)
        found = [s for s in sorties if matches(pattern, s.weapon_mods)]
        for tour, role in ((None, "all"), (september, "all"), (None, AIR), (september, ATTACK)):
            scoped = [
                s
                for s in found
                if (tour is None or s.mission.tour_id == tour.pk) and (role == "all" or s.combat_role == role)
            ]
            row = pattern_row("" if pattern == "***" else pattern, tour, role)
            if role == "all" and tour is None and pattern == "***":
                continue  # that is `AircraftStats` itself
            assert (row.sorties if row else 0) == len(scoped), (pattern, tour, role)
            if row is not None:
                assert row.deaths == sum(s.is_death for s in scoped)
                assert row.kills_air == sum(s.kills_air for s in scoped)
                assert row.pilots == len({s.player_id for s in scoped})


def test_types_without_significant_mods_have_no_filter_rows() -> None:
    history()
    sabre = GameObject.objects.get(log_name="F-86A-5")

    assert not TourAircraftStats.objects.filter(aircraft=sabre).exclude(mod_pattern="").exists()
    assert not AircraftPayload.objects.filter(aircraft=sabre).exclude(mod_pattern="").exists()
    # the MiG has 3**3 - 1 = 26 patterns possible; only the ones with sorties are stored (here: 3 tours scopes x roles)
    patterns = set(TourAircraftStats.objects.filter(aircraft=mig()).values_list("mod_pattern", flat=True))
    assert patterns <= {"".join(p) for p in product("*+-", repeat=3)} - {"***"} | {""}
    assert "+*-" not in patterns


def test_mods_rows_are_per_set_role_and_pattern() -> None:
    history()

    def row(mods: int, role: str, pattern: str = "") -> AircraftMods | None:
        return AircraftMods.objects.filter(
            aircraft=mig(), tour=None, weapon_mods=mods, combat_role=role, mod_pattern=pattern
        ).first()

    anti_g_air = row(ANTI_G, AIR)
    assert anti_g_air is not None
    assert (anti_g_air.sorties, anti_g_air.kills_air, anti_g_air.deaths, anti_g_air.kills_air_pvp) == (3, 2, 2, 2)
    plain = row(BASE, AIR)
    assert plain is not None
    assert (plain.sorties, plain.kills_air) == (2, 1)
    both = row(NR23_ANTI_G, ATTACK)
    assert both is not None
    assert (both.sorties, both.deaths) == (1, 1)
    # inside a filter only the sets that match are there
    assert row(BASE, AIR, "**+") is None
    assert row(ANTI_G, AIR, "**+") is not None
    assert row(ANTI_G, AIR, "**-") is None
    assert row(BASE, AIR, "**-") is not None


def test_mods_table_equals_the_loadout_style_computation() -> None:
    history()
    groups: dict[tuple[int, str], list[PlayerSortie]] = {}
    for s in PlayerSortie.objects.filter(aircraft=mig(), role="pilot"):
        groups.setdefault((s.weapon_mods, s.combat_role or ""), []).append(s)

    stored = {
        (r.weapon_mods, r.combat_role): r
        for r in AircraftMods.objects.filter(aircraft=mig(), mod_pattern="", tour=None)
    }
    assert set(stored) == set(groups)
    for key, found in groups.items():
        assert stored[key].sorties == len(found)
        assert stored[key].kills_air == sum(s.kills_air for s in found)
        assert stored[key].kills_ground == sum(s.kills_ground for s in found)
        assert stored[key].deaths == sum(s.is_death for s in found)
        assert stored[key].kills_air_pvp == sum(s.kills_air_pvp for s in found)
    # the loadouts of the same sorties add up to the same total as the mods
    assert sum(p.sorties for p in AircraftPayload.objects.filter(aircraft=mig(), mod_pattern="", tour=None)) == sum(
        r.sorties for r in stored.values()
    )


def test_incremental_equals_rebuild_for_every_pattern_role_and_tour() -> None:
    september, _ = history()
    # another September mission arrives late, and one is re-ingested with other mods
    save(
        mission(
            (
                sortie(0, 2, combat_role=AIR, weapon_mods=NR23, kills_air=1),
                sortie(1, 5, combat_role=ATTACK, weapon_mods=NR23_ANTI_G, ground_by_category={"tank": 2}),
            ),
            (),
        ),
        meta("2026-09-02_20-00-00", STARTED_AT + timedelta(days=1)),
    )
    save(
        mission((sortie(0, 1, combat_role=AIR, weapon_mods=NR23_ANTI_G), sortie(1, 1, combat_role=AIR)), ()),
        meta("2026-09-03_20-00-00", STARTED_AT + timedelta(days=2)),
    )
    save(
        mission((sortie(0, 1, combat_role=AIR, weapon_mods=BASE),), ()),
        meta("2026-09-03_20-00-00", STARTED_AT + timedelta(days=2)),
    )
    incremental = snapshot()
    assert september.pk

    rebuild_aggregates()

    assert snapshot() == incremental
    assert AircraftMods.objects.filter(mod_pattern="**+").exists()


def test_rebuild_repairs_drifted_filter_rows() -> None:
    history()
    good = snapshot()
    TourAircraftStats.objects.exclude(mod_pattern="").update(sorties=99)
    AircraftMods.objects.filter(mod_pattern="**+").delete()
    AircraftPayload.objects.exclude(mod_pattern="").update(kills_air=42)
    TourAircraftStats.objects.create(aircraft=mig(), tour=None, role=AIR, mod_pattern="++-", sorties=5)

    rebuild_aggregates()

    assert snapshot() == good


def detail(log_name: str = "MiG-15bis") -> str:
    return reverse("web:aircraft-detail", args=[GameObject.objects.get(log_name=log_name).pk])


def test_detail_filter_scopes_tiles_tables_and_pilot_count(client: Client) -> None:
    september, _ = history()
    url = detail()

    unfiltered = client.get(f"{url}?tour=all")
    with_suit = client.get(f"{url}?tour=all&mod5=with")
    without_suit = client.get(f"{url}?tour=all&mod5=without")
    in_september = client.get(f"{url}?tour={september.pk}&mod5=with")
    in_air = client.get(f"{url}?tour=all&mod5=with&role=air_superiority")
    combined = client.get(f"{url}?tour=all&mod5=with&mod1=without")
    nobody = client.get(f"{url}?tour=all&mod1=with&mod5=without")

    assert [r.context["tile"].sorties for r in (unfiltered, with_suit, without_suit)] == [6, 4, 2]
    assert [r.context["tile"].pilots for r in (unfiltered, with_suit, without_suit)] == [3, 3, 2]
    assert in_september.context["tile"].sorties == 3
    assert (in_air.context["tile"].sorties, in_air.context["tile"].pilots) == (3, 2)
    assert combined.context["tile"].sorties == 3
    assert nobody.context["tile"].sorties == 0  # a scope nobody flew: zero tiles, not an error
    # the tables follow the filter
    assert sum(r.payload.sorties for r in all_loadouts(client, f"{url}?tour=all&mod5=with")) == 4
    assert {r.label or "-" for r in with_suit.context["mod_sets"]} == {"Anti-G suit", "NR-23 cannons + Anti-G suit"}
    assert {r.label or "-" for r in without_suit.context["mod_sets"]} == {"-"}
    assert {r.label or "-" for r in unfiltered.context["mod_sets"]} == {
        "-",
        "Anti-G suit",
        "NR-23 cannons + Anti-G suit",
    }
    # the filter state, per significant mod
    assert [(f.name, f.state) for f in combined.context["mod_filters"]] == [
        ("NR-23 cannons", "without"),
        ("Improved air brakes and wing", "any"),
        ("Anti-G suit", "with"),
    ]
    assert combined.context["mod_filtered"]
    assert not unfiltered.context["mod_filtered"]


def test_filter_links_keep_the_other_parameters_and_unknown_values_mean_any(client: Client) -> None:
    september, _ = history()

    body = client.get(f"{detail()}?tour={september.pk}&role=attack&mod5=with").content.decode()
    junk = client.get(f"{detail()}?tour=all&mod5=sideways")

    assert body.count('aria-label="Filter by modification') == 3
    link = next(line for line in body.splitlines() if "mod1=with" in line)
    assert all(part in link for part in (f"tour={september.pk}", "role=attack", "mod5=with"))
    assert "No modifications" in client.get(f"{detail()}?tour=all").content.decode()
    assert junk.context["tile"].sorties == 6
    assert not junk.context["mod_filtered"]


def test_no_filter_on_types_without_significant_mods(client: Client) -> None:
    history()

    response = client.get(f"{detail('F-86A-5')}?tour=all&mod1=with")  # the param means nothing there

    assert response.context["mod_filters"] == ()
    assert not response.context["mod_filtered"]
    assert "Filter by modification" not in response.content.decode()
    assert [r.label for r in response.context["mod_sets"]] == ["A-1CM Gunsight"]


def test_mod_table_sorts_with_a_whitelist_and_keeps_the_scope(client: Client) -> None:
    history()
    url = f"{detail()}?tour=all&mod5=with"

    def labels(query: str) -> list[str]:
        return [r.label for r in client.get(url + query).context["mod_sets"]]

    assert labels("&msort=-sorties") == ["Anti-G suit", "NR-23 cannons + Anti-G suit"]
    assert labels("&msort=mods") == ["Anti-G suit", "NR-23 cannons + Anti-G suit"]
    assert labels("&msort=-mods") == ["NR-23 cannons + Anti-G suit", "Anti-G suit"]
    assert labels("&msort=password") == labels("")
    body = client.get(url).content.decode()
    link = next(line for line in body.splitlines() if "msort=" in line)
    assert all(part in link for part in ("mod5=with", "tour=all"))


def test_filter_page_budgets_and_simple_reads(client: Client) -> None:
    september, _ = history()
    base = detail()

    # tours + stats row + scoped row + hits + matchups + two top-pilot tables + loadouts + mods + 2 context
    assert_simple_reads(client, f"{base}?tour=all&mod5=with&mod1=without", max_queries=11)
    assert_simple_reads(client, f"{base}?tour={september.pk}&role=attack&mod5=with", max_queries=12)
    assert_simple_reads(client, f"{base}?tour=all&msort=-elo", max_queries=10)


def test_profile_shows_no_mod_sets_any_more(client: Client) -> None:
    """OQ-117: the profile keeps only the favourite loadout; the mod sets live on the aircraft page."""
    history()
    player = Player.objects.get(account_uuid__endswith="000000000001")

    body = client.get(f"/players/{player.pk}/?tour=all").content.decode()

    assert "Favorite loadout" in body
    assert "Anti-G suit" not in body
    assert "Set 33" not in body
