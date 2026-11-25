"""Read the saved experiments and report the results, with their uncertainty.

Run from the repo root with:  uv run python -m evaluation.results SPLIT      (dev or test)

It reads the newest saved run of each variant from data/experiments/ and prints:
  - the primary metric (action_match) and the class, each with an interval;
  - the same on the cases the model actually decides;
  - the policy applied in code (evaluation/code_baseline.py): a fairer
    code-only baseline, and what moving the action into code would score;
  - a later replication run of the unchanged agent, if one is saved;
  - the agent against each other variant, case by case, with a test of
    whether the difference could be chance;
  - the results by class;
  - where the quick look called itself conclusive and was wrong;
  - how often the model overrode the rules;
  - what the agent did on the flaky-test probes.

No model and no network: it only reads files.
"""

import glob
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from agent.rules import CODE_DECIDED
from evaluation.code_baseline import with_coded_action

PRIMARY = "action_match"
# The same names as run_experiment.VARIANTS. Not imported from there: that
# module builds the graphs and a model client, and this one only reads files.
VARIANTS = ["rules-only", "agent", "no-tools", "no-hint"]
# The saved name of the later second run of the unchanged agent.
REPLICATION = "rerun-agent"
# An interval wider than this is reported as "cannot tell": at 40 points it
# no longer separates a good result from a poor one.
TOO_WIDE = 0.40


# --- the two calculations ---


def wilson_interval(right, total, z=1.96):
    """The range the true rate plausibly lies in, given `right` of `total`.

    The Wilson score interval at 95%. Chosen because it behaves for small
    totals and at 0% or 100%, where the simpler formula gives zero width.
    """
    if total == 0:
        return 0.0, 1.0
    rate = right / total
    centre = (rate + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    # Rounding guards the two ends against arithmetic that lands a hair outside 0 to 1.
    return max(0.0, round(centre - half, 12)), min(1.0, round(centre + half, 12))


def sign_test(first_only, second_only):
    """How likely is a split this lopsided if the two systems were equally good?

    An exact two-sided sign test on the cases where exactly one of two systems
    was right. Cases where both were right, or both wrong, say nothing about
    which is better.
    """
    disagreements = first_only + second_only
    if disagreements == 0:
        return 1.0
    fewer = min(first_only, second_only)
    one_side = sum(math.comb(disagreements, k) for k in range(fewer + 1)) / 2**disagreements
    return min(1.0, 2 * one_side)


# --- counting, one answer per case ---


def by_case(rows, score):
    """One result per case. A case run several times is right if most of its
    runs were right, and is marked as flipped if its runs disagreed."""
    runs = defaultdict(list)
    for row in rows:
        runs[row["case_id"]].append(row["scores"][score][0] is True)
    return {
        case_id: {
            "right": sum(results) * 2 > len(results),
            "runs": len(results),
            "flipped": len(set(results)) > 1,
        }
        for case_id, results in runs.items()
    }


def paired(first, second, score):
    """Compare two experiments on the cases they share, case by case."""
    first, second = by_case(first, score), by_case(second, score)
    table = Counter()
    for case_id in first.keys() & second.keys():
        a, b = first[case_id]["right"], second[case_id]["right"]
        table["both" if a and b else "first_only" if a else "second_only" if b else "neither"] += 1
    return {key: table[key] for key in ("both", "first_only", "second_only", "neither")}


def per_class(rows, score):
    """(right, total) for each true class, counting each case once."""
    cases = by_case(rows, score)
    true_class = {row["case_id"]: row["label"]["true_class"] for row in rows}
    counts = defaultdict(lambda: [0, 0])
    for case_id, result in cases.items():
        counts[true_class[case_id]][0] += result["right"]
        counts[true_class[case_id]][1] += 1
    return {name: tuple(count) for name, count in counts.items()}


def conclusive_and_wrong(rows, score):
    """Cases the quick look called conclusive that then came out wrong. The deep
    dive, and its tools, never got a look at these."""
    return sorted(
        {row["case_id"] for row in rows if row.get("conclusive") and row["scores"][score][0] is not True}
    )


def errored_runs(rows):
    """How many runs have a score that could not be computed.

    A score of None means the agent or an evaluator raised on that run. The
    run counts as wrong in the rate of the score that could not be computed,
    and a judge score that is missing leaves the judge's rates covering fewer
    runs. Either way the report says so.
    """
    return sum(any(score[0] is None for score in row["scores"].values()) for row in rows)


def unshared_cases(first, second):
    """The cases only one of two experiments holds. A case-by-case comparison
    leaves these out, so it has to say how many there were."""
    return sorted({row["case_id"] for row in first} ^ {row["case_id"] for row in second})


# --- reading the saved experiments and printing ---


def newest(variant, split):
    """The rows of the newest saved run of one variant on one split, or None.

    Newest by file time, which only matters while working: the repository
    keeps one file per variant.
    """
    files = glob.glob(f"data/experiments/{variant}-{split}-*.json")
    if not files:
        return None
    return json.loads(Path(max(files, key=os.path.getmtime)).read_text())


def show_rate(label, right, total):
    low, high = wilson_interval(right, total)
    note = "   cannot tell" if high - low > TOO_WIDE else ""
    rate = f"{right / total:.0%}" if total else "n/a"
    print(f"  {label:<32} {right:>3} of {total:<3} {rate:>4}   {low:.0%} to {high:.0%}{note}")


def totals(rows, score, keep=lambda row: True):
    cases = by_case([row for row in rows if keep(row)], score)
    return sum(case["right"] for case in cases.values()), len(cases)


def show_policy_in_code(experiments):
    """Three rows where code, not a model, picks the action from the looked-up facts."""
    print("The policy applied in code (no model picks the action):")
    if "rules-only" in experiments:
        show_rate(
            "rules + policy applied in code", *totals(with_coded_action(experiments["rules-only"]), PRIMARY)
        )
    if "agent" in experiments:
        coded = with_coded_action(experiments["agent"], "triage_class")
        show_rate("agent's class + policy in code", *totals(coded, PRIMARY))
    if "rules-only" in experiments:
        coded = with_coded_action(experiments["rules-only"], "true_class")
        show_rate("true class + policy in code*", *totals(coded, PRIMARY))
    print("  * a diagnostic, not a system: it is handed the right class. All three were written")
    print("    after the held-out run.")


def show_score(title, score, experiments):
    """One score for every variant: on all cases, then on the cases the model decides."""

    def model_decides(row):
        return row.get("rule_name") not in CODE_DECIDED

    print(f"\n{title}, all cases:")
    for variant, rows in experiments.items():
        show_rate(variant, *totals(rows, score))
    print(f"{title}, only the cases the model decides:")
    for variant, rows in experiments.items():
        show_rate(variant, *totals(rows, score, model_decides))


def show_replication(experiments, split):
    """The second run of the unchanged agent, if one is saved, beside the fair baseline."""
    replication = newest(REPLICATION, split)
    if not replication:
        return
    print("A second run of the unchanged agent, made later (one run per case):")
    show_rate("agent, replication run", *totals(replication, PRIMARY))
    if "rules-only" in experiments:
        table = paired(replication, with_coded_action(experiments["rules-only"]), PRIMARY)
        chance = sign_test(table["first_only"], table["second_only"])
        print(
            f"    against rules + policy in code: replication only {table['first_only']}, "
            f"rules+policy only {table['second_only']}   chance: {chance:.3f}"
        )
    show_rate(
        "  its class + policy in code", *totals(with_coded_action(replication, "triage_class"), PRIMARY)
    )


def report(split):
    experiments = {variant: rows for variant in VARIANTS if (rows := newest(variant, split))}
    if not experiments:
        sys.exit(f"no saved experiments for the {split} split in data/experiments/")

    print(f"\nRESULTS ON THE {split.upper()} SPLIT   (rate, then the 95% interval)")
    for variant, rows in experiments.items():
        if errored_runs(rows):
            print(
                f"  WARNING {variant}: {errored_runs(rows)} of {len(rows)} runs have a score that "
                "could not be computed. They count as wrong on that score below."
            )
    show_score("Right action (the primary metric)", PRIMARY, experiments)
    show_policy_in_code(experiments)
    show_replication(experiments, split)
    show_score("Right class", "class_match", experiments)

    if "agent" in experiments:
        agent = experiments["agent"]
        print(f"\nThe agent against each other variant, case by case, on {PRIMARY}:")
        others = {variant: rows for variant, rows in experiments.items() if variant != "agent"}
        if "rules-only" in experiments:
            others["rules+policy"] = with_coded_action(experiments["rules-only"])
        for variant, rows in others.items():
            table = paired(agent, rows, PRIMARY)
            left_out = unshared_cases(agent, rows)
            if left_out:
                print(
                    f"  WARNING agent vs {variant}: {len(left_out)} cases are in only one of the two "
                    f"and are left out: {left_out}"
                )
            chance = sign_test(table["first_only"], table["second_only"])
            verdict = "a real difference" if chance < 0.05 else "could be chance"
            print(
                f"  agent vs {variant:<11} both right {table['both']:>2}, agent only "
                f"{table['first_only']:>2}, {variant} only {table['second_only']:>2}, neither "
                f"{table['neither']:>2}   chance of a split this uneven: {chance:.3f} ({verdict})"
            )

        print(f"\nThe agent by class, on {PRIMARY}:")
        for name, (right, total) in sorted(per_class(agent, PRIMARY).items()):
            show_rate(name, right, total)

        cases = by_case(agent, PRIMARY)
        runs = max(case["runs"] for case in cases.values())
        flipped = sorted(case_id for case_id, case in cases.items() if case["flipped"])
        print(
            f"\nRuns per case: {runs}. Cases whose runs disagreed with each other: "
            f"{len(flipped)} {flipped if flipped else ''}"
        )

        wrong = sorted(case_id for case_id, case in cases.items() if not case["right"])
        confident = conclusive_and_wrong(agent, PRIMARY)
        print(f"Wrong action: {len(wrong)} {wrong if wrong else ''}")
        print(
            "  of those, the quick look had called itself conclusive on: "
            f"{confident if confident else 'none'}"
        )
        deep_dives = {row["case_id"] for row in agent if row.get("deep_dive")}
        print(f"Deep dives: {len(deep_dives)} of {len(cases)} cases")

        asked = [row for row in agent if row.get("model_class")]
        overrode = sum(row["model_class"] != row["rule_class"] for row in asked)
        overruled = sum(row["model_class"] != row["triage_class"] for row in asked)
        print(
            f"The model answered for the class on {len(asked)} runs. It overrode the rules on "
            f"{overrode}. Code overruled the model on {overruled}."
        )

        judged = [row for row in agent if row["scores"].get("conclusion_judged", [False])[0] is True]
        if judged:
            for key in ("grounded", "conclusion_matches_action"):
                show_rate(f"judge: {key}", sum(row["scores"][key][0] is True for row in judged), len(judged))

    probes = newest("agent", "probe")
    if probes:
        print("\nThe flaky-test probes (no right answer; this is what the agent did):")
        for row in sorted(probes, key=lambda row: row["case_id"]):
            print(
                f"  {row['case_id']}: {row['triage_class']} / {row['action']}   deep dive: {row['deep_dive']}"
            )


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else "test")
