"""Build the 20 conclusions used to check the judge against a person. Run once.

Run from the repo root with:  uv run python -m evaluation.make_judge_items

14 are real conclusions: the agent is run on 14 development cases. 6 are
written by hand to be wrong in a known way, because a judge can only be tested
on catching failures if the sample holds some.

Each item keeps everything the judge is shown: the record, the looked-up facts,
the lookups the deep dive made, the class, the action and the conclusion. An
item's ID is made from its text, so a label can never be matched to a
conclusion it was not given for.
"""

import hashlib
import json
import random
import sys
from pathlib import Path

from store.cases import DEV_FILE, read_cases

ITEMS_FILE = Path("data/judge_items.jsonl")

# Development cases the agent is run on: every class the judge scores, both
# easy and hard, and one outage. No case where nothing is wrong: the judge
# does not score the fixed sentence the code writes for those.
REAL = [
    "dev-02", "dev-04", "dev-05", "dev-07", "dev-09", "dev-10", "dev-12",
    "dev-16", "dev-18", "dev-19", "dev-21", "dev-23", "dev-26", "dev-28",
]  # fmt: skip

# (case, what is wrong with it, class, action, conclusion)
#   ungrounded  the action is right, but the text states a detail found nowhere
#               in the record or the looked-up facts
#   mismatch    the text is true to the facts, but tells the operator to do
#               something other than the action
PLANTED = [
    (
        "dev-01", "ungrounded", "code_defect", "fix_then_rerun",
        "The integration gate failed on test_half_cent_rounds_up, which expected 12.35 and got 12.34. "
        "This was caused by the rounding helper the payments team merged in commit 4f2a9c last night. "
        "Fix the rounding in the submission and run the gate again.",
    ),
    (
        "dev-15", "ungrounded", "environment_error", "rerun_same_version",
        "The runner agent was killed for lack of memory while unpacking the test image, so no test ran. "
        "Host r-14 has had a faulty memory module since Tuesday and the infrastructure team is replacing "
        "it today. Run the same version again.",
    ),
    (
        "dev-22", "ungrounded", "stuck", "escalate_environment",
        "The run has printed nothing for eight hours while waiting on settlement-stub. The stub was taken "
        "down for a certificate renewal at 03:00 and was never restarted. Escalate to the environment team.",
    ),
    (
        "dev-06", "mismatch", "stage_tooling_failure", "route_to_stage_owner",
        "The link checker stopped before reading any input because its own configuration holds an option "
        "it does not know, '--strict-links'. Nothing in the submission was checked. Run the same version "
        "again and it should pass.",
    ),
    (
        "dev-14", "mismatch", "code_defect", "cut_new_version",
        "The release scenario monthly_export_matches_schema failed because the export row no longer has "
        "the required field 'tax_region', and the file that builds the row was changed in this submission. "
        "Hand this to the environment team to look at staging-7.",
    ),
    (
        "dev-24", "mismatch", "stuck", "escalate_environment",
        "The release run has been silent in the scenario year_end_rollover for nine hours, and it has now "
        "run for 14 hours, more than twice the usual six. No other run on the line is stuck. Leave it "
        "alone for now and keep watching it.",
    ),
]  # fmt: skip


def item_id(conclusion):
    """A short code made from the text being judged, so a label belongs to
    exactly that text."""
    return "item-" + hashlib.sha256(conclusion.encode()).hexdigest()[:8]


def read_items():
    """The saved items, in the order they are to be labeled."""
    if not ITEMS_FILE.exists():
        sys.exit(f"{ITEMS_FILE} does not exist; run: uv run python -m evaluation.make_judge_items")
    return [json.loads(line) for line in ITEMS_FILE.read_text().splitlines()]


if __name__ == "__main__":
    # The labels in data/judge_labels.json belong to the text already built.
    # Running the agent again would write different text.
    if ITEMS_FILE.exists():
        sys.exit(f"{ITEMS_FILE} exists; delete it and data/judge_labels.json to rebuild")

    from agent.context import fetch_context
    from agent.graph import graph  # here, so label.py can import this file without a model

    cases = {case["id"]: case for case in read_cases(DEV_FILE)}
    missing = sorted({case_id for case_id in REAL + [item[0] for item in PLANTED]} - set(cases))
    if missing:
        sys.exit(f"these cases are not in {DEV_FILE}: {', '.join(missing)}")

    items = []
    results = graph.batch(
        [{"issue": cases[case_id]["input"]} for case_id in REAL], config={"max_concurrency": 4}
    )
    for case_id, result in zip(REAL, results):
        if result["degraded_reasons"]:
            sys.exit(f"{case_id}: a model step failed ({result['degraded_reasons']}); nothing was written")
        items.append(
            {
                "case_id": case_id,
                "planted": None,
                "issue": cases[case_id]["input"],
                "context": result["context"],
                "tool_calls": result.get("tool_calls", []),
                "triage_class": result["triage_class"],
                "action": result["action"],
                "conclusion": result["conclusion"],
            }
        )

    for case_id, flaw, triage_class, action, conclusion in PLANTED:
        items.append(
            {
                "case_id": case_id,
                "planted": flaw,
                "issue": cases[case_id]["input"],
                "context": fetch_context(cases[case_id]["input"]),
                "tool_calls": [],
                "triage_class": triage_class,
                "action": action,
                "conclusion": conclusion,
            }
        )

    random.Random(57).shuffle(items)  # so the planted ones are not all at the end
    for item in items:
        item["item_id"] = item_id(item["conclusion"])

    ITEMS_FILE.write_text("".join(json.dumps(item) + "\n" for item in items))
    print(f"wrote {len(items)} items to {ITEMS_FILE}")
