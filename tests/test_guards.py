"""The guards: plain code that runs after the model and has the last word."""

from agent.guards import apply_guards, guard_allowlist, guard_forbidden_advice, guard_forced_rules
from fakes import context

# --- guard 1: the action must be on the class's allowed list ---


def test_an_allowed_action_is_left_alone():
    assert guard_allowlist("stuck", "escalate_environment") == ("escalate_environment", None)


def test_an_action_from_another_class_is_replaced_by_the_default():
    action, note = guard_allowlist("stuck", "rerun_same_version")
    assert action == "wait_and_watch"
    assert note == "allowlist: rerun_same_version is not allowed for stuck"


# --- guard 2: the three forced rules ---


def test_nothing_wrong_always_means_none():
    action, note = guard_forced_rules("nothing_wrong", "wait_and_watch", context())
    assert action == "none"
    assert note == "forced: nothing_wrong always means none"


def test_an_environment_error_during_an_outage_is_escalated():
    action, note = guard_forced_rules(
        "environment_error", "rerun_same_version", context(outage=True, peers_failing=3)
    )
    assert action == "escalate_environment"
    assert note == "forced: 3 others failing, so escalate and do not rerun"


def test_an_environment_error_below_the_threshold_may_be_rerun():
    result = guard_forced_rules(
        "environment_error", "rerun_same_version", context(outage=False, peers_failing=2)
    )
    assert result == ("rerun_same_version", None)


def test_the_outage_rule_only_applies_to_environment_errors():
    result = guard_forced_rules("code_defect", "fix_then_rerun", context(outage=True, peers_failing=5))
    assert result == ("fix_then_rerun", None)


def test_a_withdrawn_submission_is_never_rerun_or_given_any_other_action():
    # Someone pulled it on purpose. Whatever class and action reached here,
    # the answer is to do nothing.
    action, note = guard_forced_rules(
        "environment_error", "rerun_same_version", context(submission_state="withdrawn")
    )
    assert action == "none"
    assert note == "forced: the submission was withdrawn, so no action"


def test_a_forced_rule_that_changes_nothing_leaves_no_note():
    result = guard_forced_rules(
        "environment_error", "escalate_environment", context(outage=True, peers_failing=5)
    )
    assert result == ("escalate_environment", None)


# --- guard 3: no advice to cancel or withdraw ---


def test_a_clean_conclusion_is_left_alone():
    text = "The runner lost its connection. Escalate to the environment owner."
    assert guard_forbidden_advice(text, "stuck", "escalate_environment") == (text, None)


def test_advice_to_cancel_is_withheld():
    text, note = guard_forbidden_advice(
        "It has hung for 52 hours. Cancel the submission and resubmit.", "stuck", "escalate_environment"
    )
    assert "cancel" not in text.lower()
    assert "stuck" in text and "escalate_environment" in text
    assert note == "forbidden advice: the conclusion mentioned cancelling or withdrawing"


def test_advice_to_withdraw_is_withheld():
    _, note = guard_forbidden_advice("Ask the submitter to withdraw it.", "stuck", "wait_and_watch")
    assert note is not None


def test_the_scan_cannot_tell_do_not_cancel_from_cancel():
    # A known cost: this conclusion is good advice, and it is withheld anyway.
    # A word scan errs on the safe side. Telling the two apart needs a model,
    # which is what the judge is for.
    _, note = guard_forbidden_advice(
        "Escalate the stale lock. Do not cancel the submission.", "stuck", "escalate_environment"
    )
    assert note is not None


# --- the three together, in order ---


def test_the_forced_rule_wins_over_the_allowlist_default():
    # A wrong-class action during an outage. Guard 1 swaps in the default,
    # "rerun_same_version". Guard 2 then forces the escalation.
    action, _, notes = apply_guards(
        "environment_error", "fix_then_rerun", "The registry is down.", context(outage=True, peers_failing=3)
    )
    assert action == "escalate_environment"
    assert notes == [
        "allowlist: fix_then_rerun is not allowed for environment_error",
        "forced: 3 others failing, so escalate and do not rerun",
    ]


def test_the_withheld_notice_names_the_action_after_the_forced_rule_changed_it():
    action, conclusion, _ = apply_guards(
        "environment_error", "rerun_same_version", "Cancel it.", context(outage=True, peers_failing=3)
    )
    assert action == "escalate_environment"
    assert "escalate_environment" in conclusion and "rerun_same_version" not in conclusion
