"""The code evaluators. Each takes plain dicts and returns True or False.

tool_calls_valid is tested in test_tool_agent.py.
"""

from evaluation.evaluators import (
    action_allowed,
    action_match,
    class_match,
    guards_quiet,
    no_forbidden_advice,
    not_degraded,
)
from fakes import context


def outputs(**changes):
    base = {
        "triage_class": "stuck",
        "action": "wait_and_watch",
        "conclusion": "Watch it.",
        "guard_notes": [],
        "context": context(),
    }
    return base | changes


OUTAGE = context(outage=True, peers_failing=5)


# --- class_match: did the agent's class equal the operator's? ---


def test_class_match_passes_when_the_classes_agree():
    assert class_match(outputs(), {"true_class": "stuck"}) is True


def test_class_match_fails_when_they_differ():
    assert class_match(outputs(), {"true_class": "environment_error"}) is False


# --- action_allowed: a safety check, needs no label ---


def test_an_action_on_the_class_s_list_is_allowed():
    assert action_allowed(outputs()) is True


def test_an_action_from_another_class_is_not_allowed():
    assert action_allowed(outputs(action="fix_then_rerun")) is False


def test_a_rerun_during_an_outage_is_not_allowed():
    rerun = {"triage_class": "environment_error", "action": "rerun_same_version"}
    assert action_allowed(outputs(**rerun)) is True
    assert action_allowed(outputs(**rerun, context=OUTAGE)) is False


def test_an_escalation_during_an_outage_is_allowed():
    escalate = outputs(triage_class="environment_error", action="escalate_environment", context=OUTAGE)
    assert action_allowed(escalate) is True


def test_nothing_wrong_allows_only_none():
    assert action_allowed(outputs(triage_class="nothing_wrong", action="none")) is True
    assert action_allowed(outputs(triage_class="nothing_wrong", action="wait_and_watch")) is False


def test_a_withdrawn_submission_allows_only_none():
    withdrawn = context(submission_state="withdrawn")
    rerun = outputs(triage_class="environment_error", action="rerun_same_version", context=withdrawn)
    assert action_allowed(rerun) is False
    assert action_allowed(outputs(triage_class="nothing_wrong", action="none", context=withdrawn)) is True


# --- no_forbidden_advice: a safety check on the conclusion ---


def test_a_clean_conclusion_passes():
    assert no_forbidden_advice(outputs()) is True


def test_a_conclusion_that_mentions_cancelling_or_withdrawing_fails():
    assert no_forbidden_advice(outputs(conclusion="Cancel it and resubmit.")) is False
    assert no_forbidden_advice(outputs(conclusion="The submitter should WITHDRAW it.")) is False


def test_an_outage_does_not_restrict_other_classes():
    waiting = outputs(triage_class="stuck", action="wait_and_watch", context=OUTAGE)
    assert action_allowed(waiting) is True


def test_the_word_check_matches_inside_longer_words():
    # Plain substring matching. Erring toward a false alarm is the safe side.
    assert no_forbidden_advice(outputs(conclusion="The job was cancelled by the scheduler.")) is False


# --- guards_quiet: did the model's answer need correcting? ---


def test_guards_quiet_passes_when_no_guard_changed_anything():
    assert guards_quiet(outputs()) is True


def test_guards_quiet_fails_when_a_guard_stepped_in():
    note = "forbidden advice: the conclusion mentioned cancelling or withdrawing"
    assert guards_quiet(outputs(guard_notes=[note])) is False


# --- not_degraded: did every model step work? ---


def test_not_degraded_passes_when_no_step_fell_back():
    assert not_degraded({"degraded_reasons": []}) is True


def test_not_degraded_fails_when_a_step_fell_back():
    assert not_degraded({"degraded_reasons": ["deep_dive: TimeoutError: down"]}) is False


# --- action_match: the primary metric. Did the agent pick the operator's action? ---


def test_action_match_passes_when_the_action_is_the_one_the_label_expects():
    label = {"true_class": "stuck", "acceptable_actions": ["wait_and_watch"]}
    assert action_match(outputs(action="wait_and_watch"), label) is True


def test_action_match_fails_on_another_action_even_one_the_class_allows():
    # escalate_environment is allowed for stuck, so action_allowed passes it.
    # The label says wait. Only this evaluator reads the label's action.
    label = {"true_class": "stuck", "acceptable_actions": ["wait_and_watch"]}
    escalate = outputs(action="escalate_environment")
    assert action_allowed(escalate) is True
    assert action_match(escalate, label) is False


def test_action_match_accepts_either_action_when_the_label_lists_two():
    label = {"true_class": "stuck", "acceptable_actions": ["wait_and_watch", "escalate_environment"]}
    assert action_match(outputs(action="escalate_environment"), label) is True


def test_action_match_is_one_of_the_evaluators_an_experiment_runs():
    from evaluation.evaluators import CODE_EVALUATORS

    assert action_match in CODE_EVALUATORS
