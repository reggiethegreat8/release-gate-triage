"""The experiment runner's own logic: the code-only baseline, which scores
apply to which run, and the check that the dataset matches the local files.
No model and no network."""

from evaluation import run_experiment
from evaluation.evaluators import action_match, class_match
from evaluation.judge import conclusion_judge
from evaluation.run_experiment import dataset_problems, evaluators_for, rules_only
from fakes import context, issue


def use_context(monkeypatch, **changes):
    monkeypatch.setattr(run_experiment, "fetch_context", lambda record: context(**changes))


# --- the baseline: everything the system does without a model ---


def test_rules_only_gives_the_rules_class_and_that_class_s_default_action(monkeypatch):
    use_context(monkeypatch)
    output = rules_only({"issue": issue(gate_status="running")})
    assert (output["triage_class"], output["rule_name"]) == ("stuck", "still_running")
    assert output["action"] == "wait_and_watch"


def test_rules_only_still_goes_through_the_guards(monkeypatch):
    # An outage: the rules say environment_error, whose default action is to
    # rerun, and the guard escalates, as it would for the full agent.
    use_context(monkeypatch, outage=True, peers_failing=3)
    output = rules_only({"issue": issue()})
    assert (output["triage_class"], output["action"]) == ("environment_error", "escalate_environment")


def test_rules_only_returns_every_key_the_evaluators_read(monkeypatch):
    use_context(monkeypatch)
    output = rules_only({"issue": issue()})
    for key in (
        "triage_class",
        "action",
        "conclusion",
        "context",
        "tool_calls",
        "degraded_reasons",
        "guard_notes",
        "conclusive",
    ):
        assert key in output


# --- which scores apply ---


def test_the_rules_are_not_judged_because_they_write_no_conclusion():
    assert conclusion_judge not in evaluators_for("rules-only", "dev")
    assert conclusion_judge in evaluators_for("agent", "dev")


def test_a_probe_is_not_scored_against_a_label_it_does_not_have():
    chosen = evaluators_for("agent", "probe")
    assert action_match not in chosen and class_match not in chosen
    assert action_match in evaluators_for("agent", "test")


# --- the dataset must match the local files ---


def example(case_id, hours=2, action="wait_and_watch"):
    return {
        "case_id": case_id,
        "inputs": {"issue": issue(hours_in_status=hours)},
        "outputs": {"true_class": "stuck", "acceptable_actions": [action]},
    }


def test_matching_files_and_dataset_have_no_problems():
    assert dataset_problems([example("a"), example("b")], [example("b"), example("a")]) == []


def test_a_case_missing_on_either_side_is_named():
    assert dataset_problems([example("a"), example("b")], [example("a")]) == [
        "b is in the local files and not in the dataset"
    ]
    assert dataset_problems([example("a")], [example("a"), example("c")]) == [
        "c is in the dataset and not in the local files"
    ]


def test_an_edited_record_or_label_is_named():
    assert dataset_problems([example("a", hours=3)], [example("a")]) == [
        "a: the inputs differ between the local files and the dataset"
    ]
    assert dataset_problems([example("a", action="escalate_environment")], [example("a")]) == [
        "a: the outputs differ between the local files and the dataset"
    ]


def test_the_report_and_the_runner_name_the_same_variants():
    from evaluation import results

    assert results.VARIANTS == run_experiment.VARIANTS
