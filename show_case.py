"""Print one case so a person can read it: for checking a case by hand.

  uv run python show_case.py test-07        one case
  uv run python show_case.py test           every held-out case, one after another
  uv run python show_case.py dev            every development case
  uv run python show_case.py live-02        one of the unlabeled "live" issues

It shows the record the agent is handed, then the history that goes into the
store, then the answer. The answer is last so you can decide for yourself first.
"""

import json
import sys

from store.cases import DEV_FILE, LIVE_FILE, PROBE_FILE, TEST_FILE, read_cases


def show(case):
    record, store = case["input"], case.get("store", {})
    print("=" * 100)
    pair = f"   pair: {case['pair']}" if "pair" in case else ""
    print(f"{case['id']}   ({case.get('kind', 'no label')}){pair}")
    print("-" * 100)
    print("THE RECORD (all the agent is handed)")
    print(
        f"  status: {record['gate_status']}   stage: {record['stage']}   "
        f"hours in status: {record['hours_in_status']}   submission: {record['submission_id']}"
    )
    print(f"  events: {', '.join(record['job_events'])}")
    print("  log:")
    for line in record["log_excerpt"].splitlines():
        print(f"    {line}")
    print("\nTHE HISTORY (in the store; the agent has to look it up)")
    for key, value in store.items():
        if isinstance(value, list):
            print(f"  {key}:")
            for item in value:
                print(f"    {json.dumps(item)}")
        else:
            print(f"  {key}: {value}")
    if not store:
        print("  (nothing beyond the defaults)")
    if "label" in case:
        print("\nTHE ANSWER")
        print(f"  cause: {case['cause']}")
        print(f"  rule:  {case['rule']}")
        print(f"  label: {case['label']['true_class']} / {', '.join(case['label']['acceptable_actions'])}")
    print()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    wanted = sys.argv[1]
    files = {"dev": DEV_FILE, "test": TEST_FILE, "probe": PROBE_FILE}
    if wanted in files:
        chosen = read_cases(files[wanted])
    else:
        everything = [case for path in [*files.values(), LIVE_FILE] for case in read_cases(path)]
        chosen = [case for case in everything if case["id"] == wanted]
        if not chosen:
            sys.exit(f"no case is called {wanted}")
    for case in chosen:
        show(case)
