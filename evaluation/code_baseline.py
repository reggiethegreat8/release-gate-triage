"""The operator's policy applied in code: an evaluation aid, not part of the agent.

Once the class is known, most of the policy is a lookup: every fact its lines
turn on is already fetched and compared by the context step (agent/context.py).
coded_action() is that lookup. The results report uses it for three rows:

  - the rules' class, then this lookup: a fairer code-only baseline than the
    fallback the agent uses, which takes the class's default action;
  - the agent's own class, then this lookup: what moving the choice of action
    out of the model and into code would have scored;
  - the true class, then this lookup: a diagnostic of how much of the action
    is a lookup once the class is right.

It was written after the held-out run, from the same policy lines the model
reads (agent/policy.py). Its ticket rule is deliberately crude: any open ticket
from the stage's owners means wait, without reading whether the ticket is about
the same failure. Telling that takes reading, which is why it misses test-20.
"""


def coded_action(triage_class, context):
    """The action the policy's lines give for one class and one set of looked-up facts."""
    if triage_class == "nothing_wrong":
        return "none"
    if triage_class == "code_defect":
        return "cut_new_version" if context["commits_since_failure"] > 0 else "fix_then_rerun"
    if triage_class == "environment_error":
        return "escalate_environment" if context["outage"] else "rerun_same_version"
    if triage_class == "stuck":
        if not context["past_min_hours"]:
            return "wait_and_watch"
        if context["hold_note"] or context["dependency_landed"] is False:
            return "wait_and_watch"
        if context["peers_stuck"] == 0 and context["slow"] and not context["outage"]:
            return "escalate_environment"
        return "wait_and_watch"
    if triage_class == "stage_tooling_failure":
        any_open = any(ticket["state"] == "open" for ticket in context["stage_tickets"])
        return "wait_and_watch" if any_open else "route_to_stage_owner"
    raise ValueError(f"unknown class: {triage_class}")


def with_coded_action(rows, class_from="rule_class"):
    """The saved rows of an experiment, with the action chosen by coded_action().

    class_from says whose class to use: "rule_class" (the rules'), "triage_class"
    (the one the run ended with) or "true_class" (the label's). One row comes back
    for each row given, in the shape the results report counts, so a case run
    several times is still decided by the majority of its runs.
    """
    made = []
    for row in rows:
        label = row["label"]
        triage_class = label["true_class"] if class_from == "true_class" else row[class_from]
        action = coded_action(triage_class, row["context"])
        made.append(
            {
                "case_id": row["case_id"],
                "label": label,
                "rule_name": row["rule_name"],
                "triage_class": triage_class,
                "action": action,
                "scores": {
                    "action_match": [action in label["acceptable_actions"], None],
                    "class_match": [triage_class == label["true_class"], None],
                },
            }
        )
    return made
