"""The context step: the facts the operator's policy needs, fetched from the
store by code, with the number comparisons already done. No model is involved."""

import pytest

from agent.context import fetch_context
from agent.policy import MIN_HOURS, OUTAGE_THRESHOLD, SLOW_FACTOR
from store import api
from store.cases import STORE_DEFAULTS, cases_with_history
from store.load_store import load_store
from store.make_sources import TYPICAL_HOURS

CASES = cases_with_history()


@pytest.fixture(scope="module", autouse=True)
def store(tmp_path_factory):
    store_file = tmp_path_factory.mktemp("store") / "store.db"
    load_store(store_file)
    with pytest.MonkeyPatch.context() as patch:  # put back when this file's tests end
        patch.setattr(api, "STORE_FILE", store_file)
        yield


def history(case, key):
    return case["store"].get(key, STORE_DEFAULTS[key])


# --- facts copied from the store ---


def test_the_submission_s_own_facts_come_from_its_row():
    for case in CASES:
        context = fetch_context(case["input"])
        assert context["submission_state"] == history(case, "state"), case["id"]
        assert context["commits_since_failure"] == history(case, "commits_since_failure")
        assert context["hold_note"] == history(case, "hold_note")
        assert context["dependency_landed"] == history(case, "dependency_landed")


def test_the_stage_owner_s_tickets_are_included_with_their_state():
    for case in CASES:
        wanted = [
            (ticket["state"], ticket["text"])
            for ticket in history(case, "tickets")
            if ticket["on"] == "stage"
        ]
        found = fetch_context(case["input"])["stage_tickets"]
        assert [(ticket["state"], ticket["text"]) for ticket in found] == wanted, case["id"]


# --- the comparisons, done in code ---


def test_the_peers_are_counted_and_this_submission_is_not_one_of_them():
    for case in CASES:
        peers = history(case, "peers")
        context = fetch_context(case["input"])
        assert context["peers_total"] == len(peers), case["id"]
        assert context["peers_failing"] == sum(peer["status"] == "failed" for peer in peers)
        assert context["peers_running"] == sum(peer["status"] == "running" for peer in peers)


def test_an_outage_is_the_threshold_or_more_failing_peers():
    seen = set()
    for case in CASES:
        context = fetch_context(case["input"])
        assert context["outage"] == (context["peers_failing"] >= OUTAGE_THRESHOLD), case["id"]
        seen.add(context["outage"])
    assert seen == {True, False}  # the cases exercise both sides


def test_a_peer_counts_as_stuck_only_when_it_has_run_past_the_floor():
    # dev-05 is held with two peers, both running for well over the floor.
    held = next(case for case in CASES if case["id"] == "dev-05")
    assert fetch_context(held["input"])["peers_stuck"] == 2
    # dev-03 has one peer that has been running for an hour.
    outage = next(case for case in CASES if case["id"] == "dev-03")
    assert fetch_context(outage["input"])["peers_running"] == 1
    assert fetch_context(outage["input"])["peers_stuck"] == 0


def test_the_hours_are_compared_with_the_floor_and_the_typical_run_time():
    for case in CASES:
        record = case["input"]
        typical = TYPICAL_HOURS[record["stage"]]
        context = fetch_context(record)
        assert context["typical_hours"] == typical
        assert context["times_typical"] == round(record["hours_in_status"] / typical, 1)
        assert context["slow"] == (record["hours_in_status"] > SLOW_FACTOR * typical), case["id"]
        assert context["past_min_hours"] == (record["hours_in_status"] >= MIN_HOURS), case["id"]


# --- when the store cannot answer, the run stops ---


def test_a_submission_that_is_not_in_the_store_is_an_error_that_names_it():
    record = CASES[0]["input"] | {"submission_id": "SUB-NOBODY"}
    with pytest.raises(RuntimeError, match="SUB-NOBODY"):
        fetch_context(record)


def test_a_stage_the_store_does_not_know_is_an_error_too():
    record = CASES[0]["input"] | {"stage": "deploy"}
    with pytest.raises(RuntimeError, match="deploy"):
        fetch_context(record)


def test_a_missing_store_is_reported_with_how_to_fix_it(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "STORE_FILE", tmp_path / "no-such-store.db")
    with pytest.raises(RuntimeError, match="store.load_store"):
        fetch_context(CASES[0]["input"])


# --- the two answers decided by code come out right with no model at all ---


def test_the_cases_whose_answer_is_decided_by_code_are_right_by_the_rules_alone():
    from agent.rules import CODE_DECIDED, classify_by_rules

    decided = [case for case in CASES if case.get("rule") in CODE_DECIDED]
    assert {case["rule"] for case in decided} == set(CODE_DECIDED)  # the cases cover both
    for case in decided:
        triage_class, rule_name = classify_by_rules(case["input"], fetch_context(case["input"]))
        assert (triage_class, rule_name) == (case["label"]["true_class"], case["rule"]), case["id"]


def test_no_other_case_is_taken_out_of_the_model_s_hands():
    from agent.rules import CODE_DECIDED, classify_by_rules

    for case in CASES:
        if "rule" in case and case["rule"] not in CODE_DECIDED:
            _, rule_name = classify_by_rules(case["input"], fetch_context(case["input"]))
            assert rule_name not in CODE_DECIDED, case["id"]


# --- the boundaries, on a small store written for the purpose ---
# The cases in the files do not sit on every boundary. These do, and every expected
# value below is written out, not computed with the same formula as the code.


def record(submission_id, hours, status="running", stage="integration_tests"):
    return {
        "submission_id": submission_id,
        "gate_status": status,
        "stage": stage,
        "hours_in_status": hours,
        "job_events": ["job_started"],
        "log_excerpt": "log",
    }


def case(case_id, record, **store):
    return {"id": case_id, "input": record, "store": store}


@pytest.fixture
def small_store(tmp_path, monkeypatch):
    """Build a store from a handful of hand-written cases. Returns their records by id."""
    from store.make_sources import write_sources

    cases = [
        # integration_tests typically takes 3.0 hours, so twice that is 6.
        case("six", record("SUB-A", 6)),
        case("seven", record("SUB-B", 7)),
        case(
            "eight",
            record("SUB-C", 8),
            peers=[
                {"status": "running", "hours_ago": 8},  # exactly on the floor: stuck
                {"status": "running", "hours_ago": 7},  # under it: running, not stuck
                {"status": "failed", "hours_ago": 9},  # old, but it is not running
                {"status": "passed"},
            ],
        ),
        case("commits", record("SUB-D", 1, status="failed"), commits_since_failure=2),
        case("landed", record("SUB-E", 9), dependency_landed=True),
        case("not-landed", record("SUB-F", 9), dependency_landed=False),
        case(
            "tickets",
            record("SUB-G", 2, status="failed", stage="build"),
            tickets=[
                {"on": "submission", "state": "open", "text": "on the submission"},
                {"on": "stage", "state": "closed", "text": "closed stage ticket"},
                {"on": "stage", "state": "open", "text": "open stage ticket"},
            ],
        ),
        case("same-stage-no-tickets", record("SUB-H", 2, status="failed", stage="build")),
    ]
    sources = tmp_path / "sources"
    write_sources(sources, cases)
    load_store(tmp_path / "store.db", sources)
    monkeypatch.setattr(api, "STORE_FILE", tmp_path / "store.db")
    monkeypatch.setattr(api, "SOURCES", sources)
    return {made["id"]: made["input"] for made in cases}


def test_exactly_eight_hours_is_past_the_floor_and_seven_is_not(small_store):
    assert fetch_context(small_store["eight"])["past_min_hours"] is True
    assert fetch_context(small_store["seven"])["past_min_hours"] is False


def test_exactly_twice_the_typical_time_is_not_slow_and_a_little_more_is(small_store):
    six, seven = fetch_context(small_store["six"]), fetch_context(small_store["seven"])
    assert (six["times_typical"], six["slow"]) == (2.0, False)
    assert (seven["times_typical"], seven["slow"]) == (2.3, True)


def test_a_peer_is_stuck_only_if_it_is_running_and_has_reached_the_floor(small_store):
    context = fetch_context(small_store["eight"])
    assert context["peers_total"] == 4
    assert context["peers_running"] == 2
    assert context["peers_stuck"] == 1
    assert context["peers_failing"] == 1


def test_new_commits_survive_from_the_case_to_the_context(small_store):
    assert fetch_context(small_store["commits"])["commits_since_failure"] == 2


def test_a_dependency_that_landed_or_did_not_survives_the_files_the_store_and_the_api(small_store):
    assert fetch_context(small_store["landed"])["dependency_landed"] is True
    assert fetch_context(small_store["not-landed"])["dependency_landed"] is False
    assert fetch_context(small_store["six"])["dependency_landed"] is None


def test_a_ticket_on_the_submission_is_not_a_stage_ticket_and_a_closed_one_keeps_its_state(small_store):
    found = fetch_context(small_store["tickets"])["stage_tickets"]
    assert [(ticket["state"], ticket["text"]) for ticket in found] == [
        ("closed", "closed stage ticket"),
        ("open", "open stage ticket"),
    ]


def test_a_ticket_on_the_submission_is_what_the_ticket_tool_returns(small_store):
    listed = api.list_tickets("SUB-G")
    assert [ticket["text"] for ticket in listed] == ["on the submission"]


def test_a_stage_ticket_is_not_shown_to_another_case_in_the_same_stage(small_store):
    assert fetch_context(small_store["same-stage-no-tickets"])["stage_tickets"] == []


# --- the stand-in the other tests use must look like the real thing ---


def test_the_stand_in_context_has_exactly_the_keys_the_real_one_returns():
    from fakes import context as stand_in

    assert set(stand_in()) == set(fetch_context(CASES[0]["input"]))
