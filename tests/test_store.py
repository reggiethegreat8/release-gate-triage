"""The source files, the store, and the read-only API."""

import csv
import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from store import api
from store.cases import STORE_DEFAULTS, cases_with_history
from store.load_store import load_store
from store.make_sources import SOURCES, TYPICAL_HOURS, make_sources

CASES = cases_with_history()


def history(case, key):
    """One part of a case's history, or the default if the case left it out."""
    return case["store"].get(key, STORE_DEFAULTS[key])


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """The API over a fresh store built from the source files in sources/."""
    store_file = tmp_path_factory.mktemp("store") / "store.db"
    load_store(store_file)
    with pytest.MonkeyPatch.context() as patch:  # put back when this file's tests end
        patch.setattr(api, "STORE_FILE", store_file)
        yield TestClient(api.app)


# --- the source files ---


def test_the_source_files_match_the_cases():
    # Catches editing a case without rerunning make_sources.py.
    made = make_sources()
    for name in ("submissions", "tickets", "stage_stats"):
        with (SOURCES / f"{name}.csv").open(newline="") as file:
            in_file = list(csv.DictReader(file))
        assert in_file == [{key: str(value) for key, value in row.items()} for row in made[name]], name
    for name in ("gate_results", "status_changes"):
        lines = (SOURCES / f"{name}.jsonl").read_text().splitlines()
        assert [json.loads(line) for line in lines] == made[name], name


# --- the store ---


def test_every_row_of_every_source_reaches_the_store(tmp_path):
    made = make_sources()
    assert load_store(tmp_path / "store.db") == {name: len(rows) for name, rows in made.items()}


# --- the store agrees with the cases it was built from ---


def test_every_case_s_submission_can_be_looked_up(client):
    for case in CASES:
        submission_id = case["input"]["submission_id"]
        assert client.get(f"/submissions/{submission_id}").json()["submission_id"] == submission_id


def test_a_submission_carries_the_facts_its_case_wrote(client):
    for case in CASES:
        row = client.get(f"/submissions/{case['input']['submission_id']}").json()
        for key in ("state", "commits_since_failure", "hold_note", "dependency_landed"):
            assert row[key] == history(case, key), (case["id"], key)


def test_the_newest_gate_run_is_the_one_in_the_record(client):
    for case in CASES:
        record = case["input"]
        newest = client.get(f"/submissions/{record['submission_id']}/gate-runs").json()[0]
        assert newest["status"] == record["gate_status"]
        assert newest["event"] == record["job_events"][-1]
        assert newest["hours_ago"] == record["hours_in_status"]


def test_all_of_a_case_s_earlier_runs_are_listed_newest_first(client):
    for case in CASES:
        runs = client.get(f"/submissions/{case['input']['submission_id']}/gate-runs").json()
        assert len(runs) == 1 + len(history(case, "earlier_runs")), case["id"]
        ages = [run["hours_ago"] for run in runs]
        assert ages == sorted(ages), case["id"]


def test_a_submission_with_earlier_runs_is_listed_once_on_its_line_by_its_latest_run(client):
    several = [case for case in CASES if history(case, "earlier_runs")]
    assert several
    for case in several:
        record = case["input"]
        line = client.get(f"/submissions/{record['submission_id']}").json()["release_line"]
        own = [
            row
            for row in client.get(f"/release-lines/{line}/latest-runs").json()
            if row["submission_id"] == record["submission_id"]
        ]
        assert [(row["status"], row["hours_ago"]) for row in own] == [
            (record["gate_status"], record["hours_in_status"])
        ], case["id"]


def test_the_release_line_holds_the_case_s_peers_and_nobody_else_s(client):
    for case in CASES:
        submission_id = case["input"]["submission_id"]
        line = client.get(f"/submissions/{submission_id}").json()["release_line"]
        latest = client.get(f"/release-lines/{line}/latest-runs").json()
        others = [row["status"] for row in latest if row["submission_id"] != submission_id]
        assert sorted(others) == sorted(peer["status"] for peer in history(case, "peers")), case["id"]


def test_a_stage_owner_s_ticket_is_found_by_line_and_stage(client):
    with_stage_ticket = [
        case for case in CASES if any(ticket["on"] == "stage" for ticket in history(case, "tickets"))
    ]
    assert with_stage_ticket
    for case in with_stage_ticket:
        record = case["input"]
        line = client.get(f"/submissions/{record['submission_id']}").json()["release_line"]
        found = client.get(f"/release-lines/{line}/stages/{record['stage']}/tickets").json()
        wanted = [ticket["text"] for ticket in history(case, "tickets") if ticket["on"] == "stage"]
        assert [ticket["text"] for ticket in found] == wanted


def test_a_stage_owner_s_ticket_is_not_listed_against_the_submission(client):
    for case in CASES:
        tickets = client.get(f"/submissions/{case['input']['submission_id']}/tickets").json()
        wanted = [t["text"] for t in history(case, "tickets") if t["on"] == "submission"]
        assert [ticket["text"] for ticket in tickets] == wanted, case["id"]


def test_the_status_changes_are_oldest_first_and_include_the_status_in_the_record():
    changes = {}
    for row in make_sources()["status_changes"]:
        changes.setdefault(row["submission_id"], []).append(row)
    for case in CASES:
        record = case["input"]
        rows = changes[record["submission_id"]]
        assert len(rows) == 1 + len(history(case, "status_changes")), case["id"]
        assert [row["hours_ago"] for row in rows] == sorted(
            (row["hours_ago"] for row in rows), reverse=True
        ), case["id"]  # oldest first
        assert {"status": record["gate_status"], "hours_ago": record["hours_in_status"]} in [
            {"status": row["status"], "hours_ago": row["hours_ago"]} for row in rows
        ]


def test_the_status_changes_of_a_submission_can_be_looked_up_oldest_first(client):
    for case in CASES:
        record = case["input"]
        changes = client.get(f"/submissions/{record['submission_id']}/status-changes").json()
        assert len(changes) == 1 + len(history(case, "status_changes")), case["id"]
        ages = [change["hours_ago"] for change in changes]
        assert ages == sorted(ages, reverse=True), case["id"]


def test_a_withdrawal_the_gate_never_saw_shows_up_after_the_gate_s_last_status(client):
    # dev-08: the gate still says running, 26 hours. The owner withdrew it 21 hours ago.
    changes = client.get("/submissions/SUB-6108/status-changes").json()
    assert [(change["status"], change["hours_ago"]) for change in changes] == [
        ("queued", 27),
        ("running", 26),
        ("withdrawn", 21),
    ]


def test_every_stage_has_a_typical_run_time(client):
    for stage, hours in TYPICAL_HOURS.items():
        assert client.get(f"/stages/{stage}").json() == {"stage": stage, "typical_hours": hours}


# --- the API's edges ---


def test_an_unknown_submission_is_a_404_on_every_route(client):
    for path in [
        "/submissions/SUB-0",
        "/submissions/SUB-0/gate-runs",
        "/submissions/SUB-0/tickets",
        "/submissions/SUB-0/status-changes",
    ]:
        assert client.get(path).status_code == 404


def test_an_unknown_release_line_or_stage_is_a_404_not_an_empty_answer(client):
    # An empty list would read as "nobody else is failing" or "no ticket is
    # open", which is evidence, and here it would be false.
    assert client.get("/release-lines/nowhere/latest-runs").status_code == 404
    assert client.get("/release-lines/nowhere/stages/build/tickets").status_code == 404
    assert client.get("/stages/nowhere").status_code == 404


def test_a_real_line_and_stage_with_no_ticket_is_an_empty_list(client):
    case = next(case for case in CASES if not history(case, "tickets"))
    record = case["input"]
    line = client.get(f"/submissions/{record['submission_id']}").json()["release_line"]
    assert client.get(f"/release-lines/{line}/stages/{record['stage']}/tickets").json() == []


def test_text_that_looks_like_sql_is_treated_as_a_value(client):
    # If the text were run as SQL, "OR '1'='1'" would match every row.
    assert client.get("/submissions/x' OR '1'='1").status_code == 404
    assert client.get("/release-lines/x' OR '1'='1/latest-runs").status_code == 404


def test_every_route_is_a_get(client):
    methods = {method for path in client.get("/openapi.json").json()["paths"].values() for method in path}
    assert methods == {"get"}


def test_the_api_cannot_write_to_the_store(client):
    with pytest.raises(sqlite3.OperationalError):
        api.query("DELETE FROM submissions")
    assert client.get(f"/submissions/{CASES[0]['input']['submission_id']}").status_code == 200


# --- the check that the store is there and current ---


def test_a_good_store_passes_the_check(client):
    api.check_store()


def test_a_missing_store_is_reported_with_how_to_fix_it(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "STORE_FILE", tmp_path / "no-such-store.db")
    with pytest.raises(RuntimeError, match="store.load_store"):
        api.check_store()


# Written out here, not read from the loader: a file dropped from the loader's
# own list would otherwise drop out of this test too.
FIVE_SOURCE_FILES = [
    "submissions.csv",
    "gate_results.jsonl",
    "status_changes.jsonl",
    "tickets.csv",
    "stage_stats.csv",
]


@pytest.mark.parametrize("name", FIVE_SOURCE_FILES)
def test_a_change_to_any_one_source_file_is_reported(client, tmp_path, monkeypatch, name):
    newer = tmp_path / "sources"
    shutil.copytree(SOURCES, newer)
    with (newer / name).open("a") as file:
        file.write("\n")
    monkeypatch.setattr(api, "SOURCES", newer)
    with pytest.raises(RuntimeError, match="not built from the current source files"):
        api.check_store()


def test_a_store_built_from_other_source_files_is_reported_even_with_the_same_row_counts(
    client, tmp_path, monkeypatch
):
    # Someone edits a case and rebuilds the source files, but not the store.
    # Here one peer's status changes: every table still has as many rows.
    newer = tmp_path / "sources"
    shutil.copytree(SOURCES, newer)
    lines = (newer / "gate_results.jsonl").read_text().splitlines()
    changed = json.loads(lines[-1]) | {"status": "passed", "event": "job_passed"}
    (newer / "gate_results.jsonl").write_text("\n".join(lines[:-1] + [json.dumps(changed)]) + "\n")
    monkeypatch.setattr(api, "SOURCES", newer)
    with pytest.raises(RuntimeError, match="not built from the current source files"):
        api.check_store()


def test_a_store_from_before_the_loader_recorded_its_sources_is_reported(client, tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    shutil.copy(api.STORE_FILE, old)
    store = sqlite3.connect(old)
    store.execute("DROP TABLE loaded_from")
    store.commit()
    store.close()
    monkeypatch.setattr(api, "STORE_FILE", old)
    with pytest.raises(RuntimeError, match="store.load_store"):
        api.check_store()


def test_a_missing_source_file_is_reported_with_how_to_fix_it(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SOURCES", tmp_path / "no-such-folder")
    with pytest.raises(RuntimeError, match="store.make_sources"):
        api.check_store()


def test_an_orphan_gate_run_is_refused_by_the_loader(tmp_path):
    sources = tmp_path / "sources"
    shutil.copytree(SOURCES, sources)
    with (sources / "gate_results.jsonl").open("a") as file:
        file.write(
            json.dumps(
                {
                    "submission_id": "SUB-NOBODY",
                    "stage": "build",
                    "status": "failed",
                    "event": "stage_failed",
                    "hours_ago": 1,
                }
            )
            + "\n"
        )
    with pytest.raises(sqlite3.IntegrityError):
        load_store(tmp_path / "store.db", sources)
