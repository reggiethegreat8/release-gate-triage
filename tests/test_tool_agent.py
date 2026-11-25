"""The tool-calling agent, with a scripted fake model and the real
in-memory tools. No real model call is made."""

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agent.graph import Analysis
from agent.tool_agent import investigate, tool_calls_made
from evaluation.evaluators import tool_calls_valid
from store import api
from store.cases import cases_with_history
from store.load_store import load_store

SUBMISSION = cases_with_history()[0]["input"]["submission_id"]


@pytest.fixture(scope="module", autouse=True)
def store(tmp_path_factory):
    store_file = tmp_path_factory.mktemp("store") / "store.db"
    load_store(store_file)
    with pytest.MonkeyPatch.context() as patch:  # put back when this file's tests end
        patch.setattr(api, "STORE_FILE", store_file)
        yield


def call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


# --- reading the trajectory back from the message history ---


def test_each_tool_call_is_listed_with_the_arguments_the_model_chose():
    messages = [
        HumanMessage("investigate SUB-1"),
        AIMessage("", tool_calls=[call("list_gate_runs", {"submission_id": "SUB-1"}, "a")]),
        ToolMessage("[...]", tool_call_id="a"),
        AIMessage("It passed before."),
    ]
    assert tool_calls_made(messages, "Analysis") == [
        {"name": "list_gate_runs", "args": {"submission_id": "SUB-1"}, "result": "[...]", "failed": False},
    ]


def test_a_call_that_came_back_as_an_error_is_marked_failed():
    messages = [
        AIMessage("", tool_calls=[call("list_tickets", {"submission_id": "SUB-999"}, "a")]),
        ToolMessage("404: no submission SUB-999", tool_call_id="a", status="error"),
    ]
    assert tool_calls_made(messages, "Analysis")[0]["failed"] is True


def test_a_call_that_never_got_a_result_is_marked_failed():
    messages = [AIMessage("", tool_calls=[call("list_tickets", {"submission_id": "SUB-1"}, "a")])]
    assert tool_calls_made(messages, "Analysis")[0]["failed"] is True


def test_two_calls_in_one_model_turn_are_both_listed():
    messages = [
        AIMessage(
            "",
            tool_calls=[
                call("list_tickets", {"submission_id": "SUB-1"}, "a"),
                call("list_gate_runs", {"submission_id": "SUB-1"}, "b"),
            ],
        )
    ]
    assert [made["name"] for made in tool_calls_made(messages, "Analysis")] == [
        "list_tickets",
        "list_gate_runs",
    ]


def test_the_final_answer_arriving_as_a_tool_call_is_not_a_lookup():
    messages = [AIMessage("", tool_calls=[call("Analysis", {"finding": "x"}, "a")])]
    assert tool_calls_made(messages, "Analysis") == []


def test_no_tool_calls_gives_an_empty_list():
    assert tool_calls_made([HumanMessage("hi"), AIMessage("answer")], "Analysis") == []


# --- the evaluator that checks the model's choices ---


def test_tool_calls_valid_passes_when_every_lookup_worked_or_none_was_made():
    assert tool_calls_valid({"tool_calls": []}) is True
    assert tool_calls_valid({"tool_calls": [{"name": "x", "args": {}, "failed": False}]}) is True


def test_tool_calls_valid_fails_when_a_lookup_came_back_as_an_error():
    assert tool_calls_valid({"tool_calls": [{"name": "x", "args": {}, "failed": True}]}) is False


# --- the whole loop: a scripted model, the real tools, the real store ---


class Scripted(GenericFakeChatModel):
    """A fake model that replies with the messages it was given, in order."""

    profile: dict = {"structured_output": True}  # as a real Claude model reports

    def bind_tools(self, tools, **kwargs):
        return self  # a real model is told about the tools here; a script ignores them


def scripted(*messages):
    return Scripted(messages=iter(messages))


ANSWER = AIMessage('{"finding": "it passed before", "conclusive": true}')


def test_the_agent_calls_a_tool_reads_the_store_and_answers():
    model = scripted(
        AIMessage("", tool_calls=[call("list_gate_runs", {"submission_id": SUBMISSION}, "a")]), ANSWER
    )
    answer, calls = investigate(model, Analysis, "investigate this")
    assert answer == Analysis(finding="it passed before", conclusive=True)
    assert [made["name"] for made in calls] == ["list_gate_runs"]
    assert calls[0]["failed"] is False
    assert '"status"' in calls[0]["result"]  # real rows from the store


def test_a_lookup_of_an_id_that_does_not_exist_is_marked_failed():
    model = scripted(
        AIMessage("", tool_calls=[call("list_gate_runs", {"submission_id": "SUB-0"}, "a")]), ANSWER
    )
    _, calls = investigate(model, Analysis, "investigate this")
    assert calls[0]["failed"] is True
    assert "404" in calls[0]["result"]


def test_a_call_to_a_tool_that_does_not_exist_is_recorded_and_marked_failed():
    model = scripted(AIMessage("", tool_calls=[call("delete_submission", {}, "a")]), ANSWER)
    _, calls = investigate(model, Analysis, "investigate this")
    assert calls[0]["name"] == "delete_submission"
    assert calls[0]["failed"] is True


def lookup_rounds(rounds):
    return [
        AIMessage("", tool_calls=[call("list_gate_runs", {"submission_id": SUBMISSION}, str(n))])
        for n in range(rounds)
    ]


def test_the_step_limit_allows_five_rounds_of_lookups_and_then_the_answer():
    _, calls = investigate(scripted(*lookup_rounds(5), ANSWER), Analysis, "investigate this")
    assert len(calls) == 5


def test_the_step_limit_stops_a_sixth_round_of_lookups():
    with pytest.raises(GraphRecursionError):
        investigate(scripted(*lookup_rounds(6), ANSWER), Analysis, "investigate this")


def test_a_model_that_keeps_asking_for_tools_is_stopped():
    lookups = [
        AIMessage("", tool_calls=[call("list_gate_runs", {"submission_id": SUBMISSION}, str(n))])
        for n in range(8)
    ]
    with pytest.raises(GraphRecursionError):
        investigate(scripted(*lookups), Analysis, "investigate this")


# --- the deep dive inside the graph that pauses for approval ---


def test_the_tool_agent_runs_inside_a_graph_saved_to_sqlite(monkeypatch, tmp_path):
    # review.py builds the graph with a SQLite checkpointer, which has no async
    # methods. The tool agent is async and runs inside the graph's deep-dive
    # step, so it must not try to use the graph's checkpointer.
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from agent import graph, tool_agent
    from fakes import fake_analyzer, fake_classifier, fake_synthesizer, issue

    monkeypatch.setattr(graph, "investigate", tool_agent.investigate)  # undo the stand-in
    monkeypatch.setattr(
        graph,
        "model",
        scripted(
            AIMessage("", tool_calls=[call("list_gate_runs", {"submission_id": SUBMISSION}, "a")]), ANSWER
        ),
    )
    monkeypatch.setattr(graph, "classifier", fake_classifier("stuck"))
    monkeypatch.setattr(graph, "analyzer", fake_analyzer(conclusive=False))
    monkeypatch.setattr(graph, "synthesizer", fake_synthesizer("wait_and_watch"))
    saver = SqliteSaver(sqlite3.connect(tmp_path / "checkpoints.db", check_same_thread=False))
    app = graph.build_graph(saver)
    result = app.invoke({"issue": issue(submission_id=SUBMISSION)}, {"configurable": {"thread_id": "t1"}})
    assert result["degraded_reasons"] == []
    assert [made["name"] for made in result["tool_calls"]] == ["list_gate_runs"]
