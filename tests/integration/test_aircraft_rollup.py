"""All-time aircraft rows are the roll-up of the tour rows (maintainer 2026-10-05, doc 14).

A tour refresh reads only that tour's level-1 rows; the all-time rows are SUM / argmax of the tour rows and never read
level 1; nothing visible changes. After every step of a history (three tours, a late import into an old tour, a
re-ingest that moves a mission to another tour, a re-ingest that drops an aircraft type and an ammo type) the stored
rows equal the OLD computation, which read the whole history (`tests/legacy_aircraft_alltime.py`, kept as the oracle),
and equal a rebuild."""

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from il2ks.core.replay.result import SingleAttackerKill
from il2ks.db.models import (
    AircraftAmmoMixStats,
    AircraftAmmoStats,
    AircraftMatchup,
    AircraftMods,
    AircraftPayload,
    AircraftStats,
    GameObject,
    PlayerAircraft,
    PlayerAircraftScope,
    Tour,
    TourAircraftStats,
)
from il2ks.ingest.aggregates import rebuild_aggregates, refresh_tours
from il2ks.ingest.aircraft_stats import (
    recompute_aircraft_tour_rows,
    recompute_matchup_tours,
    rollup_aircraft_stats,
    rollup_matchups,
)
from tests import legacy_aircraft_alltime as legacy
from tests.factories import STARTED_AT, kill, meta, mission, save, sortie

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("list_every_row")]

AIR = "air_superiority"
ATTACK = "attack"
OCTOBER = STARTED_AT + timedelta(days=40)
NOVEMBER = STARTED_AT + timedelta(days=72)
API = "BULLET_12-7_USA_API"
INC = "BULLET_12-7_USA_INC"
BASE = 1
ANTI_G = BASE | 1 << 5  # a significant MiG-15bis mod (3 significant mods: patterns exist)
NR23_ANTI_G = BASE | 1 << 1 | 1 << 5
SEPT_A = "2026-09-19_22-34-13"
OCT_B = "2026-10-29_22-00-00"
NOV_C = "2026-11-30_21-00-00"
SEPT_LATE = "2026-09-25_20-00-00"

IGNORED = {"id", "sorties_redfor", "sorties_blufor"}  # the oracle does not write the new side counters


def shot(victim_index: int | None, *hits: tuple[str, int]) -> SingleAttackerKill:
    return SingleAttackerKill("MiG-15bis", tuple(sorted(hits)), victim_index)


def mission_a(*, with_sabre: bool = True, with_inc: bool = True):  # noqa: ANN201
    sorties = (
        sortie(0, 1, kills_air=1, kills_air_pvp=1, combat_role=AIR, weapon_mods=ANTI_G, payload_id=1),
        sortie(
            1,
            2,
            combat_role=ATTACK,
            weapon_mods=NR23_ANTI_G,
            payload_id=2,
            ground_by_category={"tank": 2},
            time_on_target_s=600.0,
            is_death=True,
            is_plane_lost=True,
        ),
        sortie(2, 4, combat_role=AIR, weapon_mods=BASE, payload_id=1, is_death=True, is_plane_lost=True),
    )
    kills = ()
    if with_sabre:
        sorties += (
            sortie(
                3, 5, aircraft_type="F-86A-5", coalition=2, combat_role=AIR, kills_air=1, kills_air_pvp=1, is_death=True
            ),
        )
        kills = (
            kill(100, 0, 3, victim_type="F-86A-5"),
            kill(200, 3, 1, killer_type="F-86A-5", victim_type="MiG-15bis"),
        )
    hits_b = ((API, 5), (INC, 1)) if with_inc else ((API, 5),)
    return replace(
        mission(sorties, kills),
        single_attacker_kills=(shot(1, (API, 3)), shot(2, *hits_b), shot(None, (API, 2))),
    )


def mission_b():  # noqa: ANN201
    return replace(
        mission(
            (
                sortie(0, 1, combat_role=AIR, weapon_mods=ANTI_G, payload_id=1, is_death=True, is_plane_lost=True),
                sortie(1, 5, aircraft_type="F-86A-5", coalition=2, combat_role=AIR, kills_air=1, kills_air_pvp=1),
                sortie(2, 6, aircraft_type="F-86A-5", coalition=2, combat_role=ATTACK, time_on_target_s=100.0),
            ),
            (kill(100, 1, 0, killer_type="F-86A-5", victim_type="MiG-15bis"),),
        ),
        single_attacker_kills=(shot(0, (API, 4)),),
    )


def mission_c():  # noqa: ANN201
    return replace(
        mission(
            (
                sortie(0, 1, kills_air=2, kills_air_pvp=2, combat_role=AIR, weapon_mods=NR23_ANTI_G, payload_id=3),
                sortie(1, 4, combat_role=AIR, weapon_mods=BASE, payload_id=1),
                sortie(2, 5, aircraft_type="F-86A-5", coalition=2, combat_role=AIR, is_death=True),
                sortie(3, 7, aircraft_type="F-86A-5", coalition=2, combat_role=AIR, is_death=True),
            ),
            (
                kill(100, 0, 2, victim_type="F-86A-5"),
                kill(150, 0, 3, victim_type="F-86A-5"),
            ),
        ),
        single_attacker_kills=(shot(None, (INC, 7)),),
    )


MODELS = (
    AircraftStats,
    TourAircraftStats,
    AircraftPayload,
    AircraftMods,
    AircraftMatchup,
    PlayerAircraftScope,
    AircraftAmmoStats,
    AircraftAmmoMixStats,
)


def snapshot() -> dict[str, list[dict[str, object]]]:
    """Every aircraft-type table as sorted plain rows (no ids; floats rounded: Postgres sums in any order)."""

    def clean(row: dict[str, object]) -> dict[str, object]:
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in row.items() if k not in IGNORED}

    return {
        model.__name__: sorted(
            (clean(row) for row in model.objects.values()), key=lambda row: [str(v) for v in row.values()]
        )
        for model in MODELS
    }


def oracle_equals_stored(label: str) -> None:
    """The stored rows equal the old, history-reading computation and a rebuild; `label` names the step."""
    stored = snapshot()
    aircraft = set(GameObject.objects.values_list("pk", flat=True))
    legacy.recompute_aircraft_stats(aircraft, None)
    legacy.recompute_matchups(None)
    legacy.recompute_aircraft_ammo(aircraft)
    assert snapshot() == stored, f"{label}: the roll-up differs from the old computation"
    rebuild_aggregates()
    assert snapshot() == stored, f"{label}: incremental differs from a rebuild"


def tour_rows_sum_to_all_time() -> None:
    """The definition, checked by hand: the all-time counters are the sums of the tour rows, the side the larger of the
    summed side counters, the pilots the count of the type's `PlayerAircraft` rows."""
    for stats in AircraftStats.objects.all():
        rows = TourAircraftStats.objects.filter(
            aircraft_id=stats.aircraft_id, tour__isnull=False, role="all", mod_pattern=""
        )
        sums = rows.aggregate(
            s=models.Sum("sorties"),
            k=models.Sum("kills_air"),
            d=models.Sum("deaths"),
            red=models.Sum("sorties_redfor"),
            blue=models.Sum("sorties_blufor"),
        )
        assert (stats.sorties, stats.kills_air, stats.deaths) == (sums["s"], sums["k"], sums["d"])
        expected = "" if not (sums["red"] or sums["blue"]) else ("redfor" if sums["red"] >= sums["blue"] else "blufor")
        assert stats.side == expected
        assert stats.pilots == PlayerAircraft.objects.filter(aircraft_id=stats.aircraft_id).count()


STEPS: list[tuple[str, Callable[[], object]]] = [
    ("tour 1", lambda: save(mission_a(), meta(SEPT_A))),
    ("tour 2", lambda: save(mission_b(), meta(OCT_B, OCTOBER))),
    ("tour 3", lambda: save(mission_c(), meta(NOV_C, NOVEMBER))),
    ("late import into tour 1", lambda: save(mission_c(), meta(SEPT_LATE, STARTED_AT + timedelta(days=6)))),
    ("re-ingest moves tour 2's mission to tour 3", lambda: save(mission_b(), meta(OCT_B, NOVEMBER))),
    (
        "re-ingest drops the Sabre and an ammo type",
        lambda: save(mission_a(with_sabre=False, with_inc=False), meta(SEPT_A)),
    ),
    ("re-ingest brings them back", lambda: save(mission_a(), meta(SEPT_A))),
]


def test_all_time_rows_equal_the_old_computation_after_every_step() -> None:
    for label, step in STEPS:
        step()
        oracle_equals_stored(label)
        tour_rows_sum_to_all_time()
    assert Tour.objects.count() == 3
    stored = snapshot()
    for name in ("AircraftStats", "TourAircraftStats", "AircraftMatchup", "PlayerAircraftScope", "AircraftAmmoStats"):
        assert stored[name], f"the history leaves no {name} rows: the comparison would prove nothing"


def test_the_all_time_step_reads_no_level_one_rows() -> None:
    for _, step in STEPS[:4]:
        step()
    aircraft = list(GameObject.objects.values_list("pk", flat=True))
    pairs = list(AircraftMatchup.objects.values_list("killer_aircraft_id", "victim_aircraft_id").distinct())
    with CaptureQueriesContext(connection) as queries:
        rollup_aircraft_stats(aircraft)
        rollup_matchups(pairs)
    sql = " ".join(q["sql"].lower() for q in queries)
    for level1 in ("playersortie", "il2ks_db_kill", "missionaircraftammo", "playermission"):
        assert level1 not in sql, f"the roll-up reads {level1}"


def test_a_tour_refresh_reads_only_its_tours_level_one_rows() -> None:
    for _, step in STEPS[:4]:
        step()
    october = Tour.objects.order_by("started_at")[1]
    aircraft = list(GameObject.objects.values_list("pk", flat=True))
    pairs = list(AircraftMatchup.objects.values_list("killer_aircraft_id", "victim_aircraft_id").distinct())
    with CaptureQueriesContext(connection) as queries:
        recompute_aircraft_tour_rows(aircraft, [october.pk])
        recompute_matchup_tours(pairs, [october.pk])
    checked = 0
    for q in queries:
        sql = q["sql"].lower()
        if 'from "il2ks_db_playersortie"' in sql or 'from "il2ks_db_kill"' in sql:
            assert f'"tour_id" in ({october.pk})' in sql or f'"tour_id" = {october.pk}' in sql, sql
            checked += 1
    assert checked, "no level-1 query was captured"


def test_a_refresh_of_one_tour_leaves_the_other_tours_rows_alone() -> None:
    for _, step in STEPS[:3]:
        step()
    before = snapshot()
    refresh_tours([Tour.objects.order_by("started_at")[1].pk], None)
    assert snapshot() == before


def test_a_refresh_reads_only_its_tours_ammo_rows() -> None:
    """Hits to destroy: the types come from the touched tours' rows and only those tours' `MissionAircraftAmmo(Mix)`
    rows are summed; the all-time rows are the sum of the tour rows (never the whole ammo history again)."""
    for _, step in STEPS[:4]:
        step()
    october = Tour.objects.order_by("started_at")[1]
    with CaptureQueriesContext(connection) as queries:
        refresh_tours([october.pk], None, payload_elo=False)
    checked = 0
    for q in queries:
        sql = q["sql"].lower()
        if 'from "il2ks_db_missionaircraftammo' in sql:
            assert f'"tour_id" in ({october.pk})' in sql or f'"tour_id" = {october.pk}' in sql, sql
            checked += 1
    assert checked, "no ammo level-1 query was captured"


def test_player_scope_rollup_float_sums_do_not_depend_on_the_summing_order() -> None:
    """The all-time scope row sums the tour rows: 0.1 + 0.2 + 0.3 is 0.6000000000000001 as a float, whatever order
    the database adds in, so the float counters are rounded at write like `rollup.py` does (rebuild == incremental)."""
    from il2ks.db.models import GameObject, Player, PlayerAircraftScope, Tour
    from il2ks.ingest.aircraft_stats import _rollup_player_scopes  # pyright: ignore[reportPrivateUsage]

    aircraft = GameObject.objects.create(log_name="MiG-X", display_name="MiG-X", cls="fighter")
    player = Player.objects.create(
        account_uuid="acc-x", current_name="X", name_lower="x", first_seen=STARTED_AT, last_seen=STARTED_AT
    )
    for i, seconds in enumerate((0.1, 0.2, 0.3)):
        tour = Tour.objects.create(
            title=f"T{i}",
            started_at=STARTED_AT + timedelta(days=40 * i),
            ended_at=STARTED_AT + timedelta(days=40 * i + 30),
            mode="monthly",
        )
        PlayerAircraftScope.objects.create(
            player=player, aircraft=aircraft, tour=tour, role=AIR, mod_pattern="x", flight_time_s=seconds
        )
    _rollup_player_scopes([aircraft.pk])
    total = PlayerAircraftScope.objects.get(tour__isnull=True)
    assert total.flight_time_s == 0.6
