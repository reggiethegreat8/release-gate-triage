"""The case files, and how to read and check them.

A case written for the evaluation is one line of JSON:

  id      a name for the case, such as dev-04
  kind    "ordinary" or "hard"; for people reading results
  rule    the one policy line (see policy.py) that produced the label
  pair    optional: two cases with the same pair name sit on either side of
          one condition in the policy
  cause   one sentence: what really went wrong
  input   the record the agent is handed. Only what the gate itself reports.
  store   the case's history, which goes into the store and not to the agent:
          its peers on the release line, earlier runs, tickets and so on.
          Every key is optional; STORE_DEFAULTS says what a missing one means.
  label   the answer: true_class and acceptable_actions. Never shown to the agent.

Two kinds of line carry less. A live issue or a probe has only id, input and
store: it has no answer. A case added from production has id, kind
("from_production"), input and label: it is the same submission as the live
issue it came from, so its history is already in the store.

The agent sees `input`. Everything else it learns about a submission, it has to
look up in the store.
"""

import json
from pathlib import Path

DEV_FILE = Path("data/cases_dev.jsonl")  # may be looked at while changing prompts
TEST_FILE = Path("data/cases_test.jsonl")  # held out: never tuned against
PROBE_FILE = Path("data/probes.jsonl")  # no label: for runs that are not scored
LIVE_FILE = Path("data/live_issues.jsonl")  # unlabeled issues standing in for production
REGRESSION_FILE = Path("data/regression_cases.jsonl")  # cases added from production

# The record: what the gate reports about one run, and nothing else.
RECORD_FIELDS = ("submission_id", "gate_status", "stage", "hours_in_status", "job_events", "log_excerpt")

# A case's history, and what each key means when a case leaves it out.
STORE_DEFAULTS = {
    "state": "active",  # or "withdrawn"
    "commits_since_failure": 0,  # new commits in the source since the failed version
    "hold_note": "",  # free text: why the run is being held, if it is
    "dependency_landed": None,  # True or False if the run waits on a dependency
    "peers": [],  # other submissions on the release line: {status, event, hours_ago}
    "earlier_runs": [],  # this submission's earlier gate runs: {status, event, hours_ago}
    "tickets": [],  # {on: "submission" or "stage", state, text}
    "status_changes": [],  # other status changes, oldest first: {status, hours_ago}
}

STATES = ("active", "withdrawn")
PEER_STATUSES = ("failed", "passed", "running")
PEER_KEYS = {"status", "event", "hours_ago"}
RUN_KEYS = {"status", "event", "hours_ago"}
TICKET_KEYS = {"on", "state", "text"}
CHANGE_KEYS = {"status", "hours_ago"}


def read_cases(path, optional=False):
    """The cases in one file.

    A missing file is an error, so a wrong folder or a renamed file can never
    read as "no cases". optional=True is for the one file that may not exist
    yet: the cases added from production.
    """
    if not path.exists():
        if optional:
            return []
        raise FileNotFoundError(f"{path} does not exist; run from the repo root")
    return [json.loads(line) for line in path.read_text().splitlines()]


def cases_with_history():
    """Every case that carries its own history for the store.

    A case added from production is left out: it is the same submission as the
    live issue it came from, which is already here.
    """
    return read_cases(DEV_FILE) + read_cases(TEST_FILE) + read_cases(PROBE_FILE) + read_cases(LIVE_FILE)


def case_problems(case):
    """Return what is wrong with one case's format, as a list of sentences.

    Building the store fills in a default for anything a case leaves out. So a
    misspelled key or value would not fail: it would quietly give the case a
    different history from the one its author meant, and the agent would be
    scored against a label the store contradicts. This check turns each such
    mistake into an error that names it.
    """
    problems = []
    record = case["input"]
    if set(record) != set(RECORD_FIELDS):
        problems.append(f"the record must have exactly these fields: {', '.join(RECORD_FIELDS)}")
    store = case.get("store")
    if store is None:  # a case added from production has no history of its own
        return problems

    unknown = set(store) - set(STORE_DEFAULTS)
    if unknown:
        problems.append(f"unknown keys in the history: {', '.join(sorted(unknown))}")
    if store.get("state", "active") not in STATES:
        problems.append(f"state must be one of: {', '.join(STATES)}")
    if not (store.get("dependency_landed") is None or isinstance(store["dependency_landed"], bool)):
        problems.append("dependency_landed must be true, false or null")

    for peer in store.get("peers", []):
        if set(peer) - PEER_KEYS:
            problems.append(f"unknown keys in a peer: {', '.join(sorted(set(peer) - PEER_KEYS))}")
        if peer.get("status") not in PEER_STATUSES:
            problems.append(f"a peer's status must be one of: {', '.join(PEER_STATUSES)}")
        # How long a peer has been running decides whether it counts as stuck.
        elif peer["status"] == "running" and "hours_ago" not in peer:
            problems.append("a running peer must say how long it has been running (hours_ago)")

    for run in store.get("earlier_runs", []):
        if set(run) != RUN_KEYS:
            problems.append(f"an earlier run must have exactly: {', '.join(sorted(RUN_KEYS))}")
        # The store finds a submission's latest run by its age.
        elif run["hours_ago"] <= record["hours_in_status"]:
            problems.append("an earlier run must be older than the run in the record")

    for ticket in store.get("tickets", []):
        if set(ticket) != TICKET_KEYS:
            problems.append(f"a ticket must have exactly: {', '.join(sorted(TICKET_KEYS))}")
        elif ticket["on"] not in ("submission", "stage") or ticket["state"] not in ("open", "closed"):
            problems.append('a ticket is on "submission" or "stage", and is "open" or "closed"')

    for change in store.get("status_changes", []):
        if set(change) != CHANGE_KEYS:
            problems.append(f"a status change must have exactly: {', '.join(sorted(CHANGE_KEYS))}")

    return problems
