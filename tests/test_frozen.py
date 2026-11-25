"""Everything a model reads, and what the code-decided parts answer, as one fingerprint.

The held-out results belong to an exact set of prompts, definitions, policy
lines, tool descriptions, rules and guards. This fingerprint was taken before
the first held-out run and has not changed since. If this test fails, one of
them has changed, and the reported results no longer describe the system: do
not update the value below without rerunning the evaluation and saying so.
"""

import hashlib
import json

from agent import domain, graph, guards, policy, prompts, rules, tool_agent
from evaluation import judge
from store import api

FROZEN = "7d26df1007ed6fb8a6fd70dbf504b7a0488d1d532b0e12f693913e95c8acba52"

ISSUE = {
    "submission_id": "SUB-1",
    "gate_status": "failed",
    "stage": "build",
    "hours_in_status": 9,
    "job_events": ["job_started"],
    "log_excerpt": "a <log> line",
}
CONTEXT = {
    "release_line": "R1",
    "submission_state": "active",
    "commits_since_failure": 2,
    "hold_note": "note",
    "dependency_landed": False,
    "stage_tickets": [{"ticket_id": "T1", "state": "open", "text": "t"}],
    "typical_hours": 0.5,
    "peers_total": 4,
    "peers_failing": 3,
    "peers_running": 1,
    "peers_stuck": 1,
    "outage": True,
    "past_min_hours": True,
    "times_typical": 18.0,
    "slow": True,
}


def fingerprint():
    parts = [
        # The tables and the tool agent's instructions.
        json.dumps(domain.CLASS_MEANINGS, sort_keys=True),
        json.dumps(domain.ALLOWED_ACTIONS, sort_keys=True),
        json.dumps(domain.ACTION_MEANINGS, sort_keys=True),
        json.dumps(domain.MODEL_ACTIONS),
        tool_agent.SYSTEM_PROMPT,
        str(tool_agent.MAX_STEPS),
    ]
    # Every prompt, for every class, and the judge's.
    lookup = [{"name": "n", "args": {}, "result": "r", "failed": False}]
    for name in domain.ALLOWED_ACTIONS:
        parts += [
            policy.policy_text(name),
            prompts.classify_prompt(ISSUE, CONTEXT, name),
            prompts.classify_prompt(ISSUE, CONTEXT, None),
            prompts.analyze_prompt(ISSUE, CONTEXT, name),
            prompts.deep_dive_prompt(ISSUE, CONTEXT, name, "f"),
            prompts.synthesize_prompt(ISSUE, CONTEXT, name, "f"),
            judge.judge_prompt(ISSUE, CONTEXT, name, domain.ALLOWED_ACTIONS[name][0], "concl", lookup),
        ]
    # The tool descriptions: the docstrings of the routes offered as tools.
    for route in api.app.routes:
        if getattr(route, "tags", None):
            parts.append(f"{route.operation_id}|{route.endpoint.__doc__}")
    # What the rules answer, over every combination they read.
    for status in ("failed", "running", "passed", "skipped", "withdrawn"):
        for stage in ("build", "integration_tests"):
            for events in (["job_started"], ["env_setup_failed"], ["job_errored"]):
                for outage in (True, False):
                    for state in ("active", "withdrawn"):
                        issue = dict(ISSUE, gate_status=status, stage=stage, job_events=events)
                        context = dict(CONTEXT, outage=outage, submission_state=state)
                        parts.append(str(rules.classify_by_rules(issue, context)))
    # What the guards answer, for every class and action.
    withdrawn = dict(CONTEXT, outage=False, submission_state="withdrawn")
    for name in domain.ALLOWED_ACTIONS:
        for action in [*domain.MODEL_ACTIONS, "none"]:
            parts.append(
                str(guards.apply_guards(name, action, "Please cancel it.", CONTEXT))
                + str(guards.apply_guards(name, action, "Fine.", withdrawn))
            )
    # The shape each model answer must have.
    for schema in (graph.ClassChoice, graph.Analysis, graph.Synthesis, judge.Verdict):
        parts.append(json.dumps(schema.model_json_schema(), sort_keys=True))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def test_nothing_a_model_reads_has_changed_since_the_held_out_run():
    assert fingerprint() == FROZEN
