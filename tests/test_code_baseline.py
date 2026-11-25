"""The policy applied in code, and the rows of the results report built from it.

These read the committed experiment files, so they also pin the reported figures.
"""

import json
from pathlib import Path

from evaluation.code_baseline import coded_action, with_coded_action
from evaluation.results import by_case, paired, sign_test, totals

RULES = json.loads(Path("data/experiments/rules-only-test-49856071.json").read_text())
AGENT = json.loads(Path("data/experiments/agent-test-4ec89e8f.json").read_text())
RERUN = json.loads(Path("data/experiments/rerun-agent-test-339bb803.json").read_text())


def context(**changes):
    base = {
        "commits_since_failure": 0,
        "outage": False,
        "past_min_hours": True,
        "hold_note": "",
        "dependency_landed": None,
        "peers_stuck": 0,
        "slow": True,
        "stage_tickets": [],
    }
    return base | changes


def wrong(rows):
    return sorted(case_id for case_id, case in by_case(rows, "action_match").items() if not case["right"])


def test_each_class_gets_the_action_its_policy_lines_give():
    assert coded_action("nothing_wrong", context()) == "none"
    assert coded_action("code_defect", context()) == "fix_then_rerun"
    assert coded_action("code_defect", context(commits_since_failure=2)) == "cut_new_version"
    assert coded_action("environment_error", context()) == "rerun_same_version"
    assert coded_action("environment_error", context(outage=True)) == "escalate_environment"
    assert coded_action("stage_tooling_failure", context()) == "route_to_stage_owner"
    open_ticket = [{"ticket_id": "TKT-1", "state": "open", "text": "x"}]
    closed_ticket = [{"ticket_id": "TKT-1", "state": "closed", "text": "x"}]
    assert coded_action("stage_tooling_failure", context(stage_tickets=open_ticket)) == "wait_and_watch"
    assert (
        coded_action("stage_tooling_failure", context(stage_tickets=closed_ticket)) == "route_to_stage_owner"
    )


def test_a_stuck_run_follows_the_four_stuck_lines_in_order():
    assert coded_action("stuck", context()) == "escalate_environment"
    assert coded_action("stuck", context(past_min_hours=False)) == "wait_and_watch"
    assert coded_action("stuck", context(hold_note="cutoff window")) == "wait_and_watch"
    assert coded_action("stuck", context(dependency_landed=False)) == "wait_and_watch"
    assert coded_action("stuck", context(dependency_landed=True)) == "escalate_environment"
    assert coded_action("stuck", context(peers_stuck=1)) == "wait_and_watch"
    assert coded_action("stuck", context(slow=False)) == "wait_and_watch"


def test_the_committed_files_give_the_reported_rows():
    # The published rows must not move, and the new ones are pinned.
    assert totals(RULES, "action_match") == (27, 45)
    assert totals(AGENT, "action_match") == (44, 45)
    assert totals(RERUN, "action_match") == (43, 45)
    assert totals(with_coded_action(RULES), "action_match") == (36, 45)
    assert totals(with_coded_action(RULES), "class_match") == (37, 45)
    assert totals(with_coded_action(AGENT, "triage_class"), "action_match") == (43, 45)
    assert totals(with_coded_action(RULES, "true_class"), "action_match") == (44, 45)


def test_the_agent_against_the_coded_baseline_is_nine_to_one():
    table = paired(AGENT, with_coded_action(RULES), "action_match")
    assert table == {"both": 35, "first_only": 9, "second_only": 1, "neither": 0}
    assert sign_test(9, 1) == 22 / 1024


def test_what_each_coded_row_gets_wrong():
    # The ticket about a different failure needs reading; a wrong class cannot be recovered.
    assert wrong(with_coded_action(RULES, "true_class")) == ["test-20"]
    assert wrong(with_coded_action(AGENT, "triage_class")) == ["test-20", "test-41"]
    assert wrong(with_coded_action(RERUN, "triage_class")) == ["test-20", "test-41"]


def test_the_replication_run_misses_one_case_code_would_have_got_right():
    # test-27: the facts said another run on the line was stuck; the model escalated anyway.
    assert wrong(RERUN) == ["test-27", "test-41"]


def test_moving_the_action_into_code_costs_one_case_and_removes_the_one_flip():
    coded = with_coded_action(AGENT, "triage_class")
    assert paired(AGENT, coded, "action_match") == {
        "both": 43,
        "first_only": 1,
        "second_only": 0,
        "neither": 1,
    }
    assert not any(case["flipped"] for case in by_case(coded, "action_match").values())


def test_repeated_runs_still_count_each_case_once():
    assert len(with_coded_action(AGENT)) == 135
    assert totals(with_coded_action(RULES + RULES), "action_match") == (36, 45)


def test_the_report_runs_without_a_rules_only_file(capsys, monkeypatch):
    from evaluation import results

    # As on the dev split, where no rules-only run is saved.
    monkeypatch.setattr(results, "newest", lambda variant, split: AGENT if variant == "agent" else None)
    results.report("test")
    out = capsys.readouterr().out
    assert "44 of 45" in out
    assert "rules + policy applied in code" not in out
    assert "agent's class + policy in code" in out
