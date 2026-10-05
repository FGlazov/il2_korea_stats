"""Who won the mission (AType 8 objective results, doc 12 "Mission result"; maintainer request 2026-10-05).

Korea reports TYPE 0 once per reporting coalition just before the mission end. Measured over 210 real missions: only
coalition 1 RES 1 (47), only coalition 2 RES 1 (84), both RES 1 (22), both RES 0 (52), no objective event (5)."""

from tests.unit.replay.builder import Scenario


def test_the_only_coalition_with_a_completed_objective_won() -> None:
    sc = Scenario()
    sc.objective(100, 2, 1)
    sc.mission_end(101)
    info = sc.result().mission
    assert (info.result, info.winning_coalition) == ("win", 2)


def test_both_coalitions_completing_it_is_a_draw_not_a_win_for_the_first_event() -> None:
    sc = Scenario()
    sc.objective(100, 1, 1)
    sc.objective(100, 2, 1)
    sc.mission_end(101)
    info = sc.result().mission
    assert (info.result, info.winning_coalition) == ("draw", None)


def test_nobody_completing_it_is_a_draw() -> None:
    sc = Scenario()
    sc.objective(100, 1, 0)
    sc.objective(100, 2, 0)
    sc.mission_end(101)
    info = sc.result().mission
    assert (info.result, info.winning_coalition) == ("draw", None)


def test_a_failed_objective_of_one_side_next_to_the_win_of_the_other() -> None:
    sc = Scenario()
    sc.objective(100, 1, 0)
    sc.objective(100, 2, 1)
    sc.mission_end(101)
    assert sc.result().mission.winning_coalition == 2


def test_no_objective_event_leaves_the_result_unknown() -> None:
    sc = Scenario()
    sc.mission_end(101)
    info = sc.result().mission
    assert (info.result, info.winning_coalition) == ("unknown", None)


def test_other_objective_types_and_neutral_reports_do_not_decide() -> None:
    sc = Scenario()
    sc.objective(100, 1, 1, objective_type=1)
    sc.objective(100, 0, 1)
    sc.mission_end(101)
    info = sc.result().mission
    assert (info.result, info.winning_coalition) == ("unknown", None)
