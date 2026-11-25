"""Checks on the case files themselves: the format, and that every label agrees
with the policy line it names and with the facts written into the case.

Run from the repo root with:  uv run pytest
"""

import copy

import pytest

from agent.domain import ALLOWED_ACTIONS
from agent.policy import MIN_HOURS, OUTAGE_THRESHOLD, POLICY, SLOW_FACTOR
from store.cases import (
    DEV_FILE,
    PROBE_FILE,
    RECORD_FIELDS,
    REGRESSION_FILE,
    STORE_DEFAULTS,
    TEST_FILE,
    case_problems,
    cases_with_history,
    read_cases,
)
from store.make_sources import TYPICAL_HOURS

# The cases written for the evaluation: each has a label and names the policy
# line behind it.
SCORED = read_cases(DEV_FILE) + read_cases(TEST_FILE)
PRODUCTION = read_cases(REGRESSION_FILE, optional=True)

NOT_BROKEN_STATUSES = ("passed", "skipped", "withdrawn")


def failing_peers(case):
    return sum(peer["status"] == "failed" for peer in case["store"].get("peers", []))


def stuck_peers(case):
    return sum(
        peer["status"] == "running" and peer["hours_ago"] >= MIN_HOURS
        for peer in case["store"].get("peers", [])
    )


def open_stage_tickets(case):
    return [
        ticket
        for ticket in case["store"].get("tickets", [])
        if ticket["on"] == "stage" and ticket["state"] == "open"
    ]


def has_a_reason_to_be_held(case):
    return bool(case["store"].get("hold_note")) or case["store"].get("dependency_landed") is False


# --- the format ---


def test_there_are_scored_cases():
    assert SCORED


def test_a_missing_case_file_is_an_error_unless_it_is_allowed_to_be_missing(tmp_path):
    # A wrong folder or a renamed file must not read as "no cases".
    with pytest.raises(FileNotFoundError, match="no-such-file"):
        read_cases(tmp_path / "no-such-file.jsonl")
    assert read_cases(tmp_path / "no-such-file.jsonl", optional=True) == []


def test_no_case_has_a_mistake_in_its_format():
    for case in cases_with_history() + PRODUCTION:
        assert case_problems(case) == [], case["id"]


def broken(change):
    """A copy of the first case with one thing changed by `change`."""
    case = copy.deepcopy(SCORED[0])
    case["store"].setdefault("peers", [])
    case["store"].setdefault("tickets", [])
    change(case)
    return case_problems(case)


def test_the_format_check_names_each_kind_of_mistake():
    # Each of these would otherwise be replaced by a default without a word,
    # and the store would then contradict what the case's author meant.
    mistakes = {
        "an extra field in the record": lambda c: c["input"].update(ticket_text="x"),
        "a misspelled key in the history": lambda c: c["store"].update(peer=[]),
        "a state that is not active or withdrawn": lambda c: c["store"].update(state="withdrawen"),
        "a dependency flag that is not true, false or null": lambda c: c["store"].update(dependency_landed=1),
        "a peer status outside the three known": lambda c: c["store"]["peers"].append({"status": "fail"}),
        "a misspelled key in a peer": lambda c: c["store"]["peers"].append({"status": "failed", "hours": 3}),
        "a running peer with no age": lambda c: c["store"]["peers"].append({"status": "running"}),
        "a ticket that is neither on the submission nor the stage": lambda c: c["store"]["tickets"].append(
            {"on": "Stage ", "state": "open", "text": "x"}
        ),
        "a ticket state that is not open or closed": lambda c: c["store"]["tickets"].append(
            {"on": "stage", "state": "opened", "text": "x"}
        ),
        "an earlier run that is not older than the record's": lambda c: c["store"].update(
            earlier_runs=[
                {"status": "passed", "event": "job_passed", "hours_ago": c["input"]["hours_in_status"]}
            ]
        ),
    }
    for name, change in mistakes.items():
        assert broken(change), name


def test_ids_and_submission_ids_are_unique_across_every_file():
    cases = cases_with_history()
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["input"]["submission_id"] for case in cases}) == len(cases)


def test_the_record_has_exactly_the_six_fields_the_gate_reports():
    # Nothing extra can reach the agent: no part of the label, and nothing that
    # belongs in the store.
    for case in cases_with_history() + PRODUCTION:
        assert set(case["input"]) == set(RECORD_FIELDS), case["id"]


def test_a_case_s_history_uses_only_the_known_keys():
    # A misspelled key would be ignored without a word, and the case would get
    # the default instead of what its author meant.
    for case in cases_with_history():
        assert set(case["store"]) <= set(STORE_DEFAULTS), case["id"]


def test_a_case_s_earlier_runs_are_older_than_the_run_in_the_record():
    # The store finds a submission's latest run by its age. An earlier run
    # that was no older than the current one would be taken for the latest.
    for case in cases_with_history():
        for run in case["store"].get("earlier_runs", []):
            assert run["hours_ago"] > case["input"]["hours_in_status"], case["id"]


def test_every_log_is_distinct():
    logs = [case["input"]["log_excerpt"] for case in SCORED + read_cases(PROBE_FILE)]
    assert len(set(logs)) == len(logs)


# --- the labels against the policy ---


def test_every_label_is_the_class_and_action_of_the_policy_line_it_names():
    for case in SCORED:
        line = POLICY[case["rule"]]
        assert case["label"] == {"true_class": line.triage_class, "acceptable_actions": [line.action]}, case[
            "id"
        ]


def test_an_outage_label_means_enough_failing_peers_and_only_then():
    for case in SCORED:
        if case["rule"] == "outage":
            assert failing_peers(case) >= OUTAGE_THRESHOLD, case["id"]
        elif case["rule"] != "not_broken":
            assert failing_peers(case) < OUTAGE_THRESHOLD, case["id"]


def test_the_cases_sit_on_both_sides_of_the_outage_threshold():
    # One case exactly on the threshold and one just under it, so ">= 3" and
    # "> 3" give different answers on the data.
    counts = {failing_peers(case) for case in SCORED}
    assert {OUTAGE_THRESHOLD - 1, OUTAGE_THRESHOLD} <= counts


def test_a_not_broken_label_means_the_status_or_the_store_says_so():
    for case in SCORED:
        not_broken = (
            case["input"]["gate_status"] in NOT_BROKEN_STATUSES or case["store"].get("state") == "withdrawn"
        )
        assert (case["rule"] == "not_broken") == not_broken, case["id"]


def test_the_stuck_labels_agree_with_the_hours():
    for case in SCORED:
        hours = case["input"]["hours_in_status"]
        if case["rule"] == "too_early":
            assert hours < MIN_HOURS, case["id"]
        if case["rule"] in ("held_for_reason", "stuck_alone_and_slow", "stuck_wait"):
            assert hours >= MIN_HOURS, case["id"]
        if case["rule"] == "stuck_alone_and_slow":
            assert hours > SLOW_FACTOR * TYPICAL_HOURS[case["input"]["stage"]], case["id"]


def test_a_held_label_has_a_reason_written_in_the_case_and_stuck_wait_has_none():
    for case in SCORED:
        if case["rule"] == "held_for_reason":
            assert has_a_reason_to_be_held(case), case["id"]
        if case["rule"] in ("stuck_alone_and_slow", "stuck_wait"):
            assert not has_a_reason_to_be_held(case), case["id"]


def test_alone_and_slow_means_both_and_stuck_wait_means_not_both():
    for case in SCORED:
        hours, stage = case["input"]["hours_in_status"], case["input"]["stage"]
        alone_and_slow = stuck_peers(case) == 0 and hours > SLOW_FACTOR * TYPICAL_HOURS[stage]
        if case["rule"] == "stuck_alone_and_slow":
            assert alone_and_slow, case["id"]
        if case["rule"] == "stuck_wait":
            assert not alone_and_slow, case["id"]


def test_a_ticket_open_label_means_an_open_stage_ticket():
    # Whether an open ticket is about the same failure takes reading, so only
    # the half that can be counted is checked here.
    for case in SCORED:
        if case["rule"] == "stage_tool_ticket_open":
            assert open_stage_tickets(case), case["id"]


def test_fix_or_new_version_agrees_with_the_commits():
    for case in SCORED:
        commits = case["store"].get("commits_since_failure", 0)
        if case["rule"] == "test_failed_new_version":
            assert commits > 0, case["id"]
        if case["rule"] == "test_failed_fix":
            assert commits == 0, case["id"]


def test_a_pair_is_two_cases_with_different_policy_lines():
    # Within one file. The development set and the held-out set each have
    # their own pair for the same condition.
    for path in (DEV_FILE, TEST_FILE):
        pairs = {}
        for case in read_cases(path):
            if "pair" in case:
                pairs.setdefault(case["pair"], []).append(case["rule"])
        for name, rules in pairs.items():
            assert len(rules) == 2 and rules[0] != rules[1], (path.name, name)


# --- cases added from production ---


def test_a_case_from_production_has_a_valid_label_and_a_submission_in_the_store():
    in_store = {case["input"]["submission_id"] for case in cases_with_history()}
    for case in PRODUCTION:
        label = case["label"]
        assert set(label["acceptable_actions"]) <= set(ALLOWED_ACTIONS[label["true_class"]])
        assert case["input"]["submission_id"] in in_store, case["id"]


# --- what the development set must contain ---

DEV = read_cases(DEV_FILE)


def test_the_development_set_uses_every_policy_line():
    assert {case["rule"] for case in DEV} == set(POLICY)


def test_the_development_set_has_every_pair_on_the_operator_s_list():
    assert {case["pair"] for case in DEV if "pair" in case} == {
        "peers-2-and-3",
        "hours-7-and-9",
        "held-or-not",
        "typical-time",
        "progress-or-never-picked-up",
        "failed-test-or-errored-job",
        "new-commits",
        "stage-ticket",
        "passed-now",
        "withdrawn-or-active",
    }


def test_every_class_has_at_least_four_development_cases():
    for triage_class in ALLOWED_ACTIONS:
        count = sum(case["label"]["true_class"] == triage_class for case in DEV)
        assert count >= 4, triage_class


def test_an_early_stage_failure_is_not_always_the_stage_s_tooling():
    # Where the failure happened does not say what went wrong. The set holds
    # early-stage failures caused by the code and by the machine as well.
    early = {
        case["label"]["true_class"] for case in DEV if case["input"]["stage"] in ("build", "static_checks")
    }
    assert {"stage_tooling_failure", "code_defect", "environment_error"} <= early


def test_the_planted_traps_are_in_the_development_set():
    logs = " ".join(case["input"]["log_excerpt"] for case in DEV)
    tickets = " ".join(ticket["text"] for case in DEV for ticket in case["store"].get("tickets", []))
    assert "NOTE TO AUTOMATED TRIAGE" in logs  # a log that gives orders
    assert "cancel it and resubmit" in tickets  # a submitter asking to cancel


def test_a_probe_has_no_answer():
    probes = read_cases(PROBE_FILE)
    assert len(probes) == 4
    for probe in probes:
        assert set(probe) == {"id", "input", "store"}, probe["id"]
