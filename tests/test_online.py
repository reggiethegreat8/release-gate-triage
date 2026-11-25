"""The online evaluator, the review feedback, and adding a case."""

import json

import pytest

from evaluation.add_case import make_label
from evaluation.evaluators import human_review_feedback
from evaluation.online_evaluator import perform_eval
from store.cases import REGRESSION_FILE

# --- the online evaluator: gets the run as a dict ---


def run(guard_notes):
    return {"inputs": {"issue": {"gate_status": "failed"}}, "outputs": {"guard_notes": guard_notes}}


def test_a_run_no_guard_touched_scores_true():
    assert perform_eval(run([])) == {"guards_quiet": True}


def test_a_run_a_guard_corrected_scores_false():
    note = "forced: 4 others failing, so escalate and do not rerun"
    assert perform_eval(run([note])) == {"guards_quiet": False}


def test_a_run_that_errored_gets_no_score_rather_than_a_clean_one():
    assert perform_eval({"inputs": {"issue": {}}, "outputs": {}}) == {}
    assert perform_eval({"inputs": {"issue": {}}, "outputs": None}) == {}


def test_the_resume_trace_of_a_paused_run_is_not_scored_a_second_time():
    resumed = {"inputs": {"input": None}, "outputs": {"guard_notes": []}}
    assert perform_eval(resumed) == {}


# --- the second online score: did every model step work? ---


def health_run(degraded_reasons):
    outputs = {"guard_notes": [], "degraded_reasons": degraded_reasons}
    return {"inputs": {"issue": {"gate_status": "failed"}}, "outputs": outputs}


def test_a_run_whose_model_steps_all_worked_scores_healthy():
    assert perform_eval(health_run([])) == {"guards_quiet": True, "not_degraded": True}


def test_a_run_that_fell_back_scores_unhealthy_even_though_no_guard_stepped_in():
    scores = perform_eval(health_run(["classify: APIConnectionError: connection error"]))
    assert scores == {"guards_quiet": True, "not_degraded": False}


def test_a_run_without_the_health_field_gets_no_health_score():
    # Not counted as healthy: the field is missing, so nothing is known.
    assert "not_degraded" not in perform_eval(run([]))


# --- the reviewer's decision as feedback ---


def test_approve_scores_one():
    feedback = human_review_feedback({"decision": "approve"})
    assert feedback == {"key": "human_review", "score": 1, "value": "approve", "comment": None}


def test_edit_scores_zero_and_keeps_the_reviewer_s_choice():
    feedback = human_review_feedback({"decision": "edit", "action": "escalate_environment"})
    assert feedback["score"] == 0
    assert feedback["value"] == "edit"
    assert feedback["comment"] == "reviewer chose escalate_environment"


def test_reject_scores_zero():
    assert human_review_feedback({"decision": "reject"})["score"] == 0


# --- the operator's label for a case added from production ---


def test_a_valid_label_is_accepted():
    label = make_label("environment_error", ["escalate_environment"])
    assert label == {"true_class": "environment_error", "acceptable_actions": ["escalate_environment"]}


def test_a_label_outside_the_fixed_answer_set_is_refused():
    with pytest.raises(ValueError):
        make_label("flaky_test", ["fix_then_rerun"])
    with pytest.raises(ValueError):
        make_label("stuck", ["fix_then_rerun"])
    with pytest.raises(ValueError):
        make_label("stuck", [])


def test_every_case_added_from_production_carries_a_valid_label():
    for line in REGRESSION_FILE.read_text().splitlines():
        label = json.loads(line)["label"]
        assert make_label(label["true_class"], label["acceptable_actions"]) == label
