"""Run one experiment on the LangSmith dataset and score it.

Run from the repo root with:  uv run python -m evaluation.run_experiment VARIANT SPLIT [REPEATS [NAME]]

  VARIANT  rules-only  code only: the rules' class, its default action, the guards. No model.
           agent       the full agent
           no-tools    the agent, with the deep dive's lookup tools switched off
           no-hint     the agent, with the rules' pick kept out of the classify prompt
  SPLIT    dev, test or probe
  REPEATS  how many times to run each case (default 1)
  NAME     the experiment's name in LangSmith, if not VARIANT-SPLIT
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from langsmith import Client

from agent.context import fetch_context
from agent.domain import ALLOWED_ACTIONS
from agent.graph import build_graph
from agent.guards import apply_guards
from agent.rules import classify_by_rules
from evaluation.evaluators import CODE_EVALUATORS, action_match, class_match
from evaluation.judge import conclusion_judge
from evaluation.upload_dataset import DATASET_NAME, examples
from store.api import check_store

# The agent's three variants. Each is the same graph with one switch changed.
GRAPHS = {
    "agent": build_graph(),
    "no-tools": build_graph(tools=False),
    "no-hint": build_graph(rules_hint=False),
}
VARIANTS = ["rules-only", *GRAPHS]
SPLITS = ["dev", "test", "probe"]


def triage(inputs, variant="agent"):
    """The target: what LangSmith calls once per example, with its inputs."""
    result = GRAPHS[variant].invoke({"issue": inputs["issue"]})
    return {
        "triage_class": result["triage_class"],
        "action": result["action"],
        "conclusion": result["conclusion"],
        "rule_class": result["rule_class"],
        "rule_name": result["rule_name"],
        "model_class": result.get("model_class"),  # what the model said before code had its say
        "context": result["context"],  # the looked-up facts, for the evaluators
        "conclusive": result["conclusive"],  # what the quick look said about itself
        "deep_dive": result.get("deep_dive", False),
        "tool_calls": result.get("tool_calls", []),
        "degraded_reasons": result["degraded_reasons"],
        "guard_notes": result["guard_notes"],
    }


def rules_only(inputs):
    """The baseline: everything the system does without a model.

    The looked-up facts, the rules' class, that class's default action, and
    the guards. An operator gets the same class and action from the full agent
    when every model step fails.
    """
    context = fetch_context(inputs["issue"])
    rule_class, rule_name = classify_by_rules(inputs["issue"], context)
    action, conclusion, notes = apply_guards(
        rule_class, ALLOWED_ACTIONS[rule_class][0], f"Classified as {rule_class} by rule.", context
    )
    return {
        "triage_class": rule_class,
        "action": action,
        "conclusion": conclusion,
        "rule_class": rule_class,
        "rule_name": rule_name,
        "model_class": None,
        "context": context,
        "conclusive": True,
        "deep_dive": False,
        "tool_calls": [],
        "degraded_reasons": [],
        "guard_notes": notes,
    }


def evaluators_for(variant, split):
    """Which scores make sense for this run.

    A probe has no label, so nothing that reads the label. The rules write no
    conclusion of their own, so there is nothing for the judge to judge.
    """
    chosen = list(CODE_EVALUATORS)
    if split == "probe":
        chosen = [evaluator for evaluator in chosen if evaluator not in (action_match, class_match)]
    if variant != "rules-only":
        chosen.append(conclusion_judge)
    return chosen


def dataset_problems(local, remote):
    """Compare the local cases of one split with the dataset's examples.

    local and remote are lists of {"case_id", "inputs", "outputs"}. Returns
    what differs, as sentences. The record and the label of an experiment come
    from LangSmith, and the looked-up facts come from the local store, so if
    the two drift apart the experiment scores one against the other with no
    error. This is the check that stops that.
    """
    local = {example["case_id"]: example for example in local}
    remote = {example["case_id"]: example for example in remote}
    problems = [
        f"{case_id} is in the local files and not in the dataset"
        for case_id in sorted(set(local) - set(remote))
    ]
    problems += [
        f"{case_id} is in the dataset and not in the local files"
        for case_id in sorted(set(remote) - set(local))
    ]
    for case_id in sorted(set(local) & set(remote)):
        for part in ("inputs", "outputs"):
            if local[case_id][part] != remote[case_id][part]:
                problems.append(f"{case_id}: the {part} differ between the local files and the dataset")
    return problems


def average_scores(scores_per_run):
    """Average each score over the runs. Returns (averages, errors).

    scores_per_run is one list of scores for each run. A score of None means
    the agent or an evaluator raised on that run. Those are not averaged; they
    are returned as errors, so a failure can never hide inside a smaller total.
    """
    values = defaultdict(list)
    errors = []
    for scores in scores_per_run:
        for score in scores:
            if score.score is None:
                errors.append(f"{score.key} could not be computed: {score.comment}")
            else:
                values[score.key].append(score.score)
    averages = {key: sum(numbers) / len(numbers) for key, numbers in values.items()}
    return averages, errors


def failed_production_cases(runs):
    """Return the IDs of cases that came from production and got the wrong action.

    runs is one (example metadata, scores) pair for each run. These cases are
    failures already seen once in real use, so the gate allows them no tolerance.
    """
    failed = []
    for metadata, scores in runs:
        if metadata.get("kind") != "from_production":
            continue
        action_right = any(score.key == "action_match" and score.score is True for score in scores)
        if not action_right:
            failed.append(metadata["case_id"])
    return failed


def run_experiment(variant="agent", split="dev", repeats=1, prefix=None):
    """Run every example of one split through one variant and score each run.

    Returns (experiment name, average of each score, errors, failed production cases).
    """
    # Checked once here. Without it, a missing or out-of-date store would fail
    # every example separately, and the reason would be buried in each one.
    check_store()

    client = Client()
    remote = list(client.list_examples(dataset_name=DATASET_NAME, splits=[split]))
    problems = dataset_problems(
        [
            {"case_id": e["metadata"]["case_id"], "inputs": e["inputs"], "outputs": e["outputs"]}
            for e in examples()
            if e["split"] == split
        ],
        [
            # A probe has no label: LangSmith gives back {} where the local file has None.
            {"case_id": e.metadata["case_id"], "inputs": e.inputs, "outputs": e.outputs or None}
            for e in remote
        ],
    )
    if problems:
        sys.exit(
            "the LangSmith dataset and the local case files disagree:\n  "
            + "\n  ".join(problems)
            + "\nrun: uv run python -m evaluation.upload_dataset   to add missing cases; "
            "for a changed case, delete the dataset in LangSmith first"
        )

    target = rules_only if variant == "rules-only" else (lambda inputs: triage(inputs, variant))
    results = client.evaluate(
        target,
        data=remote,
        evaluators=evaluators_for(variant, split),
        experiment_prefix=prefix or f"{variant}-{split}",
        num_repetitions=repeats,
        max_concurrency=4,
        metadata={
            "variant": variant,
            "split": split,
            "repeats": repeats,
            "model": os.environ["TRIAGE_MODEL"],
            "judge_model": os.environ["JUDGE_MODEL"],
        },
    )

    # Each row holds one example, its run, and the scores the evaluators gave.
    rows = list(results)
    averages, errors = average_scores([row["evaluation_results"]["results"] for row in rows])
    production_failures = failed_production_cases(
        [(row["example"].metadata, row["evaluation_results"]["results"]) for row in rows]
    )

    # A local copy of what the agent wrote and how it scored, named after the
    # LangSmith experiment, so experiments can be searched and compared as
    # text. Only the files behind the reported results are committed; see
    # .gitignore.
    saved = []
    for row in rows:
        scores = {s.key: [s.score, s.comment] for s in row["evaluation_results"]["results"]}
        saved.append(
            row["example"].metadata
            | (row["run"].outputs or {})
            | {"label": row["example"].outputs, "scores": scores, "variant": variant, "split": split}
        )
    folder = Path("data/experiments")
    folder.mkdir(exist_ok=True)
    (folder / f"{results.experiment_name}.json").write_text(json.dumps(saved, indent=1))

    return results.experiment_name, averages, errors, production_failures


def print_scores(experiment_name, averages, errors, production_failures):
    print(f"\nexperiment: {experiment_name}")
    for key, average in sorted(averages.items()):
        print(f"  {key:<22} {average:.0%}")
    for error in errors:
        print(f"  ERROR {error}")
    for case_id in production_failures:
        print(f"  WRONG ACTION on a case from production: {case_id}")


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if len(arguments) < 2 or arguments[0] not in VARIANTS or arguments[1] not in SPLITS:
        sys.exit(__doc__)
    repeats = int(arguments[2]) if len(arguments) > 2 else 1
    name = arguments[3] if len(arguments) > 3 else None
    print_scores(*run_experiment(arguments[0], arguments[1], repeats, prefix=name))
