"""Upload the cases to LangSmith as one dataset.

Run it again after adding cases: it uploads only the ones that are not there
yet. It never changes or removes an example. If a case that was already
uploaded is edited, run_experiment.py will say so; delete the dataset in
LangSmith and run this again.

Each example carries a split, so an experiment can run on one part:
  dev    the development cases and any added from production
  test   the held-out cases
  probe  the flaky-test probes, which have no label and are not scored

Run from the repo root with:  uv run python -m evaluation.upload_dataset
"""

from collections import Counter

from dotenv import load_dotenv
from langsmith import Client

from store.cases import DEV_FILE, PROBE_FILE, REGRESSION_FILE, TEST_FILE, read_cases

DATASET_NAME = "release-gate-triage"


def examples():
    """Every case as a LangSmith example: what goes in, the answer, and its split."""
    parts = [
        ("dev", read_cases(DEV_FILE)),
        ("dev", read_cases(REGRESSION_FILE, optional=True)),
        ("test", read_cases(TEST_FILE)),
        ("probe", read_cases(PROBE_FILE)),
    ]
    made = []
    for split, cases in parts:
        for case in cases:
            made.append(
                {
                    "inputs": {"issue": case["input"]},  # what the agent is given
                    "outputs": case.get("label"),  # the reference; a probe has none
                    "metadata": {
                        "case_id": case["id"],
                        "kind": case.get("kind", "probe"),
                        "rule": case.get("rule"),
                        "pair": case.get("pair"),
                    },
                    "split": split,
                }
            )
    return made


if __name__ == "__main__":
    load_dotenv()
    client = Client()
    made = examples()
    counts = ", ".join(f"{count} {split}" for split, count in Counter(e["split"] for e in made).items())

    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        there = {example.metadata["case_id"] for example in client.list_examples(dataset_id=dataset.id)}
    else:
        dataset = client.create_dataset(
            dataset_name=DATASET_NAME,
            description="Invented release-gate issues with an operator's labels. Synthetic data.",
        )
        there = set()

    missing = [example for example in made if example["metadata"]["case_id"] not in there]
    if missing:
        client.create_examples(dataset_id=dataset.id, examples=missing)
    print(
        f"local files hold {len(made)} cases ({counts}). {len(there)} were already in "
        f"{DATASET_NAME}; uploaded {len(missing)}."
    )
