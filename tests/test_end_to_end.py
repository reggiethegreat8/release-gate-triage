"""The whole graph on the development cases, with the REAL context step reading a real
store and fake model steps. Every other graph test uses a stand-in for the
context, so this is where the two halves are shown to fit together."""

import pytest

from agent import context, graph
from fakes import broken, fake_analyzer, fake_classifier, fake_synthesizer
from store import api
from store.cases import DEV_FILE, read_cases
from store.load_store import load_store

CASES = {case["id"]: case for case in read_cases(DEV_FILE)}


@pytest.fixture(autouse=True)
def real_context(tmp_path_factory, monkeypatch):
    store_file = tmp_path_factory.mktemp("store") / "store.db"
    load_store(store_file)
    monkeypatch.setattr(api, "STORE_FILE", store_file)
    monkeypatch.setattr(graph, "fetch_context", context.fetch_context)  # undo the stand-in


def use(monkeypatch, classifier, analyzer, synthesizer):
    monkeypatch.setattr(graph, "classifier", classifier)
    monkeypatch.setattr(graph, "analyzer", analyzer)
    monkeypatch.setattr(graph, "synthesizer", synthesizer)
    return classifier, analyzer, synthesizer


def run(case_id):
    return graph.graph.invoke({"issue": CASES[case_id]["input"]})


def test_every_development_case_runs_to_an_answer(monkeypatch):
    use(monkeypatch, fake_classifier("stuck"), fake_analyzer(True), fake_synthesizer("wait_and_watch"))
    for case_id in CASES:
        result = run(case_id)
        assert result["action"] and result["conclusion"], case_id
        assert result["degraded_reasons"] == [], case_id


def test_an_outage_in_the_store_is_escalated_whatever_the_model_says(monkeypatch):
    classifier, _, _ = use(
        monkeypatch,
        fake_classifier("code_defect"),
        fake_analyzer(True),
        fake_synthesizer("rerun_same_version"),
    )
    result = run("dev-03")  # three failing peers
    assert classifier.calls == 0
    assert (result["triage_class"], result["action"]) == ("environment_error", "escalate_environment")
    assert result["guard_notes"] == ["forced: 3 others failing, so escalate and do not rerun"]


def test_one_failing_peer_fewer_is_left_to_the_model(monkeypatch):
    classifier, _, _ = use(
        monkeypatch,
        fake_classifier("environment_error"),
        fake_analyzer(True),
        fake_synthesizer("rerun_same_version"),
    )
    result = run("dev-02")  # two failing peers
    assert classifier.calls == 1
    assert (result["action"], result["guard_notes"]) == ("rerun_same_version", [])


def test_a_withdrawn_submission_in_the_store_gets_no_action_and_no_model_call(monkeypatch):
    steps = use(monkeypatch, broken(), broken(), broken())
    result = run("dev-08")  # the gate still shows it running
    assert [step.calls for step in steps] == [0, 0, 0]
    assert (result["triage_class"], result["action"]) == ("nothing_wrong", "none")
    assert result["guard_notes"] == []  # the fixed sentence must not trip the word scan


def test_the_stage_owner_s_ticket_in_the_store_reaches_every_model_step(monkeypatch):
    steps = use(
        monkeypatch,
        fake_classifier("stage_tooling_failure"),
        fake_analyzer(True),
        fake_synthesizer("wait_and_watch"),
    )
    run("dev-07")
    for step in steps:
        assert "buildtool 9.4.1 crashes in the dependency planner" in step.prompts[0]
        assert "TKT-" in step.prompts[0]
