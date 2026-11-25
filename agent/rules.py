"""The rule chain. Plain Python, no model.

The rules make the first call on every issue. They are also the fallback: if
the classify step fails, the rules' class is the one that gets used.
"""

from agent.domain import EARLY_STAGES

# A gate status that means there is nothing to triage any more.
NOT_BROKEN_STATUSES = ("passed", "skipped", "withdrawn")

# The two rules whose answer the model is not asked to confirm. They are the
# operator's first two questions and have no exceptions: "is it still broken?"
# and "is it bigger than this one submission?". Both are decided from facts,
# not from reading text.
CODE_DECIDED = ("not_broken", "outage")


def classify_by_rules(issue, context):
    """Return (class, name of the rule that fired).

    An ordered chain: the first rule that matches wins, so the order matters.
    It reads the status, the stage and the job events from the record, and two
    facts from the context (see context.py). It never reads the log or any
    other text; that is the model's job.
    """
    events = issue["job_events"]

    # A withdrawn submission can still show a stale "running" at the gate, so
    # the store's word counts as well as the gate's.
    if issue["gate_status"] in NOT_BROKEN_STATUSES or context["submission_state"] == "withdrawn":
        return "nothing_wrong", "not_broken"

    if context["outage"]:
        return "environment_error", "outage"

    # The gate only raises an issue about a run that is still going when it is
    # not finishing. This comes before the stage check: a hung build is stuck,
    # wherever it hung.
    if issue["gate_status"] == "running":
        return "stuck", "still_running"

    # Setup failed or the job errored, so the tests never ran and it cannot be
    # the submitted code. Also before the stage check: a build whose machine
    # failed is an environment error.
    if "env_setup_failed" in events or "job_errored" in events:
        return "environment_error", "tests_never_ran"

    # A guess. An early stage can also fail because of the submitted code, and
    # only the log says which. The model is there to read it.
    if issue["stage"] in EARLY_STAGES:
        return "stage_tooling_failure", "in_early_stage"

    # The default always matches, so the chain always answers. It is a guess
    # for anything the checks above did not recognise: most failures at the
    # gate are real failures of the submitted code.
    return "code_defect", "no_other_rule_matched"
