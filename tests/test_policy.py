"""The operator's action policy: one table that the prompt, the case labels and
the checks on the cases all read."""

from agent.domain import ALLOWED_ACTIONS
from agent.policy import MIN_HOURS, OUTAGE_THRESHOLD, POLICY, SLOW_FACTOR, policy_text
from agent.prompts import synthesize_prompt
from fakes import context, issue


def test_every_line_gives_an_action_its_class_allows():
    for line_id, line in POLICY.items():
        assert line.action in ALLOWED_ACTIONS[line.triage_class], line_id


def test_every_class_and_every_action_has_a_line():
    # A class or an action with no policy line could never be a case's label.
    assert {line.triage_class for line in POLICY.values()} == set(ALLOWED_ACTIONS)
    every_action = {action for actions in ALLOWED_ACTIONS.values() for action in actions}
    assert {line.action for line in POLICY.values()} == every_action


def test_the_text_for_a_class_holds_that_class_s_lines_and_no_others():
    text = policy_text("stuck")
    for line_id, line in POLICY.items():
        assert (line.text in text) == (line.triage_class == "stuck"), line_id


def test_each_line_in_the_text_names_its_action():
    text = policy_text("code_defect")
    assert "Action: cut_new_version" in text
    assert "Action: fix_then_rerun" in text


def test_the_numbers_in_the_text_come_from_the_constants():
    # If a threshold changes, the sentence the model reads changes with it.
    assert f"{OUTAGE_THRESHOLD} or more" in POLICY["outage"].text
    assert f"{MIN_HOURS} hours" in POLICY["too_early"].text
    assert f"{SLOW_FACTOR} times" in POLICY["stuck_alone_and_slow"].text


def test_the_synthesize_prompt_carries_the_policy_for_its_class():
    prompt = synthesize_prompt(issue(), context(), "stuck", "a finding")
    assert policy_text("stuck") in prompt
