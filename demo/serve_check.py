"""Drive one issue through the graph as a server runs it.

Start the server first, in another terminal:

    uv run langgraph dev --allow-blocking --no-browser

Then, from the repo root:

    uv run python demo/serve_check.py

It talks to the server through the LangGraph SDK, the way a customer's own
service would: it starts a run for one reviewer, shows the proposal the run
pauses on, approves it, and shows the action recorded. The server supplies
the storage for the paused run and the reviewers' memory.

Run it from the same folder the server was started in: it reads the recorded
action from actions.db there.
"""

import sqlite3
import sys

from langgraph_sdk import get_sync_client

sys.path.insert(0, ".")
from agent.actions import ACTIONS_FILE  # noqa: E402
from store.cases import cases_with_history  # noqa: E402

if __name__ == "__main__":
    case = {case["id"]: case for case in cases_with_history()}["dev-04"]
    client = get_sync_client(url="http://127.0.0.1:2024")

    # A reviewer's saved preference goes in the server's store, under their user ID.
    client.store.put_item(("alice", "preferences"), key="conclusion_style", value={"style": "brief"})

    thread = client.threads.create()
    # The reviewer's ID is runtime context: beside the input, never in it.
    paused = client.runs.wait(
        thread["thread_id"], "triage", input={"issue": case["input"]}, context={"user_id": "alice"}
    )
    proposal = paused["__interrupt__"][0]["value"]
    print(f"paused for approval: {proposal['triage_class']} / {proposal['action']}")
    print(f"  shown to alice (brief): {proposal['conclusion']}")
    print(f"  lookups made: {[call['name'] for call in paused.get('tool_calls', [])]}")

    done = client.runs.wait(thread["thread_id"], "triage", command={"resume": {"decision": "approve"}})
    print(f"after approval: review={done['review']}, final action={done['final_action']}")

    row = (
        sqlite3.connect(ACTIONS_FILE)
        .execute(
            "SELECT submission_id, action, review FROM actions WHERE thread_id = ?", (thread["thread_id"],)
        )
        .fetchone()
    )
    print(f"recorded once for this run: {row}")
