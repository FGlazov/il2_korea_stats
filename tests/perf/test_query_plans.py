"""Query plans of the level-2 reads of the player roll-up (doc 14 "Level 2 is per tour"): a tour refresh reads the
tours' level-1 rows and the players' tour rows, so none of those reads may scan a whole table.

Over the seeded world of `tests.perf.seed` (a few thousand sorties). SQLite prints `SCAN <table>` for a full scan
(`SEARCH ... USING INDEX` for an indexed read), Postgres `Seq Scan on <table>`: both are caught (a covering-index scan
of a whole index counts, too). A small table may be scanned legitimately (`il2ks_db_tour`, `il2ks_db_mission` of one
tour): only the tables that grow with the history are listed in `GROWING`."""

import re
from typing import Protocol

import pytest
from django.db import connection
from django.db.models import Q
from tests.perf.seed import SeededWorld

from il2ks.db.models import (
    KillCredit,
    PlayerAircraftBuild,
    PlayerKillboard,
    PlayerMission,
    PlayerSortie,
    PlayerTour,
    PlayerTourAircraft,
    PlayerTourKillboard,
    PlayerTourName,
    PlayerTourPool,
    PlayerTypeKillboard,
    Tour,
)
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.identity import tour_sorties
from il2ks.ingest.pairs import pair_kills
from il2ks.ingest.type_board import type_kills

pytestmark = pytest.mark.django_db


class Explainable(Protocol):
    def explain(self) -> str: ...


GROWING = (
    "il2ks_db_playersortie",
    "il2ks_db_playermission",
    "il2ks_db_kill",
    "il2ks_db_playertour",
    "il2ks_db_playertouraircraft",
    "il2ks_db_playertourpool",
    "il2ks_db_playertourkillboard",
    "il2ks_db_playerkillboard",
    "il2ks_db_playertypekillboard",
    "il2ks_db_playertourname",
    "il2ks_db_playeraircraftbuild",
)
SCAN = re.compile(r"(?:\bSCAN(?: TABLE)?\s+|Seq Scan on\s+)(\w+)", re.IGNORECASE)


def full_scans(queryset: Explainable) -> list[str]:
    """The growing tables the plan of this query reads without an index."""
    plan = queryset.explain()
    return sorted({m.group(1) for line in plan.splitlines() if (m := SCAN.search(line)) and m.group(1) in GROWING})


def test_the_seeded_world_has_tours(big_world: SeededWorld) -> None:
    assert Tour.objects.count() >= 1


def reads(big_world: SeededWorld) -> dict[str, Explainable]:
    chunk = big_world.player_pks[:40]
    tours = sorted(Tour.objects.values_list("pk", flat=True))[:1]
    touching = Q(player_id__in=chunk) | Q(opponent_id__in=chunk)
    return {
        # the per-tour step: the tours' level-1 rows of these players
        "tour sorties of the players": counted_sorties().filter(player_id__in=chunk, mission__tour_id__in=tours),
        "tour player missions": PlayerMission.objects.filter(player_id__in=chunk, mission__tour_id__in=tours),
        "tour sorties, all roles (identity)": tour_sorties(chunk, tours),
        "tour kills (pairs)": pair_kills(chunk, [KillCredit.KILL], set(tours)),
        "tour kills (types)": type_kills(chunk, set(tours)),
        # the roll-up: the players' tour rows, and the all-time rows they are written to
        "PlayerTour": PlayerTour.objects.filter(player_id__in=chunk).order_by("tour_id"),
        "PlayerTourAircraft": PlayerTourAircraft.objects.filter(player_id__in=chunk).order_by("tour_id"),
        "PlayerTourPool": PlayerTourPool.objects.filter(player_id__in=chunk).order_by("tour_id"),
        "PlayerTourName": PlayerTourName.objects.filter(player_id__in=chunk).order_by("tour_id"),
        "PlayerAircraftBuild tour rows": PlayerAircraftBuild.objects.filter(
            player_id__in=chunk, tour_id__isnull=False
        ).order_by("tour_id"),
        "PlayerAircraftBuild all-time rows": PlayerAircraftBuild.objects.filter(
            player_id__in=chunk, tour_id__isnull=True
        ),
        "PlayerTourKillboard": PlayerTourKillboard.objects.filter(touching).order_by("tour_id"),
        "PlayerKillboard": PlayerKillboard.objects.filter(touching),
        "PlayerTypeKillboard tour rows": PlayerTypeKillboard.objects.filter(
            player_id__in=chunk, tour_id__isnull=False
        ).order_by("tour_id"),
        "PlayerTypeKillboard all-time rows": PlayerTypeKillboard.objects.filter(
            player_id__in=chunk, tour_id__isnull=True
        ),
    }


def test_no_level_2_read_of_the_roll_up_scans_a_growing_table(big_world: SeededWorld) -> None:
    scans = {name: found for name, qs in reads(big_world).items() if (found := full_scans(qs))}
    assert scans == {}, f"full scans on {connection.vendor}"


def test_the_guard_sees_a_full_scan() -> None:
    """The check itself works: a read by an unindexed column is a scan."""
    assert full_scans(PlayerSortie.objects.filter(damage_taken=1.0)) == ["il2ks_db_playersortie"]
