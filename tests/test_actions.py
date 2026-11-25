"""The step after approval: the approved action is recorded, once."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent import graph
from agent.actions import action_for, record_action
from agent.graph import act_node, build_graph
from fakes import fake_analyzer, fake_classifier, fake_synthesizer, issue

THREAD = {"configurable": {"thread_id": "thread-1"}}


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(graph, "classifier", fake_classifier("stuck"))
    monkeypatch.setattr(graph, "analyzer", fake_analyzer(True))
    monkeypatch.setattr(
        graph, "synthesizer", fake_synthesizer("escalate_environment", "The lock is stale. Escalate.")
    )


def decide(answer, **issue_changes):
    app = build_graph(InMemorySaver())
    app.invoke({"issue": issue(**issue_changes)}, THREAD)
    return app.invoke(Command(resume=answer), THREAD)


# --- recording: a second attempt for the same run changes nothing ---


def test_the_first_record_for_a_run_is_kept_and_a_second_is_refused():
    assert record_action("t1", "SUB-1", "escalate_environment", "approved") is True
    assert record_action("t1", "SUB-1", "wait_and_watch", "edited") is False
    kept = action_for("t1")
    assert (kept["submission_id"], kept["action"], kept["review"]) == (
        "SUB-1",
        "escalate_environment",
        "approved",
    )


def test_a_second_run_on_the_same_submission_gets_a_record_of_its_own():
    assert record_action("t1", "SUB-1", "escalate_environment", "approved") is True
    assert record_action("t2", "SUB-1", "wait_and_watch", "approved") is True


def test_nothing_is_recorded_for_a_run_nobody_decided():
    assert action_for("no-such-run") is None


# --- in the graph: only an approval or an edit has an effect ---


def test_approving_records_the_agent_s_action_against_the_submission(fakes):
    result = decide({"decision": "approve"})
    assert result["action_recorded"] is True
    assert action_for("thread-1")["action"] == "escalate_environment"


def test_editing_records_the_reviewer_s_action_not_the_agent_s(fakes):
    decide({"decision": "edit", "action": "wait_and_watch"})
    kept = action_for("thread-1")
    assert (kept["action"], kept["review"]) == ("wait_and_watch", "edited")


def test_rejecting_records_nothing(fakes):
    result = decide({"decision": "reject"})
    assert result["action_recorded"] is False
    assert action_for("thread-1") is None


def test_nothing_is_recorded_when_there_was_nothing_to_approve(fakes):
    app = build_graph(InMemorySaver())
    result = app.invoke({"issue": issue(gate_status="passed")}, THREAD)
    assert result["action_recorded"] is False
    assert action_for("thread-1") is None


# --- exactly once: the step may run twice, the effect may not ---


def test_running_the_step_again_for_the_same_run_does_not_record_twice(fakes):
    # A saved run guarantees a step runs at least once. If the process died
    # after the record was written and before the step was saved, the step
    # would run again on resume. The second run must change nothing.
    state = {"issue": issue(), "review": "approved", "final_action": "escalate_environment"}
    assert act_node(state, THREAD) == {"action_recorded": True}
    assert act_node(state, THREAD) == {"action_recorded": False}
    assert action_for("thread-1")["action"] == "escalate_environment"


def test_the_evaluated_graph_has_no_approval_and_records_nothing(fakes):
    result = build_graph().invoke({"issue": issue()})
    assert "action_recorded" not in result
