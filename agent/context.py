"""The context step: fetch the facts the operator's policy always needs.

The record the agent is handed carries only what the test gate reports. The
policy also depends on facts held elsewhere: how many other submissions on the
release line are failing, how long this stage usually takes, whether the
submission was withdrawn, whether there are new commits, why a run is held and
whether its dependency has landed, and what tickets the stage's owners have open.

Those facts are needed for every run, so code fetches them here, before any
model step. No model chooses whether to ask. The number comparisons in the
policy (the outage threshold, the MIN_HOURS floor, SLOW_FACTOR times the typical run time) are
done here too, in plain arithmetic, and the model is handed the results.
"""

from fastapi import HTTPException

from agent.policy import MIN_HOURS, OUTAGE_THRESHOLD, SLOW_FACTOR
from store import api


def fetch_context(issue):
    """Return the looked-up facts for one issue, as a plain dict.

    The lookups are the API's own route functions, called directly as Python
    functions: no network and no MCP. If the store is missing or does not know
    this submission or stage, this raises and the run stops. A run that
    carried on without these facts would look healthy and be wrong.
    """
    api.check_store()
    try:
        submission = api.get_submission(issue["submission_id"])
        line = submission["release_line"]
        latest_runs = api.list_latest_runs_on_release_line(line)
        typical_hours = api.get_stage(issue["stage"])["typical_hours"]
        stage_tickets = api.list_stage_tickets(line, issue["stage"])
    except HTTPException as error:
        raise RuntimeError(
            f"the store cannot answer for this issue: {error.detail}. If this "
            "submission should be there, add it to a case file and rebuild the "
            "store"
        ) from error

    peers = [run for run in latest_runs if run["submission_id"] != issue["submission_id"]]
    peers_failing = sum(peer["status"] == "failed" for peer in peers)
    peers_running = [peer for peer in peers if peer["status"] == "running"]
    hours = issue["hours_in_status"]

    return {
        # copied from the store
        "release_line": line,
        "submission_state": submission["state"],
        "commits_since_failure": submission["commits_since_failure"],
        "hold_note": submission["hold_note"],
        "dependency_landed": submission["dependency_landed"],
        "stage_tickets": stage_tickets,
        "typical_hours": typical_hours,
        # counted and compared here, so no model has to do arithmetic
        "peers_total": len(peers),
        "peers_failing": peers_failing,
        "peers_running": len(peers_running),
        "peers_stuck": sum(peer["hours_ago"] >= MIN_HOURS for peer in peers_running),
        "outage": peers_failing >= OUTAGE_THRESHOLD,
        "past_min_hours": hours >= MIN_HOURS,
        "times_typical": round(hours / typical_hours, 1),
        "slow": hours > SLOW_FACTOR * typical_hours,
    }
