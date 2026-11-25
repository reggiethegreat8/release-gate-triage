"""The whole graph, run with fake model steps: routing, fallback, and state."""

import pytest
from pydantic import ValidationError

from agent import graph
from agent.domain import ALLOWED_ACTIONS, MODEL_ACTIONS
from agent.graph import Analysis, Synthesis, build_graph, route_after_analyze
from fakes import Fake, broken, context, fake_analyzer, fake_classifier, fake_synthesizer, issue


def use(monkeypatch, classifier, analyzer, synthesizer):
    monkeypatch.setattr(graph, "classifier", classifier)
    monkeypatch.setattr(graph, "analyzer", analyzer)
    monkeypatch.setattr(graph, "synthesizer", synthesizer)


def run(**issue_changes):
    return graph.graph.invoke({"issue": issue(**issue_changes)})


def with_context(monkeypatch, **changes):
    """Make the context step return these facts in place of the plain ones."""
    monkeypatch.setattr(graph, "fetch_context", lambda record: context(**changes))


# --- the context step: first, in code, and the run stops if it cannot answer ---


def test_the_looked_up_facts_are_kept_in_the_state(monkeypatch):
    with_context(monkeypatch, peers_failing=2, peers_total=3)
    use(monkeypatch, fake_classifier("code_defect"), fake_analyzer(True), fake_synthesizer("fix_then_rerun"))
    assert run()["context"] == context(peers_failing=2, peers_total=3)


def test_every_model_step_is_shown_the_looked_up_facts(monkeypatch):
    with_context(monkeypatch, commits_since_failure=4)
    steps = [fake_classifier("code_defect"), fake_analyzer(True), fake_synthesizer("fix_then_rerun")]
    use(monkeypatch, *steps)
    run()
    for step in steps:
        assert "new commits in the source since this version: 4" in step.prompts[0]


def test_a_store_that_cannot_answer_stops_the_run_before_any_model_step(monkeypatch):
    def no_answer(record):
        raise RuntimeError("the store cannot answer for this issue: no submission SUB-1")

    monkeypatch.setattr(graph, "fetch_context", no_answer)
    steps = [fake_classifier("code_defect"), fake_analyzer(True), fake_synthesizer("fix_then_rerun")]
    use(monkeypatch, *steps)
    with pytest.raises(RuntimeError, match="SUB-1"):
        run()
    assert [step.calls for step in steps] == [0, 0, 0]


# --- the conditional edge ---


def test_a_conclusive_fast_pass_goes_straight_to_synthesize():
    assert route_after_analyze({"conclusive": True}) == "synthesize"


def test_an_inconclusive_fast_pass_goes_to_the_deep_dive():
    assert route_after_analyze({"conclusive": False}) == "deep_dive"


def test_the_deep_dive_is_skipped_when_the_fast_pass_is_conclusive(monkeypatch):
    analyzer = fake_analyzer(conclusive=True)
    use(monkeypatch, fake_classifier("code_defect"), analyzer, fake_synthesizer("fix_then_rerun"))
    result = run()
    assert analyzer.calls == 1
    assert "deep_dive" not in result


def fake_investigation(finding, tool_calls=(), error=None):
    """Stands in for the deep dive's tool agent. Remembers the prompt it got."""
    seen = []

    def investigate(model, answer_schema, prompt):
        seen.append(prompt)
        if error:
            raise error
        return Analysis(finding=finding, conclusive=True), list(tool_calls)

    investigate.prompts = seen
    return investigate


def test_the_deep_dive_runs_when_the_fast_pass_is_inconclusive(monkeypatch):
    analyzer = fake_analyzer(conclusive=False)
    investigation = fake_investigation("careful answer")
    monkeypatch.setattr(graph, "investigate", investigation)
    use(monkeypatch, fake_classifier("code_defect"), analyzer, fake_synthesizer("fix_then_rerun"))
    result = run()
    assert analyzer.calls == 1  # the fast pass only
    assert len(investigation.prompts) == 1  # then the tool agent, once
    assert result["deep_dive"] is True


def test_the_deep_dive_finding_replaces_the_fast_one_and_reaches_synthesize(monkeypatch):
    investigation = fake_investigation("careful answer")
    monkeypatch.setattr(graph, "investigate", investigation)
    synthesizer = fake_synthesizer("fix_then_rerun")
    use(
        monkeypatch,
        fake_classifier("code_defect"),
        fake_analyzer(conclusive=False, finding="quick guess"),
        synthesizer,
    )
    result = run()
    assert result["finding"] == "careful answer"
    assert "quick guess" in investigation.prompts[0]  # the deep dive saw the first finding
    assert "careful answer" in synthesizer.prompts[0]  # synthesize saw the second


def test_the_lookups_the_deep_dive_made_are_kept_in_the_state(monkeypatch):
    calls = [{"name": "list_gate_runs", "args": {"submission_id": "SUB-1"}, "failed": False}]
    monkeypatch.setattr(graph, "investigate", fake_investigation("careful answer", calls))
    use(
        monkeypatch,
        fake_classifier("code_defect"),
        fake_analyzer(conclusive=False),
        fake_synthesizer("fix_then_rerun"),
    )
    assert run()["tool_calls"] == calls


def test_a_failed_deep_dive_keeps_the_fast_finding(monkeypatch):
    monkeypatch.setattr(graph, "investigate", fake_investigation(None, error=TimeoutError("down")))
    use(
        monkeypatch,
        fake_classifier("code_defect"),
        fake_analyzer(conclusive=False, finding="quick guess"),
        fake_synthesizer("fix_then_rerun"),
    )
    result = run()
    assert result["finding"] == "quick guess"
    assert result["degraded_reasons"] == ["deep_dive: TimeoutError: down"]


# --- a normal run ---


def test_a_run_ends_with_a_class_a_conclusion_and_an_action(monkeypatch):
    use(
        monkeypatch,
        fake_classifier("environment_error"),
        fake_analyzer(True),
        fake_synthesizer("rerun_same_version", "The runner died. Rerun it."),
    )
    result = run()
    assert result["rule_class"] == "code_defect"  # the rules' first call
    assert result["triage_class"] == "environment_error"  # the model overrode it
    assert result["conclusion"] == "The runner died. Rerun it."
    assert result["action"] == "rerun_same_version"
    assert result["degraded_reasons"] == []


def test_the_model_action_list_is_the_table_minus_none():
    in_table = {action for actions in ALLOWED_ACTIONS.values() for action in actions}
    assert set(MODEL_ACTIONS) == in_table - {"none"}
    assert len(MODEL_ACTIONS) == len(set(MODEL_ACTIONS))


def test_the_action_schema_rejects_an_unknown_action_and_none():
    for action in ["cancel_submission", "none"]:
        with pytest.raises(ValidationError):
            Synthesis(conclusion="x", action=action)


# --- nothing wrong: no model step is asked ---


def test_no_model_step_runs_when_the_gate_has_passed(monkeypatch):
    fakes = [broken(), broken(), broken()]
    use(monkeypatch, *fakes)
    result = run(gate_status="passed")
    assert [fake.calls for fake in fakes] == [0, 0, 0]
    assert result["triage_class"] == "nothing_wrong"
    assert result["action"] == "none"
    assert result["guard_notes"] == []  # the fixed sentence must not trip the word scan
    assert result["degraded_reasons"] == []


def test_no_model_step_runs_for_a_withdrawn_submission_still_shown_as_running(monkeypatch):
    with_context(monkeypatch, submission_state="withdrawn")
    fakes = [broken(), broken(), broken()]
    use(monkeypatch, *fakes)
    result = run(gate_status="running", job_events=["job_started", "no_heartbeat"])
    assert [fake.calls for fake in fakes] == [0, 0, 0]
    assert result["triage_class"] == "nothing_wrong"
    assert result["action"] == "none"
    assert result["guard_notes"] == []  # the fixed sentence must not trip the word scan


# --- an outage: code picks the class, the model still writes the conclusion ---


def test_during_an_outage_only_the_class_is_taken_out_of_the_model_s_hands(monkeypatch):
    with_context(monkeypatch, outage=True, peers_failing=3)
    classifier, analyzer = fake_classifier("code_defect"), fake_analyzer(True)
    synthesizer = fake_synthesizer("escalate_environment", "The pool is down. Escalate.")
    use(monkeypatch, classifier, analyzer, synthesizer)
    result = run()
    assert classifier.calls == 0
    assert (analyzer.calls, synthesizer.calls) == (1, 1)
    assert result["triage_class"] == "environment_error"
    assert result["conclusion"] == "The pool is down. Escalate."
    assert result["guard_notes"] == []


# --- fallback: the rules take over if the model fails ---


def test_with_every_model_step_failing_the_run_still_answers_from_the_rules(monkeypatch):
    use(monkeypatch, broken(), broken(), broken())
    result = run(gate_status="running")  # the rules say: stuck
    assert result["triage_class"] == "stuck"
    assert result["action"] == "wait_and_watch"  # the default action for stuck
    assert result["conclusion"]
    # The reducer kept all three reasons; none overwrote another.
    assert result["degraded_reasons"] == [
        "classify: TimeoutError: model unavailable",
        "analyze: TimeoutError: model unavailable",
        "synthesize: TimeoutError: model unavailable",
    ]


def test_only_the_failed_step_is_reported(monkeypatch):
    use(monkeypatch, fake_classifier("code_defect"), fake_analyzer(True), broken())
    result = run()
    assert result["degraded_reasons"] == ["synthesize: TimeoutError: model unavailable"]
    assert result["action"] == "fix_then_rerun"


def test_a_malformed_issue_stops_the_run_and_is_not_called_degraded(monkeypatch):
    use(monkeypatch, fake_classifier("code_defect"), fake_analyzer(True), fake_synthesizer("fix_then_rerun"))
    bad_issue = issue()
    del bad_issue["log_excerpt"]
    with pytest.raises(KeyError):
        graph.graph.invoke({"issue": bad_issue})


# --- guards: the model misbehaves on purpose, and code has the last word ---


def test_an_action_from_the_wrong_class_is_corrected(monkeypatch):
    use(
        monkeypatch,
        fake_classifier("code_defect"),
        fake_analyzer(True),
        fake_synthesizer("escalate_environment"),
    )
    result = run()
    assert result["action"] == "fix_then_rerun"
    assert result["guard_notes"] == ["allowlist: escalate_environment is not allowed for code_defect"]


def test_a_rerun_into_an_outage_is_escalated(monkeypatch):
    with_context(monkeypatch, outage=True, peers_failing=4)
    use(
        monkeypatch,
        fake_classifier("environment_error"),
        fake_analyzer(True),
        fake_synthesizer("rerun_same_version"),
    )
    result = run()
    assert result["action"] == "escalate_environment"
    assert result["guard_notes"] == ["forced: 4 others failing, so escalate and do not rerun"]


def test_a_conclusion_that_says_cancel_never_reaches_the_operator(monkeypatch):
    use(
        monkeypatch,
        fake_classifier("stuck"),
        fake_analyzer(True),
        fake_synthesizer("wait_and_watch", "Just cancel the submission and resubmit."),
    )
    result = run(job_events=["job_started", "no_heartbeat"])
    assert "cancel" not in result["conclusion"].lower()
    assert result["action"] == "wait_and_watch"


def test_a_well_behaved_run_has_no_guard_notes(monkeypatch):
    use(monkeypatch, fake_classifier("code_defect"), fake_analyzer(True), fake_synthesizer("fix_then_rerun"))
    assert run()["guard_notes"] == []


def test_the_guards_also_check_the_fallback_answer(monkeypatch):
    # Both model steps that run fail. Code has already picked environment_error
    # for the outage, and the default action for that class is rerun. The guard
    # still escalates.
    with_context(monkeypatch, outage=True, peers_failing=3)
    use(monkeypatch, broken(), broken(), broken())
    result = run(job_events=["job_started", "env_setup_failed"])
    assert result["action"] == "escalate_environment"


# --- the two variants build_graph can make, for the experiments ---


def test_the_default_graph_shows_the_classifier_the_rules_pick(monkeypatch):
    classifier = fake_classifier("code_defect")
    use(monkeypatch, classifier, fake_analyzer(True), fake_synthesizer("fix_then_rerun"))
    run()
    assert "A rule-based check already picked: code_defect" in classifier.prompts[0]


def test_with_the_tools_switched_off_the_deep_dive_is_one_more_model_call(monkeypatch):
    def must_not_run(model, answer_schema, prompt):
        raise AssertionError("the tool agent ran with the tools switched off")

    monkeypatch.setattr(graph, "investigate", must_not_run)
    analyzer = Fake(
        Analysis(finding="quick guess", conclusive=False), Analysis(finding="second look", conclusive=True)
    )
    use(monkeypatch, fake_classifier("code_defect"), analyzer, fake_synthesizer("fix_then_rerun"))
    result = build_graph(tools=False).invoke({"issue": issue()})
    assert analyzer.calls == 2  # the fast pass, then the deep dive
    assert "quick guess" in analyzer.prompts[1]  # the same deep-dive prompt as with tools
    assert result["finding"] == "second look"
    assert result["deep_dive"] is True
    assert result["tool_calls"] == []


def test_with_the_hint_switched_off_the_classifier_is_not_shown_the_rules_pick(monkeypatch):
    classifier = fake_classifier("code_defect")
    use(monkeypatch, classifier, fake_analyzer(True), fake_synthesizer("fix_then_rerun"))
    result = build_graph(rules_hint=False).invoke({"issue": issue()})
    assert "rule-based check" not in classifier.prompts[0]
    assert result["rule_class"] == "code_defect"  # the rules still ran and are still the fallback


# --- what an experiment hands the evaluators ---


def test_the_experiment_s_output_carries_what_the_evaluators_and_the_results_read(monkeypatch):
    from evaluation.run_experiment import triage

    with_context(monkeypatch, commits_since_failure=1)
    use(
        monkeypatch,
        fake_classifier("environment_error"),
        fake_analyzer(True),
        fake_synthesizer("rerun_same_version"),
    )
    output = triage({"issue": issue()})
    assert output["context"] == context(commits_since_failure=1)  # action_allowed and the judge
    assert output["conclusive"] is True  # the fast pass about itself
    assert output["rule_class"] == "code_defect"
    assert output["rule_name"] == "no_other_rule_matched"
    assert output["model_class"] == "environment_error"  # what the model itself said


# --- the deep dive checks the store before it runs ---


def test_a_store_that_is_out_of_date_stops_the_run_before_the_deep_dive(monkeypatch):
    # Without the check, every lookup would come back to the model as a tool
    # error and the run would still look healthy.
    def out_of_date():
        raise RuntimeError("store.db was not built from the current source files")

    investigation = fake_investigation("careful answer")
    monkeypatch.setattr(graph, "investigate", investigation)
    monkeypatch.setattr(graph, "check_store", out_of_date)
    use(
        monkeypatch,
        fake_classifier("code_defect"),
        fake_analyzer(conclusive=False),
        fake_synthesizer("fix_then_rerun"),
    )
    with pytest.raises(RuntimeError, match="not built from the current"):
        run()
    assert investigation.prompts == []


# --- the graph a server runs: the approval steps, with storage supplied by the server ---


def test_the_served_graph_has_the_approval_steps_without_a_checkpointer_of_its_own():
    served = build_graph(approval=True)
    assert {"approval", "act"} <= set(served.get_graph().nodes)
    assert "approval" not in build_graph().get_graph().nodes  # the evaluated graph is unchanged
