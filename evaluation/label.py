"""Label the 20 conclusions by hand. Your answers are the truth the
judge is measured against.

Run from the repo root with:  uv run python -m evaluation.label
Answer y or n to each question. Progress is saved after every item, so you can
stop with Ctrl-C and run it again to carry on.
"""

import json
from pathlib import Path

from agent.domain import ACTION_MEANINGS
from agent.prompts import issue_facts
from evaluation.make_judge_items import read_items

LABELS_FILE = Path("data/judge_labels.json")


def ask(question):
    while True:
        answer = input(f"  {question} (y/n) ").strip().lower()
        if answer in ("y", "n"):
            return answer == "y"


if __name__ == "__main__":
    items = read_items()
    labels = json.loads(LABELS_FILE.read_text()) if LABELS_FILE.exists() else {}
    # An item's ID is made from its text, so a label for text that is no longer
    # among the items matches nothing and is dropped here.
    labels = {key: label for key, label in labels.items() if key in {item["item_id"] for item in items}}

    for item in items:
        if item["item_id"] in labels:
            continue
        print("\n" + "=" * 78)
        print(f"{item['item_id']}  ({len(labels) + 1} of {len(items)})")
        # The same facts, in the same words, that the agent and the judge are shown.
        print(issue_facts(item["issue"], item["context"]))
        # What the deep dive looked up, if it ran. The judge is shown these too.
        for call in item["tool_calls"]:
            print(f"\nlooked up {call['name']}({json.dumps(call['args'])}):\n    {call['result']}")
        print(f"\nagent's class:  {item['triage_class']}")
        print(f"agent's action: {item['action']}  ({ACTION_MEANINGS[item['action']]})")
        print(f"conclusion:\n    {item['conclusion']}\n")

        labels[item["item_id"]] = {
            "grounded": ask("Grounded? Every factual claim is supported by what is shown above."),
            "matches_action": ask("Matches the action? What it tells the operator agrees with it."),
        }
        LABELS_FILE.write_text(json.dumps(labels, indent=1))

    print(f"\nall {len(items)} items labeled; saved to {LABELS_FILE}")
