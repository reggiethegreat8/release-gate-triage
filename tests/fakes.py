"""Stand-ins for three of the model steps, so tests are exact, free and offline.

The fourth, the deep dive, is replaced separately: by default in conftest.py,
and by fake_investigation in test_graph.py.
"""

from agent.graph import Analysis, ClassChoice, Synthesis


class Fake:
    """Stands in for a model step. Each call returns the next answer in the
    list (the last one repeats). An answer that is an error is raised."""

    def __init__(self, *answers):
        self.answers = answers
        self.calls = 0
        self.prompts = []

    def invoke(self, prompt):
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        self.prompts.append(prompt)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def fake_classifier(triage_class):
    return Fake(ClassChoice(triage_class=triage_class, reason="because"))


def fake_analyzer(conclusive, finding="a finding"):
    return Fake(Analysis(finding=finding, conclusive=conclusive))


def fake_synthesizer(action, conclusion="a conclusion"):
    return Fake(Synthesis(conclusion=conclusion, action=action))


def broken():
    return Fake(TimeoutError("model unavailable"))


def issue(**changes):
    """A plain record: the six fields the gate reports."""
    base = {
        "submission_id": "SUB-1",
        "gate_status": "failed",
        "stage": "integration_tests",
        "hours_in_status": 2,
        "job_events": ["job_started"],
        "log_excerpt": "log",
    }
    return base | changes


def context(**changes):
    """What the context step would fetch for a plain record: nothing unusual."""
    base = {
        "release_line": "R001",
        "submission_state": "active",
        "commits_since_failure": 0,
        "hold_note": "",
        "dependency_landed": None,
        "stage_tickets": [],
        "typical_hours": 3.0,
        "peers_total": 0,
        "peers_failing": 0,
        "peers_running": 0,
        "peers_stuck": 0,
        "outage": False,
        "past_min_hours": False,
        "times_typical": 0.7,
        "slow": False,
    }
    return base | changes
