"""Load the source files into one SQLite store.

Run from the repo root with:  uv run python -m store.load_store

Five files in two formats go in. One database comes out, where a submission's
record, its gate runs, its status changes and its tickets can be looked up
together by its ID.

The loader also records a fingerprint of the files it was built from, so the
API can tell a store that is out of date (see api.check_store).
"""

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from store.make_sources import SOURCES

STORE_FILE = Path("store.db")

# The source files, in the order they are fingerprinted.
SOURCE_FILES = [
    "submissions.csv",
    "gate_results.jsonl",
    "status_changes.jsonl",
    "tickets.csv",
    "stage_stats.csv",
]

SCHEMA = """
CREATE TABLE loaded_from (
    fingerprint TEXT NOT NULL   -- of the five source files this store was built from
);
CREATE TABLE submissions (
    submission_id         TEXT PRIMARY KEY,
    release_line          TEXT NOT NULL,
    team                  TEXT NOT NULL,
    state                 TEXT NOT NULL CHECK (state IN ('active', 'withdrawn')),
    commits_since_failure INTEGER NOT NULL,  -- new commits since the failed version
    hold_note             TEXT NOT NULL,     -- why the run is held; empty if it is not
    dependency_landed     INTEGER            -- 1 or 0; NULL if it waits on no dependency
);
CREATE TABLE gate_runs (
    submission_id TEXT NOT NULL REFERENCES submissions,
    stage         TEXT NOT NULL,
    status        TEXT NOT NULL,
    event         TEXT NOT NULL,
    hours_ago     INTEGER NOT NULL
);
CREATE TABLE status_changes (
    submission_id TEXT NOT NULL REFERENCES submissions,
    status        TEXT NOT NULL,
    hours_ago     INTEGER NOT NULL
);
CREATE TABLE tickets (
    ticket_id     TEXT PRIMARY KEY,
    submission_id TEXT REFERENCES submissions,  -- NULL for a stage owner's ticket
    release_line  TEXT NOT NULL,
    stage         TEXT NOT NULL,
    state         TEXT NOT NULL,
    text          TEXT NOT NULL
);
CREATE TABLE stage_stats (
    stage         TEXT PRIMARY KEY,
    typical_hours REAL NOT NULL CHECK (typical_hours > 0)
);
"""


def source_fingerprint(sources=SOURCES):
    """A short code that changes if any byte of any source file changes."""
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        digest.update((sources / name).read_bytes())
    return digest.hexdigest()


def read_csv(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def load_store(store_file=STORE_FILE, sources=SOURCES):
    """Build the store from scratch. Returns how many rows each source gave."""
    submissions = read_csv(sources / "submissions.csv")
    tickets = read_csv(sources / "tickets.csv")
    stage_stats = read_csv(sources / "stage_stats.csv")
    gate_runs = read_jsonl(sources / "gate_results.jsonl")
    status_changes = read_jsonl(sources / "status_changes.jsonl")

    fingerprint = source_fingerprint(sources)

    # A CSV file holds only text. Two columns use an empty cell to mean
    # "nothing", which the database stores as NULL.
    for row in submissions:
        row["dependency_landed"] = {"": None, "true": 1, "false": 0}[row["dependency_landed"]]
    for row in tickets:
        row["submission_id"] = row["submission_id"] or None

    Path(store_file).unlink(missing_ok=True)
    store = sqlite3.connect(store_file)
    # SQLite only checks the REFERENCES rules above when asked to. With this
    # on, a gate run, status change or ticket for a submission that does not
    # exist is refused.
    store.execute("PRAGMA foreign_keys = ON")
    store.executescript(SCHEMA)
    store.execute("INSERT INTO loaded_from VALUES (?)", (fingerprint,))
    store.executemany(
        "INSERT INTO submissions VALUES (:submission_id, :release_line, :team, :state, "
        ":commits_since_failure, :hold_note, :dependency_landed)",
        submissions,
    )
    store.executemany(
        "INSERT INTO gate_runs VALUES (:submission_id, :stage, :status, :event, :hours_ago)", gate_runs
    )
    store.executemany(
        "INSERT INTO status_changes VALUES (:submission_id, :status, :hours_ago)", status_changes
    )
    store.executemany(
        "INSERT INTO tickets VALUES (:ticket_id, :submission_id, :release_line, :stage, :state, :text)",
        tickets,
    )
    store.executemany("INSERT INTO stage_stats VALUES (:stage, :typical_hours)", stage_stats)
    store.commit()
    store.close()
    return {
        "submissions": len(submissions),
        "gate_results": len(gate_runs),
        "tickets": len(tickets),
        "stage_stats": len(stage_stats),
        "status_changes": len(status_changes),
    }


if __name__ == "__main__":
    counts = load_store()
    print("loaded " + ", ".join(f"{count} {name}" for name, count in counts.items()) + f" into {STORE_FILE}")
