"""The fixed answer set: five classes and the actions allowed for each.

The guards, the evaluators and the model's answer schemas all read this one
table. The rules read only the list of early stages.
Which action an operator picks, and the thresholds, are in policy.py.
"""

# class -> actions that are allowed for it. The first one is the default.
ALLOWED_ACTIONS = {
    "code_defect": ["fix_then_rerun", "cut_new_version"],
    "stuck": ["wait_and_watch", "escalate_environment"],
    "environment_error": ["rerun_same_version", "escalate_environment"],
    "stage_tooling_failure": ["route_to_stage_owner", "wait_and_watch"],
    "nothing_wrong": ["none"],
}

EARLY_STAGES = ["build", "static_checks"]  # owned by another team

# What each class means, as the model is told. Every class says what went
# wrong, never where it happened. An early stage can fail because of its own
# tooling, the submitted code or the machine, so the stage alone decides
# nothing.
CLASS_MEANINGS = {
    "code_defect": "The submitted change is at fault: a test really failed, or the change "
    "does not build or pass the stage's checks.",
    "stuck": "The run is still going and is not finishing. A run the gate has already "
    "failed is not stuck, even if it failed by hitting a time limit.",
    "environment_error": "The test environment failed, not the code. That includes a run whose "
    "tests never really got to run: a job that errored, or one that no "
    "runner ever picked up.",
    "stage_tooling_failure": "The early stage's own tooling failed, not the submitted code "
    "and not the machine.",
    "nothing_wrong": "The gate no longer shows a problem.",
}

# Every action the model may pick. "none" is left out: it is only ever forced
# by code. A test checks this list matches the table above.
MODEL_ACTIONS = [
    "fix_then_rerun",
    "cut_new_version",
    "wait_and_watch",
    "escalate_environment",
    "rerun_same_version",
    "route_to_stage_owner",
]

# What each action asks the operator to do. The judge needs this to tell
# whether a conclusion's instruction is the same as the action.
ACTION_MEANINGS = {
    "fix_then_rerun": "change the code (or the test), then run the gate again",
    "cut_new_version": "make a new version of the submission",
    "rerun_same_version": "run the gate again with no changes",
    "wait_and_watch": "do nothing yet and keep monitoring",
    "escalate_environment": "hand the problem to the environment or infrastructure owners",
    "route_to_stage_owner": "hand the problem to the team that owns that stage",
    "none": "no action is needed",
}
