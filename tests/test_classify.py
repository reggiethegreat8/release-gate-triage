"""The deterministic parts of the classify step. No real model call is made."""

import pytest
from pydantic import ValidationError

from agent import graph
from agent.graph import ClassChoice, classify_node
from fakes import broken, context, fake_classifier, issue


def state(rule_class="code_defect", rule_name="no_other_rule_matched"):
    return {"issue": issue(), "context": context(), "rule_class": rule_class, "rule_name": rule_name}


# --- the schema ---


def test_the_schema_accepts_each_of_the_five_classes():
    for name in ["code_defect", "stuck", "environment_error", "stage_tooling_failure", "nothing_wrong"]:
        assert ClassChoice(triage_class=name, reason="x").triage_class == name


def test_the_schema_rejects_a_class_outside_the_fixed_set():
    with pytest.raises(ValidationError):
        ClassChoice(triage_class="flaky_test", reason="x")


# --- the classify step ---


def test_the_model_can_override_the_rules(monkeypatch):
    monkeypatch.setattr(graph, "classifier", fake_classifier("environment_error"))
    assert classify_node(state(rule_class="code_defect")) == {
        "triage_class": "environment_error",
        "model_class": "environment_error",
    }


def test_the_rules_take_over_when_the_model_call_fails(monkeypatch):
    monkeypatch.setattr(graph, "classifier", broken())
    result = classify_node(state(rule_class="stuck"))
    assert result == {
        "triage_class": "stuck",
        "degraded_reasons": ["classify: TimeoutError: model unavailable"],
    }


def test_a_nothing_wrong_answer_from_the_model_is_rejected_but_recorded(monkeypatch):
    # The rules' class is used. What the model said is kept beside it, so a
    # result can never look as if the model agreed with the rules when it did not.
    monkeypatch.setattr(graph, "classifier", fake_classifier("nothing_wrong"))
    assert classify_node(state(rule_class="code_defect")) == {
        "triage_class": "code_defect",
        "model_class": "nothing_wrong",
    }


def test_no_model_answer_is_recorded_when_the_model_failed_or_was_not_asked(monkeypatch):
    monkeypatch.setattr(graph, "classifier", broken())
    assert "model_class" not in classify_node(state(rule_class="stuck"))
    assert "model_class" not in classify_node(state(rule_class="nothing_wrong", rule_name="not_broken"))


def test_the_model_is_never_asked_when_the_rules_say_nothing_wrong(monkeypatch):
    classifier = broken()
    monkeypatch.setattr(graph, "classifier", classifier)
    result = classify_node(state(rule_class="nothing_wrong", rule_name="not_broken"))
    assert result == {"triage_class": "nothing_wrong"}
    assert classifier.calls == 0


def test_the_model_is_never_asked_for_the_class_during_an_outage(monkeypatch):
    # An outage is decided from a count, in code. A log that reads like a real
    # test failure does not get to change it.
    classifier = fake_classifier("code_defect")
    monkeypatch.setattr(graph, "classifier", classifier)
    result = classify_node(state(rule_class="environment_error", rule_name="outage"))
    assert result == {"triage_class": "environment_error"}
    assert classifier.calls == 0


# --- the variant that hides the rules' pick from the model ---


def test_by_default_the_model_is_shown_the_rules_pick(monkeypatch):
    classifier = fake_classifier("stuck")
    monkeypatch.setattr(graph, "classifier", classifier)
    classify_node(state(rule_class="stuck", rule_name="still_running"))
    assert "A rule-based check already picked: stuck" in classifier.prompts[0]


def test_without_the_hint_the_model_is_not_shown_the_rules_pick(monkeypatch):
    classifier = fake_classifier("stuck")
    monkeypatch.setattr(graph, "classifier", classifier)
    classify_node(state(rule_class="stuck", rule_name="still_running"), rules_hint=False)
    assert "rule-based check" not in classifier.prompts[0]


def test_without_the_hint_the_rules_are_still_the_fallback(monkeypatch):
    monkeypatch.setattr(graph, "classifier", broken())
    result = classify_node(state(rule_class="stuck", rule_name="still_running"), rules_hint=False)
    assert result["triage_class"] == "stuck"
