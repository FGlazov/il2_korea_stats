"""`save_mission` rules from the 2026-10 maintainer round: kill natural key (D2), PlayerMission for pilots only (D3),
sides from country codes (D5), crew/equipment classes (D6), friendly fire counters (D7), PK-stable level 2 (D1)."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction

from il2ks.core.replay.result import AmmoCounts, KillResult, MissionResult
from il2ks.db.models import Country, GameObject, Kill, Mission, Player, PlayerAircraft, PlayerMission, PlayerSortie
from il2ks.ingest import persist
from tests.factories import STARTED_AT, FakeCatalog, account, kill, mission, save, sortie

pytestmark = pytest.mark.django_db


# --- kills: natural key (victim_sortie, killer_sortie) ---


def _duel(*kills: KillResult) -> MissionResult:
    return mission((sortie(0, 1), sortie(1, 2, aircraft_type="F-86A-5", coalition=2)), kills)


def test_kill_is_upserted_by_victim_and_killer_keeping_its_pk() -> None:
    """D2: credit and tick are plain attributes of the one row per (victim sortie, killer sortie)."""
    save(_duel(kill(20_000, killer=0, victim=1, credit="assist")))
    pk = Kill.objects.get().pk

    save(_duel(kill(21_000, killer=0, victim=1, credit="kill")))

    k = Kill.objects.get()
    assert (k.pk, k.credit, k.tick) == (pk, "kill", 21_000)
    assert k.time == STARTED_AT + timedelta(seconds=420)


def test_two_results_for_one_pair_keep_the_kill_and_log_it(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="il2ks.ingest.persist")
    save(_duel(kill(20_000, killer=0, victim=1, credit="assist"), kill(21_000, killer=0, victim=1, credit="kill")))

    k = Kill.objects.get()
    assert (k.credit, k.tick) == ("kill", 21_000)
    assert "two kill results" in caplog.text


def test_two_results_for_one_pair_keep_the_first_when_credits_match() -> None:
    save(_duel(kill(20_000, killer=0, victim=1), kill(21_000, killer=0, victim=1)))

    assert Kill.objects.get().tick == 20_000


def test_kill_constraint_allows_one_row_per_victim_and_killer() -> None:
    save(_duel(kill(20_000, killer=0, victim=1)))
    k = Kill.objects.get()
    k.pk = None
    k.tick += 100
    k.credit = "assist"
    with pytest.raises(IntegrityError), transaction.atomic():
        k.save()


# --- PlayerMission only for pilots ---


def test_player_mission_only_for_players_with_a_pilot_sortie() -> None:
    """D3: gunner-only players get no PlayerMission, but still count in `players_total` and are never deleted."""
    save(
        mission(
            (
                sortie(0, 1),
                sortie(1, 2, aircraft_type="Turret_IL10", role="gunner", coalition=2),
                sortie(2, 3, aircraft_type="Turret_IL10", role="gunner"),
                sortie(3, 3, coalition=2),  # player 3 is a gunner first, then a pilot
            )
        )
    )

    assert set(PlayerMission.objects.values_list("player__account_uuid", flat=True)) == {account(1), account(3)}
    assert PlayerMission.objects.get(player__account_uuid=account(3)).coalition == 2  # first pilot sortie's
    assert Mission.objects.get().players_total == 3
    assert Player.objects.count() == 3


def test_player_mission_is_removed_when_a_player_becomes_gunner_only() -> None:
    first = mission((sortie(0, 1), sortie(1, 2)))
    save(first)
    assert PlayerMission.objects.filter(player__account_uuid=account(2)).exists()

    save(mission((first.sorties[0], replace(first.sorties[1], role="gunner", aircraft_type="Turret_IL10"))))

    assert set(PlayerMission.objects.values_list("player__account_uuid", flat=True)) == {account(1)}
    p2 = Player.objects.get(account_uuid=account(2))
    assert (p2.sorties, p2.flight_time_s) == (0, 0.0)


# --- sides from country codes ---


def test_mission_sides_come_from_the_country_code_not_the_coalition_number() -> None:
    """D5: coalition numbers are swapped here (501 is coalition 2), the sides still follow 5xx/6xx."""
    save(
        mission(
            (
                sortie(0, 1, coalition=2, country=501),
                sortie(1, 2, coalition=2, country=502),
                sortie(2, 3, coalition=1, country=601),
                sortie(3, 4, coalition=1, country=999),  # no side: counted in sorties_total only
                sortie(4, 5, aircraft_type="Turret_IL10", role="gunner", country=501),  # gunners aren't counted
            ),
            countries={501: 2, 502: 2, 601: 1, 999: 1},
        )
    )

    m = Mission.objects.get()
    assert (m.sorties_total, m.redfor_sorties, m.blufor_sorties) == (4, 2, 1)


def test_country_rows_are_named_by_side_then_by_coalition() -> None:
    save(mission((sortie(0, 1),), countries={502: 2, 602: 1, 700: 2, 800: 0}))

    names = dict(Country.objects.values_list("code", "display_name"))
    assert names == {502: "REDFOR", 602: "BLUFOR", 700: "BLUFOR", 800: "Neutral"}


# --- friendly fire counters ---


def test_friendly_fire_flows_into_every_counter_table() -> None:
    """D7: friendly_* are counters like kills: sortie -> PlayerMission, Player, PlayerAircraft (pilots) and Mission."""
    save(
        mission(
            (
                sortie(0, 1, friendly_kills=1, friendly_hits=5, friendly_damage=0.75),
                sortie(1, 1, aircraft_type="IL-10", friendly_hits=2, friendly_damage=0.25),
                sortie(2, 2, friendly_kills=2, coalition=2),
                sortie(3, 3, aircraft_type="Turret_IL10", role="gunner", friendly_hits=40, friendly_damage=9.0),
            )
        )
    )

    s = PlayerSortie.objects.get(account_uuid=account(1), aircraft__log_name="MiG-15bis")
    assert (s.friendly_kills, s.friendly_hits, s.friendly_damage) == (1, 5, 0.75)
    pm = PlayerMission.objects.get(player__account_uuid=account(1))
    assert (pm.friendly_kills, pm.friendly_hits, pm.friendly_damage) == (1, 7, pytest.approx(1.0))
    p1 = Player.objects.get(account_uuid=account(1))
    assert (p1.friendly_kills, p1.friendly_hits, p1.friendly_damage) == (1, 7, pytest.approx(1.0))
    mig = PlayerAircraft.objects.get(player=p1, aircraft__log_name="MiG-15bis")
    assert (mig.friendly_kills, mig.friendly_hits, mig.friendly_damage) == (1, 5, 0.75)
    assert Mission.objects.get().friendly_kills == 3  # the gunner's numbers feed nothing (FR-WEB-14)
    assert Player.objects.get(account_uuid=account(3)).friendly_hits == 0


# --- object classes ---


def test_crew_and_equipment_classes_are_stored() -> None:
    """D6: `crew` and `equipment` are valid `GameObject.cls` values (DB constraint) and follow the catalog."""
    catalog = FakeCatalog({"BotPlanePilot_X": ("Pilot", "crew"), "CParachute": ("Parachute", "equipment")})

    persist.register_game_objects(["BotPlanePilot_X", "CParachute"], catalog)

    assert dict(GameObject.objects.values_list("log_name", "cls")) == {
        "BotPlanePilot_X": "crew",
        "CParachute": "equipment",
    }


# --- level 2 rows keep their PKs on re-ingest ---


def test_reingest_keeps_player_aircraft_pks() -> None:
    first = mission((sortie(0, 1), sortie(1, 1, aircraft_type="IL-10"), sortie(2, 2)))
    save(first)
    pks = {(r.player_id, r.aircraft_id): r.pk for r in PlayerAircraft.objects.all()}

    # Player 1's Il-10 sortie goes away, the MiG sortie changes: that row is updated in place, never recreated.
    save(mission((replace(first.sorties[0], kills_air=3), replace(first.sorties[2], index=1))))

    after = {(r.player_id, r.aircraft_id): r for r in PlayerAircraft.objects.all()}
    assert set(after) < set(pks)
    assert len(after) == 2
    for key, row in after.items():
        assert row.pk == pks[key]
    assert PlayerAircraft.objects.get(player__account_uuid=account(1)).kills_air == 3


# --- resupply and ammo used (FR-ING-24) ---


def _ammo(**sortie_args: object) -> dict[str, object]:
    save(mission((sortie(0, 1, **sortie_args),)))  # pyright: ignore[reportArgumentType]
    return PlayerSortie.objects.get().ammo


def test_ammo_used_is_loaded_minus_left_per_type() -> None:
    ammo = _ammo(ammo_loaded=AmmoCounts(400, 100, 2, 8), ammo_left=AmmoCounts(150, 100, 0, 4))
    assert ammo["used"] == {"bullets": 250, "shells": 0, "bombs": 2, "rockets": 4}
    assert ammo["loaded"] == {"bullets": 400, "shells": 100, "bombs": 2, "rockets": 8}
    assert PlayerSortie.objects.get().resupplied is False


def test_ammo_used_is_unknown_after_a_resupply() -> None:
    ammo = _ammo(
        resupplied=True,
        ammo_loaded=AmmoCounts(400, 100, 2, 8),
        ammo_left=AmmoCounts(150, 100, 0, 4),
        store_releases=1,
        rocket_salvos=1,
    )
    assert ammo["used"] == {"bullets": None, "shells": None, "bombs": None, "rockets": None}
    assert PlayerSortie.objects.get().resupplied is True


def test_bombs_used_is_unknown_when_more_is_left_than_loaded() -> None:
    """IL-10 bomblet payloads load 4 (stations) and leave 60+ (bomblets): units differ, so no number."""
    ammo = _ammo(ammo_loaded=AmmoCounts(400, 100, 4, 0), ammo_left=AmmoCounts(300, 100, 96, 0), store_releases=2)
    assert ammo["used"] == {"bullets": 100, "shells": 0, "bombs": None, "rockets": 0}


def test_ammo_used_is_unknown_without_a_sortie_end() -> None:
    ammo = _ammo(ammo_left=None, ammo_loaded=AmmoCounts(400, 0, 2, 6), store_releases=1, rocket_salvos=1)
    assert ammo["left"] is None
    assert ammo["used"] == {"bullets": None, "shells": None, "bombs": None, "rockets": None}


def test_after_a_loss_guns_and_released_stores_are_unknown() -> None:
    """AType 4 of a destroyed aircraft the pilot climbed out of reads as empty stores, whatever was dropped. Release
    events are commands, not bomb counts: dropping 2 of 4 bombs leaves the number unknown."""
    ammo = _ammo(
        ammo_loaded=AmmoCounts(400, 0, 4, 6),
        ammo_left=AmmoCounts(150, 0, 0, 0),
        ammo_left_after_loss=True,
        store_releases=2,
        rocket_salvos=1,
    )
    assert ammo["used"] == {"bullets": None, "shells": None, "bombs": None, "rockets": None}
    assert ammo["left"] == {"bullets": 150, "shells": 0, "bombs": 0, "rockets": 0}  # the raw AType 4 stays
    assert ammo["left_after_loss"] is True
    assert ammo["releases"] == {"stores": 2, "rocket_salvos": 1}


def test_after_a_loss_bombs_and_rockets_never_released_were_not_used() -> None:
    """A bailout with all 4 bombs and 6 rockets still on board: AType 4 says 0 left, the missing releases say 0 used."""
    ammo = _ammo(ammo_loaded=AmmoCounts(400, 0, 4, 6), ammo_left=AmmoCounts(150, 0, 0, 0), ammo_left_after_loss=True)
    assert ammo["used"] == {"bullets": None, "shells": None, "bombs": 0, "rockets": 0}


def test_bombs_with_more_left_than_loaded_and_no_release_were_not_used() -> None:
    ammo = _ammo(ammo_loaded=AmmoCounts(400, 0, 4, 0), ammo_left=AmmoCounts(300, 0, 96, 0))
    assert ammo["used"] == {"bullets": 100, "shells": 0, "bombs": 0, "rockets": 0}


def test_a_resupplied_sortie_without_releases_used_no_bombs_or_rockets() -> None:
    ammo = _ammo(resupplied=True, ammo_loaded=AmmoCounts(400, 0, 2, 8), ammo_left=AmmoCounts(150, 0, 0, 0))
    assert ammo["used"] == {"bullets": None, "shells": None, "bombs": 0, "rockets": 0}


def test_trusted_ammo_left_beats_the_release_events() -> None:
    """When AType 4 is trusted, loaded - left stays the answer even if it disagrees with the events (measured on the
    samples: one event can drop a pair of bombs, and a rocket event is a salvo)."""
    ammo = _ammo(
        ammo_loaded=AmmoCounts(400, 0, 4, 8),
        ammo_left=AmmoCounts(150, 0, 0, 0),
        store_releases=1,
        rocket_salvos=2,
    )
    assert ammo["used"] == {"bullets": 250, "shells": 0, "bombs": 4, "rockets": 8}


def test_after_a_loss_released_bombs_and_rockets_get_an_all_loaded_estimate_kept_apart_from_used() -> None:
    """OQ-101: "used" stays unknown (null), the estimate (= all loaded) is stored separately; guns get none."""
    ammo = _ammo(
        ammo_loaded=AmmoCounts(400, 0, 4, 6),
        ammo_left=AmmoCounts(150, 0, 0, 0),
        ammo_left_after_loss=True,
        store_releases=1,
        rocket_salvos=1,
    )
    assert ammo["used"] == {"bullets": None, "shells": None, "bombs": None, "rockets": None}
    assert ammo["used_estimate"] == {"bombs": 4, "rockets": 6}


def test_no_estimate_when_nothing_was_released_when_trusted_or_when_resupplied() -> None:
    loaded, left = AmmoCounts(400, 0, 4, 6), AmmoCounts(150, 0, 0, 0)
    assert _ammo(ammo_loaded=loaded, ammo_left=left, ammo_left_after_loss=True)["used_estimate"] == {}
    assert _ammo(ammo_loaded=loaded, ammo_left=left, store_releases=1)["used_estimate"] == {}  # "left" is trusted
    resupplied = _ammo(ammo_loaded=loaded, ammo_left=left, ammo_left_after_loss=True, resupplied=True, store_releases=1)
    assert resupplied["used_estimate"] == {}
    one_kind = _ammo(ammo_loaded=loaded, ammo_left=left, ammo_left_after_loss=True, rocket_salvos=2)
    assert one_kind["used_estimate"] == {"rockets": 6}


def test_ended_by_mission_end_is_persisted_with_the_airborne_outcome() -> None:
    """Doc 13: a sortie the server force-ended keeps its outcome (the state then) and the flag."""
    save(mission((sortie(0, 1, outcome="airborne", ended_by_mission_end=True), sortie(1, 2))))

    forced, normal = PlayerSortie.objects.order_by("pk")
    assert (forced.outcome, forced.ended_by_mission_end) == ("airborne", True)
    assert (normal.outcome, normal.ended_by_mission_end) == ("landed", False)


def test_one_aircraft_row_whatever_case_the_log_writes() -> None:
    """Real logs write both `Il-10` and `IL-10`: one `GameObject`, keyed by the catalog's spelling."""
    from il2ks.core.catalog.loader import load_default_catalog

    objects = persist.register_game_objects(["Il-10", "IL-10"], load_default_catalog())
    again = persist.register_game_objects(["Il-10"], load_default_catalog())

    assert list(GameObject.objects.values_list("log_name", flat=True)) == ["IL-10"]
    assert objects["Il-10"].pk == objects["IL-10"].pk == again["Il-10"].pk


def test_the_upgrade_merges_aircraft_rows_that_differ_only_in_case(tmp_path: Path) -> None:
    from il2ks.db.models import PlayerSortie
    from il2ks.ops import migrate
    from tests.ops_helpers import make_instance

    save(mission((sortie(0, 1, aircraft_type="IL-10"),)))
    dup = GameObject.objects.create(log_name="Il-10", display_name="Il-10", cls="attacker", is_playable=True)
    PlayerSortie.objects.update(aircraft=dup)  # as an old database: the sortie sits on the odd spelling

    migrate._run_backfills(make_instance(tmp_path), [migrate.BACKFILL_AIRCRAFT_CASE])  # pyright: ignore[reportPrivateUsage]

    assert list(GameObject.objects.values_list("log_name", flat=True)) == ["IL-10"]
    assert PlayerSortie.objects.get().aircraft.log_name == "IL-10"


def test_b_29_with_a_space_is_the_same_aircraft_as_b_29() -> None:
    """Real logs write `B 29` and `B-29` (maintainer, OQ-120): one `GameObject`, through a catalog alias."""
    from il2ks.core.catalog.loader import load_default_catalog

    objects = persist.register_game_objects(["B 29", "B-29"], load_default_catalog())

    assert list(GameObject.objects.values_list("log_name", flat=True)) == ["B-29"]
    assert objects["B 29"].pk == objects["B-29"].pk


def test_the_upgrade_merges_a_b_29_row_written_with_a_space(tmp_path: Path) -> None:
    from il2ks.db.models import PlayerSortie
    from il2ks.ops import migrate
    from tests.ops_helpers import make_instance

    save(mission((sortie(0, 1, aircraft_type="B-29"),)))
    dup = GameObject.objects.create(log_name="B 29", display_name="B-29", cls="bomber")
    PlayerSortie.objects.update(aircraft=dup)  # as an old database: the sortie sits on the spaced spelling

    migrate._run_backfills(make_instance(tmp_path), [migrate.BACKFILL_AIRCRAFT_ALIASES])  # pyright: ignore[reportPrivateUsage]

    assert list(GameObject.objects.values_list("log_name", flat=True)) == ["B-29"]
    assert PlayerSortie.objects.get().aircraft.log_name == "B-29"
