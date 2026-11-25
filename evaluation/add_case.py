"""Turn a reviewed production run into a permanent test case.

  uv run python -m evaluation.add_case THREAD TRUE_CLASS ACTION [ACTION ...]

THREAD is the thread ID of a run started with review.py. TRUE_CLASS is what the
problem really was, and the ACTIONs are the ones that would have been
acceptable, as recorded by the person who resolved it. The case is added to the
LangSmith dataset and to data/regression_cases.jsonl. It does not check that
the run was reviewed, or that the issue is new: the person running it has to.
"""

import json
import sys

from agent.domain import ALLOWED_ACTIONS
from store.cases import REGRESSION_FILE


def make_label(true_class, actions):
    """Check the operator's answer against the fixed answer set."""
    if true_class not in ALLOWED_ACTIONS:
        raise ValueError(f"{true_class} is not one of: {', '.join(ALLOWED_ACTIONS)}")
    if not actions:
        raise ValueError("give at least one acceptable action")
    for action in actions:
        if action not in ALLOWED_ACTIONS[true_class]:
            raise ValueError(f"{action} is not allowed for {true_class}")
    return {"true_class": true_class, "acceptable_actions": list(actions)}


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    thread_id, true_class, *actions = sys.argv[1:]
    label = make_label(true_class, actions)

    # Imported here so make_label can be tested without a database or a key.
    from langsmith import Client

    from evaluation.upload_dataset import DATASET_NAME
    from review import config_for, graph

    state = graph.get_state(config_for(thread_id)).values
    if not state:
        sys.exit(f"{thread_id}: no such run")

    case = {"id": f"prod-{thread_id}", "kind": "from_production", "input": state["issue"], "label": label}
    if REGRESSION_FILE.exists() and f'"{case["id"]}"' in REGRESSION_FILE.read_text():
        sys.exit(f"{case['id']} was already added")

    Client().create_example(
        dataset_name=DATASET_NAME,
        inputs={"issue": case["input"]},
        reference_outputs=label,
        split="dev",  # where upload_dataset.py puts these cases, and where the gate looks
        metadata={"case_id": case["id"], "kind": case["kind"]},
        source_run_id=state.get("trace_id"),  # links the example to the trace it came from
    )
    with REGRESSION_FILE.open("a") as file:
        file.write(json.dumps(case) + "\n")
    print(f"added {case['id']} to {DATASET_NAME} and {REGRESSION_FILE}")
