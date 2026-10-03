"""`save_mission`: MissionResult -> level-1 rows (FR-ING-5, FR-ING-6, FR-ING-7, FR-ING-9, FR-WEB-13)."""

from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction

from il2ks.core.replay.result import Counterpart, DamageExchange, MissionResult, TimelineEntry
from il2ks.db.models import (
    Country,
    GameObject,
    Kill,
    Mission,
    Player,
    PlayerAircraft,
    PlayerMission,
    PlayerName,
    PlayerSortie,
)
from il2ks.ingest import persist
from tests.factories import (
    SERVER_UID,
    STARTED_AT,
    FakeCatalog,
    account,
    kill,
    meta,
    mission,
    reindexed,
    rows,
    save,
    sortie,
)

pytestmark = pytest.mark.django_db

ALL_TABLES = (Mission, GameObject, Country, Player, PlayerName, PlayerSortie, Kill, PlayerMission, PlayerAircraft)


def basic_result() -> MissionResult:
    """Player 1 (MiG) shoots down player 2 (Sabre) and an AI tank; player 3 flies as a gunner."""
    return mission(
        (
            sortie(0, 1, kills_air=1, kills_ground=1, flight_time_s=900.0),
            sortie(
                1,
                2,
                aircraft_type="F-86A-5",
                coalition=2,
                outcome="shot_down",
                pilot_fate="bailed_out",
                is_plane_lost=True,
                damage_taken=1.0,
                flight_time_s=300.0,
            ),
            sortie(2, 3, aircraft_type="Turret_IL10", role="gunner", flight_time_s=120.0),
        ),
        (
            kill(20_000, killer=0, victim=1),
            kill(21_000, killer=0, victim=None, victim_type="M46 Patton", victim_kind="ground"),
            kill(22_000, killer=None, victim=1, killer_type="MiG-15bis", credit="assist"),
        ),
    )


def test_mission_row_values() -> None:
    result = basic_result()
    m = save(result)

    assert (m.server_uid, m.mission_uid) == (SERVER_UID, "2026-09-19_22-34-13")
    assert m.started_at == STARTED_AT
    assert m.duration_s == result.mission.end_tick / 50
    assert m.ended_at == STARTED_AT + timedelta(seconds=result.mission.end_tick / 50)
    assert m.file_path == "archive/2026-09-19_22-34-13.zip"
    assert m.countries == {"501": 1, "601": 2}
    assert m.settings == {"raw": "0010001"}
    assert m.completed_cleanly is True
    # Counters: pilot sorties only, players of any role.
    m.refresh_from_db()
    assert (m.players_total, m.sorties_total, m.redfor_sorties, m.blufor_sorties) == (3, 2, 1, 1)
    assert (m.kills_air, m.kills_ground) == (1, 1)


def test_sortie_row_values() -> None:
    save(basic_result())
    s = PlayerSortie.objects.select_related("player", "aircraft").get(account_uuid=account(2))

    assert s.player.account_uuid == account(2)
    assert s.aircraft.log_name == "F-86A-5"
    assert s.spawned_at == STARTED_AT + timedelta(seconds=2000 / 50)
    assert s.took_off_at == STARTED_AT + timedelta(seconds=2500 / 50)
    assert s.landed_at is None
    assert (s.outcome, s.pilot_fate, s.is_plane_lost, s.damage_taken) == ("shot_down", "bailed_out", True, 1.0)
    assert s.payload_name == "Payload 1"
    assert s.air_start is False
    assert (s.pos_spawn_x, s.pos_spawn_y, s.pos_spawn_z) == (100.0, 50.0, 200.0)
    assert s.ammo == {
        "loaded": {"bullets": 400, "shells": 0, "bombs": 0, "rockets": 0},
        "left": {"bullets": 200, "shells": 0, "bombs": 0, "rockets": 0},
        "hits": [],
    }


def test_json_fields_link_counterpart_sorties_by_pk() -> None:
    base = basic_result()
    victim = Counterpart("F-86A-5", sortie_index=1, coalition=2)
    shooter = replace(
        base.sorties[0],
        damage=(DamageExchange(victim, damage_dealt=0.8, hits_dealt=12),),
        timeline=(TimelineEntry(20_000, "kill", "F-86A-5", pos=None, counterpart=victim),),
    )
    save(replace(base, sorties=(shooter, *base.sorties[1:])))

    row = PlayerSortie.objects.get(account_uuid=account(1))
    victim_pk = PlayerSortie.objects.get(account_uuid=account(2)).pk
    assert row.damage_breakdown == [
        {
            "counterpart": {"object_type": "F-86A-5", "coalition": 2, "sortie_id": victim_pk},
            "damage_dealt": 0.8,
            "damage_taken": 0.0,
            "hits_dealt": 12,
            "hits_taken": 0,
        }
    ]
    assert row.timeline == [
        {
            "tick": 20_000,
            "at": (STARTED_AT + timedelta(seconds=400)).isoformat(),
            "kind": "kill",
            "detail": "F-86A-5",
            "pos": None,
            "counterpart": {"object_type": "F-86A-5", "coalition": 2, "sortie_id": victim_pk},
        }
    ]


def test_kills_are_pvp_only() -> None:
    save(basic_result())

    kills = list(Kill.objects.select_related("killer_sortie", "victim_sortie"))
    assert len(kills) == 1
    k = kills[0]
    assert (k.killer_sortie.account_uuid, k.victim_sortie.account_uuid) == (account(1), account(2))
    assert k.time == STARTED_AT + timedelta(seconds=400)
    assert (k.credit, k.via, k.pos_x, k.pos_y, k.pos_z) == ("kill", "direct", 1.0, 2.0, 3.0)


def test_players_and_player_missions() -> None:
    save(basic_result())

    p1 = Player.objects.get(account_uuid=account(1))
    assert (p1.current_name, p1.name_lower) == ("Player-1", "player-1")
    assert (p1.sorties, p1.kills_air, p1.kills_ground, p1.flight_time_s) == (1, 1, 1, 900.0)
    p2 = Player.objects.get(account_uuid=account(2))
    assert (p2.planes_lost, p2.bailouts, p2.deaths) == (1, 1, 0)

    # The gunner gets a PlayerMission row (took part) but no counted sorties (FR-WEB-14).
    gunner = PlayerMission.objects.get(player__account_uuid=account(3))
    assert (gunner.sorties, gunner.coalition) == (0, 1)
    assert PlayerMission.objects.count() == 3
    assert list(PlayerName.objects.filter(player=p1).values_list("name", flat=True)) == ["Player-1"]


def test_unknown_object_types_are_auto_registered() -> None:
    result = basic_result()
    save(replace(result, object_types_seen=result.object_types_seen | {"Brand-New Jet"}))

    new = GameObject.objects.get(log_name="Brand-New Jet")
    assert (new.is_known, new.cls, new.display_name) == (False, "unknown", "Brand-New Jet")
    sabre = GameObject.objects.get(log_name="F-86A-5")
    assert (sabre.is_known, sabre.cls, sabre.display_name, sabre.is_playable) == (True, "fighter", "F-86A Sabre", True)
    # Kill victim types are registered even when not in object_types_seen.
    assert GameObject.objects.filter(log_name="M46 Patton", cls="tank").exists()


def test_game_object_admin_names_survive_and_unknowns_get_fixed_by_catalog_updates() -> None:
    GameObject.objects.create(log_name="MiG-15bis", display_name="Admin MiG", cls="fighter")
    GameObject.objects.create(log_name="F-86A-5", display_name="F-86A-5", cls="unknown", is_known=False)

    save(basic_result())

    assert GameObject.objects.get(log_name="MiG-15bis").display_name == "Admin MiG"
    sabre = GameObject.objects.get(log_name="F-86A-5")
    assert (sabre.display_name, sabre.cls, sabre.is_known) == ("F-86A Sabre", "fighter", True)


def test_countries_created_with_coalition_names_and_not_overwritten() -> None:
    Country.objects.create(code=601, coalition=2, display_name="UN forces")
    save(basic_result())

    assert Country.objects.get(code=501).display_name == "REDFOR"
    assert Country.objects.get(code=601).display_name == "UN forces"


def test_ingesting_twice_gives_identical_rows() -> None:
    """FR-ING-6: idempotent, including PKs and level-2 totals."""
    save(basic_result())
    before = {t.__name__: rows(t) for t in ALL_TABLES}

    save(basic_result())

    assert {t.__name__: rows(t) for t in ALL_TABLES} == before


def test_reingest_keeps_pks_and_deletes_removed_sorties() -> None:
    """FR-ING-9 / FR-WEB-13: rows that still exist keep their PK, removed ones are deleted."""
    first = basic_result()
    save(first)
    pks = {s.account_uuid: s.pk for s in PlayerSortie.objects.all()}

    # Late parts: player 2's sortie disappears (say a rule fix), player 1's changes, player 4 is new.
    changed = replace(first.sorties[0], kills_air=0, outcome="crashed", landing_tick=None, landings=0)
    sorties = reindexed((changed, first.sorties[2], sortie(3, 4, coalition=2, aircraft_type="F-51D")))
    save(mission(sorties, ()))

    after = {s.account_uuid: s for s in PlayerSortie.objects.all()}
    assert set(after) == {account(1), account(3), account(4)}
    assert after[account(1)].pk == pks[account(1)]
    assert after[account(3)].pk == pks[account(3)]
    assert after[account(1)].outcome == "crashed"
    assert Kill.objects.count() == 0
    assert Mission.objects.count() == 1
    # Level 2 reflects only the new state.
    p1 = Player.objects.get(account_uuid=account(1))
    assert (p1.sorties, p1.kills_air, p1.landings) == (1, 0, 0)
    p2 = Player.objects.get(account_uuid=account(2))  # kept (URL stays), but contributes nothing now
    assert (p2.sorties, p2.planes_lost, p2.flight_time_s) == (0, 0, 0.0)
    assert not PlayerMission.objects.filter(player=p2).exists()
    assert not PlayerAircraft.objects.filter(player=p2).exists()


def test_failure_mid_save_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-ING-5: the caller's transaction makes a mission all-or-nothing, also on re-ingest."""
    save(basic_result())
    before = {t.__name__: rows(t) for t in ALL_TABLES}

    def boom(*_args: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(persist, "_replace_kills", boom)
    changed = mission(reindexed((sortie(0, 1, kills_air=5), sortie(1, 9))))
    with pytest.raises(RuntimeError, match="disk full"), transaction.atomic():
        persist.save_mission(changed, meta(), FakeCatalog())

    assert {t.__name__: rows(t) for t in ALL_TABLES} == before


def test_new_mission_failure_leaves_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(persist, "add_mission", boom)
    with pytest.raises(RuntimeError), transaction.atomic():
        persist.save_mission(basic_result(), meta(), FakeCatalog())

    assert all(not t._default_manager.exists() for t in ALL_TABLES)


# --- DB constraints (doc 08) ---


def _sortie_row(**overrides: object) -> PlayerSortie:
    save(mission((sortie(0, 1),)))
    s = PlayerSortie.objects.get()
    for field, value in overrides.items():
        setattr(s, field, value)
    s.pk = None
    s.spawn_tick = 999_999
    return s


@pytest.mark.parametrize(
    "overrides",
    [
        {"damage_taken": 1.5},
        {"damage_taken": -0.1},
        {"pilot_fate": "teleported"},
        {"outcome": "exploded"},
        {"role": "copilot"},
        {"loss_cause": "gremlins"},
        {"flight_time_s": -1.0},
    ],
)
def test_sortie_constraints_reject_bad_values(overrides: dict[str, object]) -> None:
    row = _sortie_row(**overrides)
    with pytest.raises(IntegrityError), transaction.atomic():
        row.save()


def test_sortie_natural_key_is_unique() -> None:
    row = _sortie_row()
    row.spawn_tick = PlayerSortie.objects.get().spawn_tick
    with pytest.raises(IntegrityError), transaction.atomic():
        row.save()


def test_kill_constraint_rejects_bad_credit() -> None:
    save(basic_result())
    k = Kill.objects.get()
    k.pk = None
    k.credit = "half"
    with pytest.raises(IntegrityError), transaction.atomic():
        k.save()
