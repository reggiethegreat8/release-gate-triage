"""A small read-only API over the store.

Every route is a GET. FastAPI writes the OpenAPI spec for them, which is what
the MCP server is generated from.

The routes are of two kinds:
  - Three are tagged "investigate". Only those become tools a model can choose
    to call during the deep dive. Their docstrings are the descriptions the
    model reads when deciding.
  - The rest serve facts the operator's policy always needs. Code fetches
    those before any model runs; no model chooses whether to ask.

To look at it in a browser, build the store first (uv run python -m store.load_store),
then run:  uv run uvicorn store.api:app   and open http://127.0.0.1:8000/docs
"""

import sqlite3
from contextlib import closing

from fastapi import FastAPI, HTTPException

from store.load_store import STORE_FILE, source_fingerprint
from store.make_sources import SOURCES

app = FastAPI(title="Triage store", description="Read-only lookups about code submissions.")

# The tag that marks a route as a tool the model may choose to call.
INVESTIGATE = ["investigate"]


def query(sql, *values):
    """Run one SELECT and return the rows as dicts.

    mode=ro opens the database read-only, so no route here can change it, even
    by mistake.
    """
    with closing(sqlite3.connect(f"file:{STORE_FILE}?mode=ro", uri=True)) as store:
        store.row_factory = sqlite3.Row
        return [dict(row) for row in store.execute(sql, values)]


def check_store():
    """Stop with a clear message if the store is missing or out of date.

    The context step calls this first, so every run is checked. The deep dive
    calls it again for a run resumed from a saved checkpoint after its context
    step: the store may have been rebuilt in between, and without the check
    each lookup would come back to the model as a tool error, the model would
    answer without any records, and the run would look healthy.

    "Out of date" means the store was not built from the source files as they
    are now. The loader records a fingerprint of the files it read; this
    compares it with a fresh one. Counting rows would not be enough: a case can
    be edited without the number of rows changing, and the run would then
    carry on with the wrong facts and no error.
    """
    fix = "run: uv run python -m store.make_sources && uv run python -m store.load_store"
    try:
        loaded_from = query("SELECT fingerprint FROM loaded_from")[0]["fingerprint"]
    except (sqlite3.Error, IndexError) as error:
        raise RuntimeError(f"{STORE_FILE} cannot be read ({error!r}); {fix}") from error
    try:
        current = source_fingerprint(SOURCES)
    except OSError as error:
        raise RuntimeError(f"a source file cannot be read ({error}); {fix}") from error
    if loaded_from != current:
        raise RuntimeError(f"{STORE_FILE} was not built from the current source files in {SOURCES}/; {fix}")


def require_submission(submission_id):
    rows = query("SELECT * FROM submissions WHERE submission_id = ?", submission_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"no submission {submission_id}")
    return rows[0]


def require_release_line(release_line):
    # An unknown line is an error, not an empty list. An empty list would read
    # as "nobody else is failing", which is evidence, and here it would be false.
    if not query("SELECT 1 FROM submissions WHERE release_line = ? LIMIT 1", release_line):
        raise HTTPException(status_code=404, detail=f"no release line {release_line}")


# --- facts the policy always needs: fetched by code, never offered as tools ---


@app.get("/submissions/{submission_id}", operation_id="get_submission")
def get_submission(submission_id: str):
    """One submission: its release line and team, whether it is active or
    withdrawn, new commits since the failed version, why it is held, and
    whether a dependency it waits on has landed."""
    row = require_submission(submission_id)
    # The store keeps this as 1, 0 or nothing. Give it back as true, false or null.
    landed = row["dependency_landed"]
    return row | {"dependency_landed": None if landed is None else bool(landed)}


@app.get("/release-lines/{release_line}/latest-runs", operation_id="list_latest_runs_on_release_line")
def list_latest_runs_on_release_line(release_line: str):
    """The latest gate run of every submission on one release line."""
    require_release_line(release_line)
    return query(
        "SELECT g.submission_id, g.stage, g.status, g.event, g.hours_ago "
        "FROM gate_runs g JOIN submissions s ON s.submission_id = g.submission_id "
        "WHERE s.release_line = ? "
        "AND g.hours_ago = (SELECT MIN(hours_ago) FROM gate_runs "
        "                   WHERE submission_id = g.submission_id) "
        "ORDER BY g.submission_id",
        release_line,
    )


@app.get("/release-lines/{release_line}/stages/{stage}/tickets", operation_id="list_stage_tickets")
def list_stage_tickets(release_line: str, stage: str):
    """The tickets filed by the team that owns one stage, for one release line."""
    require_release_line(release_line)
    get_stage(stage)  # an unknown stage is an error too
    return query(
        "SELECT ticket_id, state, text FROM tickets "
        "WHERE release_line = ? AND stage = ? AND submission_id IS NULL "
        "ORDER BY ticket_id",
        release_line,
        stage,
    )


@app.get("/stages/{stage}", operation_id="get_stage")
def get_stage(stage: str):
    """One stage: how many hours a run of it typically takes."""
    rows = query("SELECT stage, typical_hours FROM stage_stats WHERE stage = ?", stage)
    if not rows:
        raise HTTPException(status_code=404, detail=f"no stage {stage}")
    return rows[0]


# --- tools: the model may choose to call these during the deep dive ---


@app.get("/submissions/{submission_id}/gate-runs", operation_id="list_gate_runs", tags=INVESTIGATE)
def list_gate_runs(submission_id: str):
    """List every gate run of one submission, newest first: stage, status,
    final event, and how many hours ago. Use it to see whether this submission
    passed before, or has failed the same way repeatedly."""
    require_submission(submission_id)
    return query(
        "SELECT stage, status, event, hours_ago FROM gate_runs WHERE submission_id = ? ORDER BY hours_ago",
        submission_id,
    )


@app.get("/submissions/{submission_id}/tickets", operation_id="list_tickets", tags=INVESTIGATE)
def list_tickets(submission_id: str):
    """List the tickets filed against one submission, with their state and text."""
    require_submission(submission_id)
    return query("SELECT ticket_id, state, text FROM tickets WHERE submission_id = ?", submission_id)


@app.get("/submissions/{submission_id}/status-changes", operation_id="list_status_changes", tags=INVESTIGATE)
def list_status_changes(submission_id: str):
    """List every change of status of one submission, oldest first, with how
    many hours ago each happened. Use it to see how the submission got to
    where it is: how long it queued, or whether something changed after the
    gate's last report."""
    require_submission(submission_id)
    return query(
        "SELECT status, hours_ago FROM status_changes WHERE submission_id = ? ORDER BY hours_ago DESC",
        submission_id,
    )
