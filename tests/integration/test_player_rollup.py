"""The all-time player rows are a roll-up of the per-tour rows (doc 14, maintainer requirement 2026-10-05): after every
step of a messy history they equal the old level-1 computation (`tests.level1_oracle`) and the SUM / MAX / MIN of the
tour rows, and a rebuild changes nothing. Tours are monthly (the default): Sep, Oct, Nov 2026.

The steps: missions in three tours, a player who skips a tour, a gunner-only player, a name change, a late import into
an old tour, a re-ingest that moves a mission to another tour, and one that drops a player."""

from collections import defaultdict
from datetime import UTC, datetime

import pytest

from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.core.replay.result import CombatRole, KillResult, Role, SortieResult
from il2ks.db.models import (
    Kill,
    Mission,
    Player,
    PlayerAircraft,
    PlayerAircraftBuild,
    PlayerKillboard,
    PlayerPool,
    PlayerRole,
    PlayerSortie,
    PlayerTour,
    PlayerTourAircraft,
    PlayerTourKillboard,
    PlayerTourPool,
    PlayerTypeKillboard,
    Tour,
)
from il2ks.ingest.aggregates import rebuild_aggregates, refresh_tours
from il2ks.ingest.counters import COUNTER_FIELDS
from tests import level1_oracle as oracle
from tests.db_canon import canonical_dump, diff_dumps
from tests.factories import kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

SEP = datetime(2026, 9, 5, 12, tzinfo=UTC)
LATE_SEP = datetime(2026, 9, 20, 12, tzinfo=UTC)
OCT = datetime(2026, 10, 5, 12, tzinfo=UTC)
NOV = datetime(2026, 11, 5, 12, tzinfo=UTC)
LATE_NOV = datetime(2026, 11, 20, 12, tzinfo=UTC)


def _approx(a: object, b: object) -> bool:
    return round(float(a), 3) == round(float(b), 3)  # type: ignore[arg-type]


def sum_of_tour_rows() -> list[str]:
    """The counters, builds and killboards re-added from the per-tour rows (independently of the oracle)."""
    out: list[str] = []
    totals: dict[int, dict[str, float]] = defaultdict(lambda: dict.fromkeys(COUNTER_FIELDS, 0.0))
    for row in PlayerTour.objects.order_by("tour_id"):
        for name in COUNTER_FIELDS:
            totals[row.player_id][name] += getattr(row, name)
    for player in Player.objects.all():
        for name in COUNTER_FIELDS:
            if not _approx(getattr(player, name), totals[player.pk][name]):
                out.append(f"Player {player.pk}.{name}: {getattr(player, name)} != tour sum {totals[player.pk][name]}")
    by_aircraft: dict[tuple[int, int], int] = defaultdict(int)
    for tour_aircraft in PlayerTourAircraft.objects.all():
        by_aircraft[(tour_aircraft.player_id, tour_aircraft.aircraft_id)] += tour_aircraft.sorties
    stored = {(r.player_id, r.aircraft_id): r.sorties for r in PlayerAircraft.objects.all()}
    if stored != dict(by_aircraft):
        out.append(f"PlayerAircraft sorties {stored} != tour sum {dict(by_aircraft)}")
    by_pool: dict[tuple[int, str], int] = defaultdict(int)
    for tour_pool in PlayerTourPool.objects.all():
        by_pool[(tour_pool.player_id, tour_pool.propulsion)] += tour_pool.sorties
    if {(r.player_id, r.propulsion): r.sorties for r in PlayerPool.objects.all()} != dict(by_pool):
        out.append("PlayerPool sorties differ from the sum of PlayerTourPool")
    by_role: dict[tuple[int, str], int] = defaultdict(int)
    for tour_role in PlayerRole.objects.filter(tour__isnull=False):
        by_role[(tour_role.player_id, tour_role.role)] += tour_role.sorties
    if {(r.player_id, r.role): r.sorties for r in PlayerRole.objects.filter(tour__isnull=True)} != dict(by_role):
        out.append("all-time PlayerRole sorties differ from the sum of the tour PlayerRole rows")
    builds: dict[tuple[int, int, int, str], int] = defaultdict(int)
    for build in PlayerAircraftBuild.objects.filter(tour__isnull=False):
        builds[(build.player_id, build.aircraft_id, build.value, build.label)] += build.sorties
    if oracle.stored_builds() != dict(builds):
        out.append("PlayerAircraftBuild all-time differs from the sum of its tour rows")
    pairs: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    for board in PlayerTourKillboard.objects.all():
        pairs[(board.player_id, board.opponent_id)][0] += board.kills
        pairs[(board.player_id, board.opponent_id)][1] += board.deaths
    if {(r.player_id, r.opponent_id): [r.kills, r.deaths] for r in PlayerKillboard.objects.all()} != dict(pairs):
        out.append("PlayerKillboard differs from the sum of PlayerTourKillboard")
    types: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    for row in PlayerTypeKillboard.objects.filter(tour__isnull=False):
        types[(row.player_id, row.enemy_aircraft_id)][0] += row.kills
        types[(row.player_id, row.enemy_aircraft_id)][1] += row.deaths
    stored_types = {
        (r.player_id, r.enemy_aircraft_id): [r.kills, r.deaths] for r in PlayerTypeKillboard.objects.filter(tour=None)
    }
    if stored_types != dict(types):
        out.append("PlayerTypeKillboard all-time differs from the sum of its tour rows")
    return out


def check(step: str) -> None:
    assert oracle.diff_all_time() == [], step
    assert sum_of_tour_rows() == [], step


class Builder:
    """A mission of sorties and kills; every duel is two fresh sorties (one kill credit per sortie pair)."""

    def __init__(self) -> None:
        self.sorties: list[SortieResult] = []
        self.kills: list[KillResult] = []

    def fly(
        self,
        player: int,
        aircraft_type: str,
        *,
        name: str | None = None,
        flight_time_s: float = 600.0,
        role: Role = "pilot",
        combat_role: CombatRole | None = None,
        kills_ground: int = 0,
        kills_air: int = 0,
        is_death: bool = False,
    ) -> int:
        self.sorties.append(
            sortie(
                len(self.sorties),
                player,
                aircraft_type=aircraft_type,
                name=name,
                flight_time_s=flight_time_s,
                role=role,
                combat_role=combat_role,
                kills_ground=kills_ground,
                kills_air=kills_air,
                kills_air_pvp=kills_air,
                is_death=is_death,
                is_plane_lost=is_death,
            )
        )
        return len(self.sorties) - 1

    def duel(
        self, killer: int, killer_type: str, victim: int, victim_type: str, *, killer_name: str | None = None
    ) -> None:
        k = self.fly(
            killer,
            killer_type,
            name=killer_name,
            flight_time_s=100.1 + len(self.sorties) / 7,
            kills_air=1,
        )
        v = self.fly(victim, victim_type, flight_time_s=50.05 + len(self.sorties) / 3, is_death=True)
        self.kills.append(kill(5000 + 100 * len(self.kills), k, v, victim_type=victim_type, killer_type=killer_type))

    def save(self, uid: str, when: datetime) -> None:
        save(mission(tuple(self.sorties), tuple(self.kills)), meta(uid, when))


MIG, F86, F51, IL10 = "MiG-15bis", "F-86A-5", "F-51D", "IL-10"


def m_sep(*, with_p3: bool = True, p2_name: str | None = None) -> None:
    b = Builder()
    b.duel(1, MIG, 2, F86)
    b.duel(1, MIG, 2, F86)
    b.duel(2, F86, 1, MIG)
    if with_p3:
        b.duel(3, F51, 2, F86)
    b.fly(2, F86, name=p2_name, flight_time_s=12.3)
    b.save("sep-1", SEP)


def m_oct(when: datetime = OCT) -> None:
    b = Builder()
    b.duel(1, F86, 2, MIG, killer_name="Ace")
    b.duel(1, F86, 2, MIG, killer_name="Ace")
    b.duel(2, MIG, 1, F86, killer_name="Dueller")
    b.fly(4, IL10, flight_time_s=0.2, combat_role="attack", kills_ground=2)
    b.fly(6, "Turret_IL10", role="gunner", flight_time_s=50.0, name="Gunner")
    b.save("oct-1", when)


def m_nov() -> None:
    b = Builder()
    for _ in range(3):
        b.duel(1, F86, 2, MIG, killer_name="Ace")
    b.duel(5, MIG, 1, F86)
    b.duel(2, MIG, 1, F86)
    b.save("nov-1", NOV)


def m_late_sep() -> None:
    b = Builder()
    b.duel(5, MIG, 1, MIG)
    b.save("sep-2", LATE_SEP)


def test_all_time_rows_follow_the_tour_rows_through_a_messy_history() -> None:
    m_sep()
    check("one mission")
    m_oct()
    m_nov()
    check("three tours; player 5 skips October; player 6 only flies a gunner")
    assert Player.objects.get(account_uuid__endswith="000000000006").sorties == 0  # no pilot sortie, still identified

    m_late_sep()
    check("late import into the oldest tour (player 5 now has September and November)")

    m_oct(LATE_NOV)
    check("a re-ingest moves the October mission to November")

    m_sep(with_p3=False, p2_name="Renamed")
    check("a re-ingest drops player 3 from September and renames player 2")
    assert Mission.objects.count() == 4

    with_incremental = canonical_dump()
    rebuild_aggregates()
    # the activity day of the moved mission is re-derived within the touched tours' time ranges (`refresh_tours`)
    assert diff_dumps(with_incremental, canonical_dump()) == []
    check("after a rebuild")


def _history() -> None:
    m_sep()
    m_oct()
    m_nov()
    m_late_sep()


def test_a_tour_refresh_never_reads_the_level_1_rows_of_other_tours() -> None:
    """The all-time rows are the roll-up of the tour rows: with the September level 1 gone (deleted behind the back of
    level 2), refreshing November still keeps September in every all-time row, because it only sums the tour rows."""
    _history()
    before = canonical_dump()
    september = Tour.objects.get(title="September 2026")
    november = Tour.objects.get(title="November 2026")
    PlayerSortie.objects.filter(mission__tour=september).delete()
    Kill.objects.filter(mission__tour=september).delete()

    refresh_tours([november.pk], DEFAULT_RULES)

    after = canonical_dump()
    for table in ("Player", "PlayerAircraft", "PlayerPool", "PlayerAircraftBuild", "PlayerKillboard", "PlayerName"):
        assert diff_dumps({table: before[table]}, {table: after[table]}) == [], table
    assert (
        diff_dumps(
            {"PlayerTypeKillboard": before["PlayerTypeKillboard"]},
            {"PlayerTypeKillboard": after["PlayerTypeKillboard"]},
        )
        == []
    )


def test_rebuild_gives_every_mission_a_tour_whatever_rules_it_is_called_with() -> None:
    """The invariant behind the roll-up: every mission has a tour, or its sorties are in no per-tour row."""
    _history()
    before = canonical_dump()
    Mission.objects.update(tour=None)
    PlayerSortie.objects.update(tour=None)  # the sorties had no tour either
    PlayerTour.objects.all().delete()

    rebuild_aggregates()  # no [tours] rules given: the defaults apply

    assert not Mission.objects.filter(tour__isnull=True).exists()
    assert diff_dumps(before, canonical_dump()) == []
    check("after a rebuild without tours")
