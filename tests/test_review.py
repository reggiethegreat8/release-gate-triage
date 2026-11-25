"""The printout a reviewer approves from. No model, no network."""

import importlib

import pytest


@pytest.fixture
def review(monkeypatch, tmp_path):
    # review.py opens its two database files when imported; keep them out of the repo.
    monkeypatch.chdir(tmp_path)
    import review as module

    return importlib.reload(module)


SHOWN = {
    "submission_id": "SUB-1",
    "triage_class": "stuck",
    "action": "wait_and_watch",
    "conclusion": "Keep watching it.",
    "allowed_actions": ["wait_and_watch", "escalate_environment"],
}


def test_a_failed_model_step_is_shown_and_not_dressed_up_as_agreement(review, capsys):
    state = {
        "rule_class": "stuck",
        "deep_dive": True,
        "tool_calls": [],
        "guard_notes": [],
        "degraded_reasons": ["classify: TimeoutError: down", "deep_dive: TimeoutError: down"],
    }
    review.show_proposal("t1", SHOWN, state)
    printed = capsys.readouterr().out
    assert "(agreed)" not in printed
    assert "looked up nothing" not in printed
    assert "a model step failed: classify: TimeoutError: down" in printed
    assert "a model step failed: deep_dive: TimeoutError: down" in printed


def test_a_class_decided_by_code_is_not_shown_as_the_model_agreeing(review, capsys):
    state = {"rule_class": "stuck", "rule_name": "outage", "guard_notes": [], "degraded_reasons": []}
    review.show_proposal("t1", SHOWN, state)
    printed = capsys.readouterr().out
    assert "(agreed)" not in printed
    assert "decided by code" in printed


def test_a_healthy_run_shows_agreement_and_its_lookups(review, capsys):
    state = {
        "rule_class": "stuck",
        "deep_dive": True,
        "tool_calls": [{"name": "list_gate_runs"}],
        "guard_notes": [],
        "degraded_reasons": [],
    }
    review.show_proposal("t1", SHOWN, state)
    printed = capsys.readouterr().out
    assert "(agreed)" in printed
    assert "looked up list_gate_runs" in printed
    assert "model step failed" not in printed


# --- deciding, and carrying on a run that stopped after the decision ---


@pytest.fixture
def paused(review, monkeypatch):
    """A run of review.py's own graph, paused for approval, with fake model
    steps and a stand-in for sending feedback. Returns (review, thread, sent)."""
    from agent import graph
    from fakes import fake_analyzer, fake_classifier, fake_synthesizer, issue

    monkeypatch.setattr(graph, "classifier", fake_classifier("stuck"))
    monkeypatch.setattr(graph, "analyzer", fake_analyzer(True))
    monkeypatch.setattr(graph, "synthesizer", fake_synthesizer("escalate_environment", "Escalate it."))
    sent = []
    monkeypatch.setattr(review, "send_feedback", lambda trace_id, answer: sent.append((trace_id, answer)))
    review.graph.invoke({"issue": issue(), "trace_id": "trace-1"}, review.config_for("t1"))
    return review, "t1", sent


def test_an_edit_outside_the_class_is_refused_and_the_run_keeps_waiting(paused, capsys):
    review, thread, sent = paused
    review.decide(thread, {"decision": "edit", "action": "fix_then_rerun"})
    assert "not sent" in capsys.readouterr().out
    assert review.graph.get_state(review.config_for(thread)).interrupts  # still waiting
    assert sent == []


def test_a_decision_is_saved_in_the_run_and_then_sent(paused):
    review, thread, sent = paused
    review.decide(thread, {"decision": "approve"})
    assert review.graph.get_state(review.config_for(thread)).values["review"] == "approved"
    assert sent == [("trace-1", {"decision": "approve"})]


def test_resume_sends_the_decision_of_a_run_that_stopped_after_it_and_only_once(paused, monkeypatch):
    from agent import graph

    review, thread, sent = paused
    real_record_action = graph.record_action
    attempts = []

    def locked_the_first_time(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise RuntimeError("actions.db is locked")
        return real_record_action(*args)

    monkeypatch.setattr(graph, "record_action", locked_the_first_time)
    with pytest.raises(RuntimeError, match="locked"):
        review.decide(thread, {"decision": "approve"})  # saved, then the act step fails
    assert sent == []

    review.resume(thread)
    assert sent == [("trace-1", {"decision": "approve"})]
    review.resume(thread)  # the run is finished: nothing more is sent
    assert sent == [("trace-1", {"decision": "approve"})]


# --- the exact words: demo/recorded_run.txt is a record of them, and console.py shows them ---

GOLDEN = """
==============================================================================
WAITING FOR A PERSON   SUB-1   (run t1)
==============================================================================
  the rules said:   code_defect
  the agent says:   stuck   (overrode the rules)
  deep dive:        failed; the quick look's finding is used
  proposed action:  wait_and_watch
  conclusion:
      Keep watching it. The job has been waiting for a build runner for nine
      hours, and so has every other job on the line.
  a model step failed: deep_dive: TimeoutError: down
  a guard stepped in: allowlist: fix_then_rerun is not allowed for stuck
------------------------------------------------------------------------------
  approve:  uv run python review.py approve t1
  edit:     uv run python review.py edit t1 escalate_environment
  reject:   uv run python review.py reject t1

"""


def test_the_proposal_is_printed_exactly_like_this(review, capsys):
    shown = SHOWN | {
        "conclusion": "Keep watching it. The job has been waiting for a build runner for nine hours, "
        "and so has every other job on the line."
    }
    state = {
        "rule_class": "code_defect",
        "rule_name": "no_other_rule_matched",
        "deep_dive": True,
        "tool_calls": [],
        "guard_notes": ["allowlist: fix_then_rerun is not allowed for stuck"],
        "degraded_reasons": ["deep_dive: TimeoutError: down"],
    }
    review.show_proposal("t1", shown, state)
    assert capsys.readouterr().out == GOLDEN
    # The lines between the banner and the commands are the ones console.py shows.
    assert review.proposal_lines(shown, state) == GOLDEN.splitlines()[4:13]


@pytest.mark.parametrize(
    "state, agent_class, verdict",
    [
        # Code deciding comes first, even if a model step also failed.
        ({"rule_name": "outage", "degraded_reasons": ["classify: x"]}, "stuck", "decided by code"),
        ({"rule_name": "other", "degraded_reasons": ["classify: x"]}, "stuck", "the rules' class stands"),
        ({"rule_name": "other", "degraded_reasons": ["analyze: x"]}, "stuck", "(agreed)"),
        ({"rule_name": "other"}, "code_defect", "(overrode the rules)"),
    ],
)
def test_how_the_class_came_about_is_said_in_this_order(review, state, agent_class, verdict):
    assert verdict in review.class_verdict(state | {"rule_class": "stuck"}, agent_class)


def test_a_new_run_is_made_without_being_started(review, monkeypatch, capsys):
    from fakes import issue

    monkeypatch.setattr(review, "cases_with_history", lambda: [{"id": "case-1", "input": issue()}])
    thread_id, run_input, config, context = review.new_run("case-1", "alice")
    assert capsys.readouterr().out == f"started {thread_id}\nreviewer alice: nothing saved, showing full\n"
    assert thread_id.startswith("case-1-")
    assert run_input == {"issue": issue(), "trace_id": str(config["run_id"])}
    assert config["configurable"] == {"thread_id": thread_id}
    assert context.user_id == "alice"
    assert not review.graph.get_state(review.config_for(thread_id)).values  # nothing has run
    with pytest.raises(SystemExit, match="no case or live issue is called case-2; there are: case-1"):
        review.new_run("case-2")


def test_a_preference_is_saved_and_said(review, capsys):
    review.prefer("alice", "brief")
    assert capsys.readouterr().out == "saved: alice prefers brief conclusions\n"
    assert review.saved_style(review.memory, "alice") == "brief"
    with pytest.raises(ValueError, match="the style must be one of: brief, full"):
        review.prefer("alice", "short")
