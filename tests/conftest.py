"""Runs before any test. Tests must never reach LangSmith or a real model."""

import os

# These are set before graph.py loads .env, and load_dotenv does not replace
# values that already exist. So tests need no .env, send no traces, and could
# not pay for a model call even if a test forgot to swap in a fake.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
os.environ["TRIAGE_MODEL"] = "test-model"
os.environ["JUDGE_MODEL"] = "test-judge-model"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_tool_agent(monkeypatch):
    """In every test the deep dive's tool agent is a stand-in, the context step
    returns plain facts without reading a store, and the check that the real
    store.db exists is switched off. A test that cares what the deep dive
    returns, or what the context holds, replaces the stand-in again."""
    from agent import graph
    from fakes import context

    def stand_in(model, answer_schema, prompt):
        return graph.Analysis(finding="investigated", conclusive=True), []

    monkeypatch.setattr(graph, "investigate", stand_in)
    monkeypatch.setattr(graph, "check_store", lambda: None)
    monkeypatch.setattr(graph, "fetch_context", lambda record: context())


@pytest.fixture(autouse=True)
def actions_in_a_temporary_file(tmp_path, monkeypatch):
    """No test writes to the real actions.db."""
    from agent import actions

    monkeypatch.setattr(actions, "ACTIONS_FILE", tmp_path / "actions.db")
