"""What happens after a person approves: the action is recorded, once.

This stands in for the real effect of an approval, such as opening a ticket or
starting a rerun. It is a write, so it is kept apart from the store the agent
reads, which stays read-only. It has its own file and one table.

The run's thread ID is the table's primary key.
So a second record for the same run is refused by the database, whatever
caused the second attempt.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

ACTIONS_FILE = Path("actions.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    thread_id     TEXT PRIMARY KEY,   -- one row per run, ever
    submission_id TEXT NOT NULL,
    action        TEXT NOT NULL,
    review        TEXT NOT NULL,      -- approved or edited
    recorded_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def record_action(thread_id, submission_id, action, review):
    """Record the approved action for one run. Returns True if this call
    recorded it, and False if the run already had one."""
    with closing(sqlite3.connect(ACTIONS_FILE)) as actions:
        actions.executescript(SCHEMA)
        # OR IGNORE: if a row with this thread ID exists, do nothing and raise
        # nothing. rowcount then says whether a row was actually added.
        added = actions.execute(
            "INSERT OR IGNORE INTO actions (thread_id, submission_id, action, review) VALUES (?, ?, ?, ?)",
            (thread_id, submission_id, action, review),
        ).rowcount
        actions.commit()
    return added == 1


def action_for(thread_id):
    """The action recorded for one run, or None."""
    if not ACTIONS_FILE.exists():
        return None
    with closing(sqlite3.connect(ACTIONS_FILE)) as actions:
        actions.row_factory = sqlite3.Row
        actions.executescript(SCHEMA)
        row = actions.execute("SELECT * FROM actions WHERE thread_id = ?", (thread_id,)).fetchone()
    return dict(row) if row else None
