"""The rule chain: one test per rule, then tests that pin the order."""

from agent.domain import ALLOWED_ACTIONS
from agent.rules import CODE_DECIDED, classify_by_rules
from fakes import context
from store.cases import cases_with_history


def issue(**changes):
    """A plain failed late-stage issue. Each test changes only what it is about."""
    base = {
        "gate_status": "failed",
        "stage": "integration_tests",
        "job_events": ["job_started"],
    }
    return base | changes


def rules(issue_changes=None, **context_changes):
    return classify_by_rules(issue(**(issue_changes or {})), context(**context_changes))


# --- one test per rule ---


def test_a_gate_that_passed_was_skipped_or_was_withdrawn_means_nothing_wrong():
    for status in ("passed", "skipped", "withdrawn"):
        assert rules({"gate_status": status}) == ("nothing_wrong", "not_broken")


def test_a_withdrawn_submission_means_nothing_wrong_whatever_the_gate_still_shows():
    # The gate can show a stale "running" after the owner withdrew the submission.
    result = rules({"gate_status": "running"}, submission_state="withdrawn")
    assert result == ("nothing_wrong", "not_broken")


def test_an_outage_means_environment_error():
    assert rules(outage=True) == ("environment_error", "outage")


def test_a_run_that_is_still_running_means_stuck():
    # The gate only raises an issue about a running run when it is not finishing.
    assert rules({"gate_status": "running"}) == ("stuck", "still_running")


def test_a_failed_setup_or_an_errored_job_means_environment_error():
    # Either way the tests never ran, so it cannot be the submitted code.
    for event in ("env_setup_failed", "job_errored"):
        assert rules({"job_events": ["job_started", event]}) == ("environment_error", "tests_never_ran")


def test_an_early_stage_failure_is_guessed_to_be_the_stage_s_own_tooling():
    assert rules({"stage": "build"}) == ("stage_tooling_failure", "in_early_stage")


def test_anything_else_is_a_code_defect():
    assert rules() == ("code_defect", "no_other_rule_matched")


# --- the order: first match wins ---


def test_not_broken_beats_an_outage():
    # The operator's first question is "is it still broken?". A passed gate on
    # a line that is having an outage needs no action.
    assert rules({"gate_status": "passed"}, outage=True) == ("nothing_wrong", "not_broken")


def test_an_outage_beats_an_early_stage():
    assert rules({"stage": "build"}, outage=True) == ("environment_error", "outage")


def test_an_outage_beats_a_run_that_is_still_running():
    assert rules({"gate_status": "running"}, outage=True) == ("environment_error", "outage")


def test_still_running_beats_an_early_stage():
    # A hung build is stuck. Where it hung does not make it a tooling failure.
    assert rules({"gate_status": "running", "stage": "build"}) == ("stuck", "still_running")


def test_still_running_beats_an_errored_job():
    # A run the gate still shows as running is stuck, whatever its events say.
    result = rules({"gate_status": "running", "job_events": ["job_started", "job_errored"]})
    assert result == ("stuck", "still_running")


def test_tests_never_ran_beats_an_early_stage():
    # A build whose machine failed is an environment error, not a tooling failure.
    result = rules({"stage": "build", "job_events": ["job_started", "env_setup_failed"]})
    assert result == ("environment_error", "tests_never_ran")


# --- which answers the model may not change ---


def test_the_two_rules_decided_by_code_are_the_ones_with_no_exceptions():
    assert CODE_DECIDED == ("not_broken", "outage")


# --- the rules must always answer, because they are the fallback ---


def test_every_case_gets_a_class_from_the_fixed_set():
    for case in cases_with_history():
        triage_class, rule_name = classify_by_rules(case["input"], context())
        assert triage_class in ALLOWED_ACTIONS
        assert rule_name
