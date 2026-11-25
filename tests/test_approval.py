"""Human approval and durability, with fake model steps and no network."""

import sqlite3

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agent import graph
from agent.graph import Analysis, answer_problem, build_graph
from fakes import Fake, fake_analyzer, fake_classifier, fake_synthesizer, issue

THREAD = {"configurable": {"thread_id": "thread-1"}}


@pytest.fixture
def fakes(monkeypatch):
    """A well-behaved fake model: stuck, conclusive, escalate."""
    steps = [
        fake_classifier("stuck"),
        fake_analyzer(True),
        fake_synthesizer("escalate_environment", "The lock is stale. Escalate."),
    ]
    monkeypatch.setattr(graph, "classifier", steps[0])
    monkeypatch.setattr(graph, "analyzer", steps[1])
    monkeypatch.setattr(graph, "synthesizer", steps[2])
    return steps


def start(app, **issue_changes):
    return app.invoke({"issue": issue(**issue_changes)}, THREAD)


# --- the pause ---


def test_the_run_pauses_and_shows_the_reviewer_the_proposal(fakes):
    app = build_graph(InMemorySaver())
    result = start(app)
    assert "final_action" not in result  # nothing is decided yet
    shown = result["__interrupt__"][0].value
    assert shown == {
        "submission_id": "SUB-1",
        "triage_class": "stuck",
        "action": "escalate_environment",
        "conclusion": "The lock is stale. Escalate.",
        "allowed_actions": ["wait_and_watch", "escalate_environment"],
    }


def test_a_paused_run_is_waiting_at_the_approval_node(fakes):
    app = build_graph(InMemorySaver())
    start(app)
    snapshot = app.get_state(THREAD)
    assert snapshot.next == ("approval",)
    assert len(snapshot.interrupts) == 1


# --- the three decisions ---


def test_approve_keeps_the_agent_s_action(fakes):
    app = build_graph(InMemorySaver())
    start(app)
    result = app.invoke(Command(resume={"decision": "approve"}), THREAD)
    assert result["review"] == "approved"
    assert result["final_action"] == "escalate_environment"
    assert app.get_state(THREAD).next == ()  # the run is finished


def test_edit_uses_the_reviewer_s_action_and_keeps_the_agent_s_for_the_record(fakes):
    app = build_graph(InMemorySaver())
    start(app)
    result = app.invoke(Command(resume={"decision": "edit", "action": "wait_and_watch"}), THREAD)
    assert result["review"] == "edited"
    assert result["final_action"] == "wait_and_watch"
    assert result["action"] == "escalate_environment"


def test_reject_means_no_action_is_taken(fakes):
    app = build_graph(InMemorySaver())
    start(app)
    result = app.invoke(Command(resume={"decision": "reject"}), THREAD)
    assert result["review"] == "rejected"
    assert result["final_action"] is None


def test_answers_are_checked_before_resuming():
    allowed = ["wait_and_watch", "escalate_environment"]
    assert answer_problem(allowed, {"decision": "approve"}) is None
    assert answer_problem(allowed, {"decision": "reject"}) is None
    assert answer_problem(allowed, {"decision": "edit", "action": "wait_and_watch"}) is None
    assert answer_problem(allowed, {"decision": "edit", "action": "fix_then_rerun"})
    assert answer_problem(allowed, {"decision": "edit"})
    assert answer_problem(allowed, {"decision": "maybe"})
    assert answer_problem(allowed, "approve")  # not even the right shape


def test_an_unusable_answer_that_gets_through_counts_as_a_rejection(fakes):
    # The node must not raise after the interrupt: the saved answer would be
    # replayed on every later resume. So a bad edit ends the run with no action.
    app = build_graph(InMemorySaver())
    start(app)
    result = app.invoke(Command(resume={"decision": "edit", "action": "fix_then_rerun"}), THREAD)
    assert result["review"] == "rejected"
    assert result["final_action"] is None


def test_an_answer_of_the_wrong_shape_is_a_rejection_too(fakes):
    app = build_graph(InMemorySaver())
    start(app)
    result = app.invoke(Command(resume="approve"), THREAD)
    assert result["review"] == "rejected"


def test_a_recovered_gate_needs_no_approval(fakes):
    app = build_graph(InMemorySaver())
    result = start(app, gate_status="passed")
    assert "__interrupt__" not in result
    assert result["review"] == "not_needed"
    assert result["final_action"] == "none"


# --- without a checkpointer there is no approval step at all ---


def test_the_graph_without_a_checkpointer_ends_after_the_guards(fakes):
    result = build_graph().invoke({"issue": issue()})
    assert result["action"] == "escalate_environment"
    assert "review" not in result


# --- durability ---


def sqlite_app(path):
    """A fresh connection and a fresh graph, as a new process would have."""
    return build_graph(SqliteSaver(sqlite3.connect(path, check_same_thread=False)))


def test_a_paused_run_survives_into_a_new_process(fakes, tmp_path):
    db = tmp_path / "checkpoints.db"
    start(sqlite_app(db))  # "process one" pauses and is thrown away

    app = sqlite_app(db)  # "process two" knows only the file and the thread id
    assert app.get_state(THREAD).values["action"] == "escalate_environment"
    result = app.invoke(Command(resume={"decision": "approve"}), THREAD)
    assert result["final_action"] == "escalate_environment"


def test_resuming_does_not_run_the_finished_steps_again(fakes, tmp_path):
    db = tmp_path / "checkpoints.db"
    start(sqlite_app(db))
    sqlite_app(db).invoke(Command(resume={"decision": "approve"}), THREAD)
    assert [step.calls for step in fakes] == [1, 1, 1]  # each model step ran once, ever


class ProcessKilled(BaseException):
    """Stands in for kill -9: not an Exception, so no fallback catches it."""


def test_a_run_killed_mid_way_carries_on_from_the_last_saved_step(fakes, monkeypatch, tmp_path):
    db = tmp_path / "checkpoints.db"
    # The analyze step dies the first time it is called, and works the second.
    analyzer = Fake(ProcessKilled(), Analysis(finding="a finding", conclusive=True))
    monkeypatch.setattr(graph, "analyzer", analyzer)

    with pytest.raises(ProcessKilled):
        start(sqlite_app(db))
    assert sqlite_app(db).get_state(THREAD).next == ("analyze",)

    # A new process, given no new input, carries on from analyze.
    result = sqlite_app(db).invoke(None, THREAD)
    assert result["__interrupt__"][0].value["action"] == "escalate_environment"
    classifier, _, synthesizer = fakes
    assert classifier.calls == 1  # classify was not run again
    assert analyzer.calls == 2  # analyze was: the step in progress is repeated
    assert synthesizer.calls == 1
