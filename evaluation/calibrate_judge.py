"""How often the judge agrees with the hand labels.

Run from the repo root with:  uv run python -m evaluation.calibrate_judge
Run label.py first.
"""

import json
import sys

from langchain_core.tracers.langchain import wait_for_all_tracers

from evaluation.judge import judge_conclusion
from evaluation.label import LABELS_FILE
from evaluation.make_judge_items import read_items

if __name__ == "__main__":
    items = read_items()
    if not LABELS_FILE.exists():
        sys.exit(f"{LABELS_FILE} does not exist; label the items first: uv run python -m evaluation.label")
    labels = json.loads(LABELS_FILE.read_text())
    labeled = [item for item in items if item["item_id"] in labels]
    if len(labeled) < len(items):
        print(f"only {len(labeled)} of {len(items)} items are labeled; comparing those")
    items = labeled

    verdicts = [
        judge_conclusion(
            item["issue"],
            item["context"],
            item["triage_class"],
            item["action"],
            item["conclusion"],
            item["tool_calls"],
        )
        for item in items
    ]
    wait_for_all_tracers()

    for criterion in ["grounded", "matches_action"]:
        # Four counts, not one total. Most labels are "pass", so a judge that
        # passed everything would agree most of the time. What matters is how
        # many of the labeled fails it caught, and how many of the labeled passes it failed.
        counts = {(True, True): 0, (False, False): 0, (True, False): 0, (False, True): 0}
        disagreements = []
        for item, verdict in zip(items, verdicts):
            human = labels[item["item_id"]][criterion]
            model = getattr(verdict, criterion)
            counts[(human, model)] += 1
            if human != model:
                reason = getattr(verdict, f"{criterion}_reason")
                disagreements.append(
                    f"  {item['item_id']} ({item['case_id']}, planted: {item['planted']})\n"
                    f"    the label says {human}, the judge said {model}: {reason}"
                )
        labeled_fail = counts[(False, False)] + counts[(False, True)]
        labeled_pass = counts[(True, True)] + counts[(True, False)]
        print(f"\n{criterion}:")
        print(f"  of the {labeled_fail} labeled as failing, the judge failed {counts[(False, False)]}")
        print(f"  of the {labeled_pass} labeled as passing, the judge passed {counts[(True, True)]}")
        print("\n".join(disagreements))
