"""The arithmetic behind the results table. Every expected value is written
out by hand or taken from a published table, not computed with the same code."""

import pytest

from evaluation.results import by_case, conclusive_and_wrong, paired, per_class, sign_test, wilson_interval

# --- how uncertain one proportion is ---


def test_the_interval_for_a_perfect_score_on_few_cases_is_wide():
    low, high = wilson_interval(31, 31)
    assert low == pytest.approx(0.890, abs=0.001)  # 31 of 31 could be a true 89%
    assert high == 1.0


def test_the_interval_for_38_of_45():
    low, high = wilson_interval(38, 45)
    assert (low, high) == (pytest.approx(0.712, abs=0.001), pytest.approx(0.923, abs=0.001))


def test_the_interval_is_symmetric_about_a_half_for_an_even_split():
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(1 - high)
    assert low == pytest.approx(0.237, abs=0.001)


def test_no_cases_gives_no_interval():
    assert wilson_interval(0, 0) == (0.0, 1.0)


# --- is a difference between two systems on the same cases real? ---


def test_the_sign_test_ignores_cases_where_both_agree_and_counts_the_rest():
    # 11 cases where only the agent was right, none the other way: 2 * (1/2)^11.
    assert sign_test(11, 0) == pytest.approx(2 / 2048)
    # 3 against 1: P(at most 1 of 4) doubled = 2 * 5/16.
    assert sign_test(3, 1) == pytest.approx(0.625)
    # An even split, or no disagreements at all, is no evidence.
    assert sign_test(2, 2) == 1.0
    assert sign_test(0, 0) == 1.0


def test_the_sign_test_does_not_depend_on_which_side_is_named_first():
    assert sign_test(1, 3) == sign_test(3, 1)


# --- one answer per case, also when a case was run several times ---


def run(case_id, right, true_class="stuck", conclusive=True, rule="stuck_wait"):
    return {
        "case_id": case_id,
        "rule": rule,
        "conclusive": conclusive,
        "label": {"true_class": true_class},
        "scores": {"action_match": [right, None], "class_match": [right, None]},
    }


def test_a_case_run_three_times_is_right_if_most_runs_were_and_a_flip_is_noted():
    cases = by_case([run("a", True), run("a", True), run("a", False), run("b", False)], "action_match")
    assert cases["a"] == {"right": True, "runs": 3, "flipped": True}
    assert cases["b"] == {"right": False, "runs": 1, "flipped": False}


def test_a_case_whose_runs_split_evenly_is_not_counted_as_right():
    assert by_case([run("a", True), run("a", False)], "action_match")["a"]["right"] is False


def test_the_paired_table_counts_each_case_once():
    # Case a was run three times by the agent, and still counts once.
    agent = [
        run("a", True),
        run("a", True),
        run("a", False),
        run("b", True),
        run("c", False),
        run("d", False),
    ]
    rules = [run("a", True), run("b", False), run("c", True), run("d", False)]
    assert paired(agent, rules, "action_match") == {
        "both": 1,
        "first_only": 1,
        "second_only": 1,
        "neither": 1,
    }


def test_per_class_gives_right_and_total_for_each_class():
    rows = [run("a", True, "stuck"), run("b", False, "stuck"), run("c", True, "code_defect")]
    assert per_class(rows, "action_match") == {"stuck": (1, 2), "code_defect": (1, 1)}


def test_conclusive_and_wrong_lists_the_cases_the_deep_dive_never_saw():
    rows = [
        run("a", False, conclusive=True),
        run("b", False, conclusive=False),
        run("c", True, conclusive=True),
    ]
    assert conclusive_and_wrong(rows, "action_match") == ["a"]


# --- a run that errored must not crash the report or hide ---


def test_a_run_that_errored_is_counted_and_does_not_crash_the_counting():
    from evaluation.results import errored_runs

    failed = {"case_id": "b", "label": {"true_class": "stuck"}, "scores": {"action_match": [None, "boom"]}}
    rows = [run("a", True), failed]
    assert errored_runs(rows) == 1
    assert conclusive_and_wrong(rows, "action_match") == []  # no output, so not "conclusive and wrong"
    assert by_case(rows, "action_match")["b"]["right"] is False


def test_the_cases_only_one_of_two_experiments_holds_are_named():
    from evaluation.results import unshared_cases

    assert unshared_cases([run("a", True), run("b", True)], [run("a", True), run("c", True)]) == ["b", "c"]
