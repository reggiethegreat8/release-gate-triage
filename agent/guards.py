"""The guards: plain code that runs after the model and has the last word.

Each guard takes what the model produced and returns (the value to use, a note).
The note is None when the guard changed nothing.
"""

import re

from agent.domain import ALLOWED_ACTIONS

FORBIDDEN_WORDS = re.compile(r"\b(cancel|withdraw|withdrew)", re.IGNORECASE)


def guard_allowlist(triage_class, action):
    """Guard 1: the action must be one the class allows."""
    allowed = ALLOWED_ACTIONS[triage_class]
    if action in allowed:
        return action, None
    return allowed[0], f"allowlist: {action} is not allowed for {triage_class}"


def guard_forced_rules(triage_class, action, context):
    """Guard 2: the three rules the model cannot override.

    The first needs only the class. The other two also read the looked-up
    facts (see context.py). None reads any text a model wrote.
    """
    if triage_class == "nothing_wrong" and action != "none":
        return "none", "forced: nothing_wrong always means none"

    # Someone pulled it on purpose. The rules already call a withdrawn
    # submission nothing_wrong, so this should never fire; it is here so that
    # no other path can ever rerun one.
    if context["submission_state"] == "withdrawn" and action != "none":
        return "none", "forced: the submission was withdrawn, so no action"

    if triage_class == "environment_error" and context["outage"] and action != "escalate_environment":
        others = context["peers_failing"]
        return "escalate_environment", f"forced: {others} others failing, so escalate and do not rerun"

    return action, None


def guard_forbidden_advice(conclusion, triage_class, action):
    """Guard 3: the operator must never be told to cancel or withdraw.

    A word scan cannot tell "cancel it" from "do not cancel it", so it withholds
    both. That is the safe direction to be wrong in.
    """
    if not FORBIDDEN_WORDS.search(conclusion):
        return conclusion, None
    safe_text = (
        f"Classified as {triage_class}. Recommended action: {action}. "
        "The written conclusion was withheld by a safety check."
    )
    return safe_text, "forbidden advice: the conclusion mentioned cancelling or withdrawing"


def apply_guards(triage_class, action, conclusion, context):
    """Run the three guards in order. Returns (action, conclusion, notes)."""
    notes = []

    action, note = guard_allowlist(triage_class, action)
    notes.append(note)
    # The forced rules run after the allowlist, so they always win.
    action, note = guard_forced_rules(triage_class, action, context)
    notes.append(note)
    # The scan runs last, so the safe text names the final action.
    conclusion, note = guard_forbidden_advice(conclusion, triage_class, action)
    notes.append(note)

    return action, conclusion, [note for note in notes if note]
