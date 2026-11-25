"""The MCP server generated from the API."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client

from store import api
from store.cases import cases_with_history
from store.load_store import load_store
from store.mcp_server import build_server, mcp

SUBMISSION = cases_with_history()[0]["input"]["submission_id"]


@pytest.fixture(scope="module", autouse=True)
def store(tmp_path_factory):
    store_file = tmp_path_factory.mktemp("store") / "store.db"
    load_store(store_file)
    with pytest.MonkeyPatch.context() as patch:  # put back when this file's tests end
        patch.setattr(api, "STORE_FILE", store_file)
        yield


def list_tools(server):
    async def ask():
        async with Client(server) as client:  # an in-memory client, no process
            return await client.list_tools()

    return asyncio.run(ask())


def call_tool(name, arguments):
    async def ask():
        async with Client(mcp) as client:
            return await client.call_tool(name, arguments, raise_on_error=False)

    return asyncio.run(ask())


# --- what a model is offered ---


def test_only_the_routes_tagged_for_investigation_become_tools():
    # Each tool is named by its route's operation_id. The other routes serve
    # facts that code fetches for every run, so the model is not offered them.
    names = {tool.name for tool in list_tools(mcp)}
    assert names == {"list_gate_runs", "list_tickets", "list_status_changes"}


def test_a_tool_s_description_is_the_route_s_docstring():
    tools = {tool.name: tool for tool in list_tools(mcp)}
    for name in ["list_gate_runs", "list_tickets", "list_status_changes"]:
        docstring = getattr(api, name).__doc__
        assert tools[name].description.split() == docstring.split()  # same words


def test_a_tool_s_arguments_come_from_the_route_s_parameters():
    tools = {tool.name: tool for tool in list_tools(mcp)}
    schema = tools["list_gate_runs"].input_schema
    assert list(schema["properties"]) == ["submission_id"]
    assert schema["required"] == ["submission_id"]


# --- calling a tool reaches the store ---


def test_calling_a_tool_returns_the_api_s_answer():
    result = call_tool("list_gate_runs", {"submission_id": SUBMISSION})
    assert not result.is_error
    # FastMCP wraps a list as {"result": [...]}, because MCP requires a tool's
    # structured answer to be a JSON object.
    from_tool = json.loads(result.content[0].text)["result"]
    from_api = TestClient(api.app).get(f"/submissions/{SUBMISSION}/gate-runs").json()
    assert from_tool == from_api


def test_an_unknown_submission_comes_back_as_a_tool_error_not_a_crash():
    result = call_tool("list_gate_runs", {"submission_id": "SUB-0"})
    assert result.is_error
    assert "404" in result.content[0].text


def test_a_route_that_is_not_a_tool_cannot_be_called_as_one():
    result = call_tool("get_submission", {"submission_id": SUBMISSION})
    assert result.is_error


# --- write routes are excluded ---


def test_a_route_that_writes_never_becomes_a_tool_even_when_tagged():
    # Our API has no write route, so build a small one that does. Every route
    # here carries the tag, so only the method can be keeping the writes out.
    other = FastAPI()

    @other.get("/notes", operation_id="read_notes", tags=api.INVESTIGATE)
    def read_notes():
        return []

    @other.post("/notes", operation_id="add_note", tags=api.INVESTIGATE)
    def add_note():
        return {}

    @other.delete("/notes/{note_id}", operation_id="delete_note", tags=api.INVESTIGATE)
    def delete_note(note_id: str):
        return {}

    names = {tool.name for tool in list_tools(build_server(other))}
    assert names == {"read_notes"}
