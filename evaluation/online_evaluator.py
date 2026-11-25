"""The online evaluator. This code runs inside LangSmith, not here.

Paste the function below into a Code Evaluator on the release-gate-triage
tracing project. LangSmith then calls it on each new trace and stores the result as
feedback on that trace. It is kept in this file so it is versioned and tested.

An online evaluator gets the run and nothing else: there is no label for live
traffic. So it can only check things that need no label. This one records two:

  guards_quiet   did the answer get through the guards unchanged?
  not_degraded   did every model step work, with no fallback?

It cannot import anything from this repo, and it has no network access.
"""


def perform_eval(run, example=None):
    inputs = run.get("inputs") or {}
    outputs = run.get("outputs") or {}
    # Score only a triage run that started from an issue and reached the guards.
    # A run that errored has no guard_notes, and must not be counted as clean.
    # The second trace made when a paused run is resumed has no issue in its
    # inputs, and must not be counted twice.
    if "issue" not in inputs or "guard_notes" not in outputs:
        return {}
    scores = {"guards_quiet": len(outputs["guard_notes"]) == 0}
    # A run whose output does not say whether a step fell back gets no health
    # score: nothing is known, and "healthy" would be a guess.
    if "degraded_reasons" in outputs:
        scores["not_degraded"] = len(outputs["degraded_reasons"]) == 0
    return scores
