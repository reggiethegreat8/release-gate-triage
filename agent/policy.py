"""The operator's action policy, in one place.

This is the rulebook an on-call operator follows to choose an action. Three
things read it, so they cannot drift apart:
  - the prompt, so the model is told every rule it is graded on;
  - the labels of the test cases: each case names the line that produced its
    label;
  - the checks on the test cases.

An operator asks these questions in order, and the first that applies decides:
  1. Is it still broken?               (not_broken)
  2. Is it bigger than this submission? (outage)
  3. Did the tests actually run?        (tests_never_ran, test_failed_...)
  4. Is it just not done yet?           (too_early, held_for_reason, stuck_...)
A failure in an early stage's own tooling has two lines of its own
(stage_tool_...).

One more rule has no exceptions and is not a line here, because it never
decides a label: never advise cancelling a run. Code enforces it; see guards.py.
A withdrawn submission is covered by not_broken, and guards.py also refuses any
action on one.
"""

from collections import namedtuple

# The three numbers in the policy. The sentences below are built from them, so
# changing a number changes what the model reads.
OUTAGE_THRESHOLD = 3  # this many other submissions failing on the release line is an outage
MIN_HOURS = 8  # a run is not an issue until it has been this long in one state
SLOW_FACTOR = 2  # "too slow" is more than this many times the stage's typical run time

# One line of the policy: when `text` applies, the problem is `triage_class`
# and the operator takes `action`.
Line = namedtuple("Line", ["triage_class", "action", "text"])

# The order matters. Within one class, the first line that applies wins.
POLICY = {
    "not_broken": Line(
        "nothing_wrong",
        "none",
        "The gate's current status is passed, skipped or withdrawn, or the submission system "
        "says the submission was withdrawn. Do nothing, even if an earlier run failed: the "
        "current status wins.",
    ),
    "outage": Line(
        "environment_error",
        "escalate_environment",
        f"{OUTAGE_THRESHOLD} or more other submissions on the same release line are failing. "
        "Treat it as an outage and escalate once to the environment team. Do not rerun and do "
        "not cut a new version: that runs more jobs into a broken environment.",
    ),
    "tests_never_ran": Line(
        "environment_error",
        "rerun_same_version",
        "The tests never really ran: an environment error, an errored job, a job that was "
        "never picked up, or an environment that broke part-way through. Rerun the same "
        "version. It is not the submitter's code.",
    ),
    "test_failed_new_version": Line(
        "code_defect",
        "cut_new_version",
        "The submitted change failed, and the source already has new commits since the failed "
        "version. Cut a new version.",
    ),
    "test_failed_fix": Line(
        "code_defect",
        "fix_then_rerun",
        "The submitted change failed, and there are no new commits since the failed version. "
        "The code needs a fix, then a rerun.",
    ),
    "too_early": Line(
        "stuck",
        "wait_and_watch",
        f"The run has been under {MIN_HOURS} hours in its current state. It is not an issue yet. Wait.",
    ),
    "held_for_reason": Line(
        "stuck",
        "wait_and_watch",
        "The run is held for a scheduled cutoff window, queued along with everyone else, or "
        "blocked on a dependency that has not landed. Wait.",
    ),
    "stuck_alone_and_slow": Line(
        "stuck",
        "escalate_environment",
        "The run is the only one stuck on its release line, has run more than "
        f"{SLOW_FACTOR} times the typical time for its stage, and there is no outage. "
        "Escalate to the environment team.",
    ),
    "stuck_wait": Line("stuck", "wait_and_watch", "None of the lines above applies. Wait."),
    "stage_tool_ticket_open": Line(
        "stage_tooling_failure",
        "wait_and_watch",
        "The team that owns the stage already has an open ticket for this same failure. Wait.",
    ),
    "stage_tool_route": Line(
        "stage_tooling_failure",
        "route_to_stage_owner",
        "The stage's own tool crashed or rejected its own configuration, and there is no open "
        "ticket for this same failure. Route it to the team that owns the stage.",
    ),
}


def policy_text(triage_class):
    """The policy lines for one class, as the model reads them."""
    lines = [line for line in POLICY.values() if line.triage_class == triage_class]
    numbered = "\n".join(
        f"{number}. {line.text} Action: {line.action}" for number, line in enumerate(lines, start=1)
    )
    return "Work down this list. The first line that applies decides the action.\n" + numbered
