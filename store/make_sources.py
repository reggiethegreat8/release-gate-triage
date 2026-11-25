"""Write the source files, as if they came from three separate systems.

Run from the repo root with:  uv run python -m store.make_sources

  the submission system
    sources/submissions.csv       who, which release line, active or withdrawn,
                                  new commits, why a run is held
  the test gate
    sources/gate_results.jsonl    one line per gate run
    sources/status_changes.jsonl  one line per change of status
    sources/stage_stats.csv       the typical run time of each stage
  the ticketing system
    sources/tickets.csv           tickets on a submission, or filed by the team
                                  that owns a stage

Every case carries its own history in its `store` block (see cases.py). This
script copies that history into these files. What it adds of its own is fixed
and small: the typical run time of each stage (TYPICAL_HOURS); names (release
lines, teams, peer and ticket IDs); and two defaults for a peer that leaves
them out (its last event, and 2 hours for the age of a peer that is not
running). It invents no evidence about a case.
"""

import csv
import json
from pathlib import Path

from store.cases import STORE_DEFAULTS, case_problems, cases_with_history

SOURCES = Path("sources")

# How long each stage usually takes, in hours. The policy calls a run slow when
# it has taken more than SLOW_FACTOR times this.
TYPICAL_HOURS = {"build": 0.5, "static_checks": 0.25, "integration_tests": 3.0, "release_tests": 6.0}

TEAMS = ["payments", "orders", "accounts", "search"]

# The final event of a peer's run, when a case does not say.
PEER_EVENT = {"failed": "test_failed", "passed": "job_passed", "running": "job_started"}

# The columns of the two files that can be empty, so they still get a header.
SUBMISSION_COLUMNS = [
    "submission_id",
    "release_line",
    "team",
    "state",
    "commits_since_failure",
    "hold_note",
    "dependency_landed",
]
TICKET_COLUMNS = ["ticket_id", "submission_id", "release_line", "stage", "state", "text"]


def submission_row(submission_id, line, team, store=STORE_DEFAULTS):
    landed = store["dependency_landed"]
    return {
        "submission_id": submission_id,
        "release_line": line,
        "team": team,
        "state": store["state"],
        "commits_since_failure": store["commits_since_failure"],
        "hold_note": store["hold_note"],
        # A CSV file has no "nothing": empty means the run waits on no dependency.
        "dependency_landed": "" if landed is None else str(landed).lower(),
    }


def make_sources(cases=None):
    """Return the rows of each source file, by file name.

    Built from every case that carries a history, unless a test passes its own.
    A case with a mistake in its format stops the build; see case_problems.
    """
    cases = cases_with_history() if cases is None else cases
    mistakes = [f"{case['id']}: {problem}" for case in cases for problem in case_problems(case)]
    if mistakes:
        raise ValueError("the cases have mistakes:\n  " + "\n  ".join(mistakes))

    submissions, gate_results, tickets, status_changes = [], [], [], []

    for number, case in enumerate(cases, start=1):
        record = case["input"]
        store = STORE_DEFAULTS | case["store"]  # the case's history, with defaults filled in
        submission_id = record["submission_id"]
        # Each case gets a release line of its own, so one case's peers and
        # tickets can never show up in another case's lookups.
        line = f"R{number:03d}"
        submissions.append(submission_row(submission_id, line, TEAMS[number % len(TEAMS)], store))

        # This submission's gate runs: the earlier ones, then the one in the record.
        for run in store["earlier_runs"]:
            gate_results.append({"submission_id": submission_id, "stage": record["stage"]} | run)
        gate_results.append(
            {
                "submission_id": submission_id,
                "stage": record["stage"],
                "status": record["gate_status"],
                "event": record["job_events"][-1],
                "hours_ago": record["hours_in_status"],
            }
        )

        # Its status changes, oldest first, with the status in the record added.
        # That one is usually last. A change made after the gate's last report
        # (a withdrawal the runner never acknowledged) comes after it.
        changes = store["status_changes"] + [
            {"status": record["gate_status"], "hours_ago": record["hours_in_status"]}
        ]
        for change in sorted(changes, key=lambda change: -change["hours_ago"]):
            status_changes.append({"submission_id": submission_id} | change)

        # The other submissions on the same release line right now.
        for peer_number, peer in enumerate(store["peers"]):
            peer_id = f"SUB-9{number:03d}{peer_number}"
            hours_ago = peer.get("hours_ago", 2)
            submissions.append(submission_row(peer_id, line, TEAMS[peer_number % len(TEAMS)]))
            gate_results.append(
                {
                    "submission_id": peer_id,
                    "stage": record["stage"],
                    "status": peer["status"],
                    "event": peer.get("event") or PEER_EVENT[peer["status"]],
                    "hours_ago": hours_ago,
                }
            )
            status_changes.append(
                {"submission_id": peer_id, "status": peer["status"], "hours_ago": hours_ago}
            )

        for ticket in store["tickets"]:
            tickets.append(
                {
                    "ticket_id": f"TKT-{701 + len(tickets)}",
                    # A stage owner's ticket is about the stage, not one submission.
                    "submission_id": submission_id if ticket["on"] == "submission" else "",
                    "release_line": line,
                    "stage": record["stage"],
                    "state": ticket["state"],
                    "text": ticket["text"],
                }
            )

    stage_stats = [{"stage": stage, "typical_hours": hours} for stage, hours in TYPICAL_HOURS.items()]
    return {
        "submissions": submissions,
        "gate_results": gate_results,
        "tickets": tickets,
        "stage_stats": stage_stats,
        "status_changes": status_changes,
    }


def write_csv(path, columns, rows):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def write_sources(folder=SOURCES, cases=None):
    """Write the five source files into a folder. Returns the rows written."""
    made = make_sources(cases)
    folder.mkdir(exist_ok=True)
    write_csv(folder / "submissions.csv", SUBMISSION_COLUMNS, made["submissions"])
    write_csv(folder / "tickets.csv", TICKET_COLUMNS, made["tickets"])
    write_csv(folder / "stage_stats.csv", ["stage", "typical_hours"], made["stage_stats"])
    write_jsonl(folder / "gate_results.jsonl", made["gate_results"])
    write_jsonl(folder / "status_changes.jsonl", made["status_changes"])
    return made


if __name__ == "__main__":
    made = write_sources()
    print("wrote " + ", ".join(f"{len(rows)} {name}" for name, rows in made.items()) + f" to {SOURCES}/")
