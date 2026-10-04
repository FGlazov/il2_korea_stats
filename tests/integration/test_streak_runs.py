"""The history of a player's streaks (FR-WEB-25, OQ-82) and the assists-received detail of the killboard (OQ-81):
ingest rules, tours, incremental == rebuild, the page, hiding, budgets."""

import pytest
from django.test import Client

from il2ks.core.replay.result import SortieResult
from il2ks.db.models import Mission, Player, PlayerKillboard, PlayerSortie, PlayerStreakRun, PlayerTourKillboard
from il2ks.ingest.aggregates import rebuild_aggregates
from tests.factories import STARTED_AT, meta, mission, save, sortie
from tests.integration.test_killboard_streaks import duel_mission, pk, snapshot
from tests.integration.test_killboard_tours import ASSISTS_ON, OCTOBER, seed_tours, tour_named
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db


def died(index: int, player: int = 1) -> SortieResult:
    return sortie(index, player, is_death=True, outcome="shot_down")


def runs_of(number: int, tour: str | None = None) -> list[tuple[int, int, str]]:
    """(sorties, air kills, ended by) of the player's runs, oldest first."""
    rows = PlayerStreakRun.objects.filter(player=pk(number), tour=tour_named(tour) if tour else None).order_by("since")
    return [(r.sorties, r.kills_air, r.ended_by) for r in rows]


def two_runs() -> None:
    save(
        mission(
            (
                sortie(0, 1, kills_air=1),
                sortie(1, 1),
                died(2),  # ends the first run (2 sorties)
                sortie(3, 1),  # a run of one: not listed
                died(4),
                sortie(5, 1, kills_air=2),
                sortie(6, 1, outcome="not_taken_off", flight_time_s=0.0),  # neutral
                sortie(7, 1),
                sortie(8, 1),  # still going: 3 sorties
            )
        ),
        meta("m1", STARTED_AT),
    )


def test_every_run_of_two_or_more_is_stored_with_what_ended_it() -> None:
    two_runs()

    assert runs_of(1) == [(2, 1, "death"), (3, 2, "open")]
    first = PlayerStreakRun.objects.filter(player=pk(1), tour=None).order_by("since").first()
    assert first is not None
    assert first.ended_sortie is not None
    assert first.ended_sortie.is_death
    assert PlayerStreakRun.objects.get(player=pk(1), tour=None, ended_by="open").ended_sortie is None


def test_runs_are_kept_per_tour_and_a_run_does_not_span_tours_there() -> None:
    save(mission((sortie(0, 1), sortie(1, 1))), meta("m1", STARTED_AT))  # September: 2
    save(mission((sortie(0, 1),)), meta("m2", OCTOBER))  # October: 1

    assert runs_of(1) == [(3, 0, "open")]
    assert runs_of(1, "September 2026") == [(2, 0, "open")]
    assert runs_of(1, "October 2026") == []  # one sortie is below the minimum


def test_runs_incremental_equals_rebuild() -> None:
    two_runs()
    save(mission((sortie(0, 1), died(1), sortie(2, 1), sortie(3, 1))), meta("m2", OCTOBER))
    incremental = snapshot()
    assert PlayerStreakRun.objects.count() >= 4

    rebuild_aggregates()

    assert snapshot() == incremental


def test_reprocessing_a_mission_updates_the_runs() -> None:
    two_runs()
    save(mission((sortie(0, 1), sortie(1, 1))), meta("m1", STARTED_AT))  # same mission, fewer sorties

    assert runs_of(1) == [(2, 0, "open")]


def test_streak_runs_page_lists_newest_first_and_links_the_fatal_sortie(client: Client) -> None:
    two_runs()

    response = client.get(f"/players/{pk(1)}/streaks/history/?tour=all")

    assert response.status_code == 200
    rows = list(response.context["page_obj"])
    assert [(r.sorties, r.ended_by) for r in rows] == [(3, "open"), (2, "death")]
    html = response.content.decode()
    assert "All streaks of Player-1" in html
    assert "Still going" in html
    fatal = PlayerSortie.objects.get(pk=rows[1].ended_sortie_id)
    assert f'href="/sorties/{fatal.pk}/"' in html
    assert f"/players/{pk(1)}/streaks/?tour=all" in html  # back to the best streaks


def test_streak_runs_page_is_paginated_by_20_and_filtered_by_tour(client: Client) -> None:
    save(
        mission(tuple(s for n in range(21) for s in (sortie(3 * n, 1), sortie(3 * n + 1, 1), died(3 * n + 2)))),
        meta("m1", STARTED_AT),
    )
    save(mission((sortie(0, 1), sortie(1, 1))), meta("m2", OCTOBER))

    first = client.get(f"/players/{pk(1)}/streaks/history/?tour=all")
    second = client.get(f"/players/{pk(1)}/streaks/history/?tour=all&page=2")

    assert len(first.context["page_obj"]) == 20
    assert first.context["page_obj"].paginator.count == 22
    assert len(second.context["page_obj"]) == 2
    in_tour = client.get(f"/players/{pk(1)}/streaks/history/?tour={tour_named('October 2026').pk}")
    assert in_tour.context["tour"] == tour_named("October 2026")
    assert [r.sorties for r in in_tour.context["page_obj"]] == [2]


def test_streak_runs_page_of_a_hidden_or_unknown_player_is_404_and_a_hidden_mission_is_not_linked(
    client: Client,
) -> None:
    two_runs()
    fatal = PlayerStreakRun.objects.get(player=pk(1), tour=None, ended_by="death").ended_sortie_id
    Mission.objects.update(is_hidden=True)

    html = client.get(f"/players/{pk(1)}/streaks/history/?tour=all").content.decode()
    assert f"/sorties/{fatal}/" not in html
    Player.objects.filter(pk=pk(1)).update(is_hidden=True)
    assert client.get(f"/players/{pk(1)}/streaks/history/").status_code == 404
    assert client.get("/players/999999/streaks/history/").status_code == 404


def test_the_sortie_list_and_the_best_streaks_page_link_to_the_history(client: Client) -> None:
    two_runs()

    assert (
        f"/players/{pk(1)}/streaks/history/?tour=all"
        in client.get(f"/players/{pk(1)}/sorties/?tour=all").content.decode()
    )
    assert (
        f"/players/{pk(1)}/streaks/history/?tour=all"
        in client.get(f"/players/{pk(1)}/streaks/?tour=all").content.decode()
    )


def test_streak_runs_budget(client: Client) -> None:
    seed_tours()
    two_runs()
    october = tour_named("October 2026").pk
    # context processor 2, player, tours, count, rows
    assert_simple_reads(client, f"/players/{pk(1)}/streaks/history/", max_queries=6)
    assert_simple_reads(client, f"/players/{pk(1)}/streaks/history/?tour={october}", max_queries=6)


# --- OQ-81: assists received ---
def test_assists_received_are_the_mirror_of_assists_and_per_tour() -> None:
    rebuild_aggregates(board=ASSISTS_ON)
    save(duel_mission(), meta("m1", STARTED_AT))

    mine = PlayerKillboard.objects.get(player=pk(3), opponent=pk(2))
    theirs = PlayerKillboard.objects.get(player=pk(2), opponent=pk(3))
    assert (mine.assists, mine.assists_received) == (1, 0)
    assert (theirs.assists, theirs.assists_received) == (0, 1)
    tour_row = PlayerTourKillboard.objects.get(player=pk(2), opponent=pk(3))
    assert tour_row.assists_received == 1
    assert not PlayerKillboard.objects.filter(player=pk(1), assists_received__gt=0).exists()


def test_assists_received_show_as_a_detail_only_with_the_toggle(client: Client) -> None:
    rebuild_aggregates(board=ASSISTS_ON)
    save(duel_mission(), meta("m1", STARTED_AT))

    html = client.get(f"/players/{pk(2)}/killboard/?tour=all").content.decode()
    assert "Assisted on your losses: 1" in html
    assert "Assisted on your losses" not in client.get(f"/players/{pk(3)}/killboard/?tour=all").content.decode()

    rebuild_aggregates()  # toggle off again
    assert "Assisted on your losses" not in client.get(f"/players/{pk(2)}/killboard/?tour=all").content.decode()
