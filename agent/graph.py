"""The triage workflow as a LangGraph StateGraph.

    START -> context -> rules -> classify -> analyze -+-> synthesize -> guards -> END
                                                      |        ^
                                                      +-> deep_dive

    context    code: looks up the facts the operator's policy always needs
    rules      code: the first call, and the fallback if the classify step fails
    classify   model: confirms or overrides the rules' class; skipped for the two
               answers code decides (not broken, outage)
    analyze    model: one quick look at what went wrong
    deep_dive  model, only if the quick look was inconclusive: an agent that can
               call the store's lookup tools
    synthesize model: the conclusion and the action
    guards     code: the rules the model cannot override

Built with a checkpointer, one more step follows the guards:

    ... -> guards -> approval -> act -> END
                     (pauses for a person; then what they approved is recorded, once)

Built with a memory store as well, the approval step shows the conclusion the
way the reviewer the run was started for has asked for it (agent/memory.py).
"""

import operator
import os
from functools import partial
from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel

from agent.actions import record_action
from agent.context import fetch_context
from agent.domain import ALLOWED_ACTIONS, MODEL_ACTIONS
from agent.guards import apply_guards
from agent.memory import ReviewContext, conclusion_style
from agent.prompts import analyze_prompt, classify_prompt, deep_dive_prompt, synthesize_prompt
from agent.rules import CODE_DECIDED, classify_by_rules
from agent.tool_agent import investigate
from store.api import check_store

load_dotenv()

# Thinking is on by default for this model and counts against max_tokens, so
# the limit is generous. Six retries, not the client's default two, so a
# rate-limit reply during an experiment is waited out before the fallbacks
# below take over.
model = ChatAnthropic(model=os.environ["TRIAGE_MODEL"], max_tokens=2000, max_retries=6)


# --- state: the one record that every node reads from and adds to ---


class TriageState(TypedDict, total=False):
    issue: dict  # the record: the six fields the gate reports; set once by the caller
    trace_id: str  # this run's trace in LangSmith, if the caller set one
    context: dict  # the looked-up facts: what code fetched from the other systems; see context.py
    rule_class: str  # what the rules said
    rule_name: str  # which rule fired
    triage_class: str  # the class after the model confirmed or overrode it
    model_class: str  # what the model itself said; absent if it failed or was not asked
    finding: str  # what most likely went wrong
    conclusive: bool  # False sends the run to the deep dive
    deep_dive: bool  # present, and True, only if the deep dive ran
    tool_calls: list[dict]  # the lookups the deep dive chose to make
    conclusion: str  # the short text for the on-call operator
    action: str  # the recommended action
    guard_notes: list[str]  # one line for each thing a guard changed
    review: str  # the person's decision: approved, edited, rejected, not_needed
    final_action: str | None  # what will actually be done; None if rejected
    action_recorded: bool  # True if this run's approved action was recorded just now
    # One entry per model step that failed. Empty means the run was not degraded.
    # Appended, not replaced, so a second failure does not overwrite the first.
    degraded_reasons: Annotated[list[str], operator.add]


# --- structured output: the shape each model answer must have ---


class ClassChoice(BaseModel):
    # Exactly the five class names.
    triage_class: Literal[*ALLOWED_ACTIONS]
    reason: str  # one sentence; not used by the code, but visible in the trace


class Analysis(BaseModel):
    finding: str
    conclusive: bool


class Synthesis(BaseModel):
    # One answer holds both, so the text and the action come from the same
    # thought and cannot contradict each other.
    conclusion: str
    action: Literal[*MODEL_ACTIONS]


# One structured-output client per schema. The deep dive hands the bare model
# to the tool agent, which binds the Analysis schema itself.
classifier = model.with_structured_output(ClassChoice, method="json_schema")
analyzer = model.with_structured_output(Analysis, method="json_schema")
synthesizer = model.with_structured_output(Synthesis, method="json_schema")


def degraded_reason(step, error):
    """What a node adds to the state when its model step fails."""
    return [f"{step}: {type(error).__name__}: {str(error)[:200]}"]


# In every model node below, the prompt is built before the try. Only the model
# step is inside it. So "degraded" means the model step failed, and a malformed
# issue still stops the run loudly. For the deep dive the model step is the
# whole agent run: a model call, the connection to the tools, or hitting the
# step limit. A lookup that returns an error does not degrade the run; the
# model is shown the error and carries on.


# --- nodes: each takes the state and returns only the keys it changes ---


def context_node(state: TriageState):
    """No model here. Code fetches the facts the policy needs for every run.

    There is no try/except: if the store cannot answer, the run stops. The
    rules and the guards both depend on these facts, so a run without them
    would not be a degraded run, it would be a wrong one.
    """
    return {"context": fetch_context(state["issue"])}


def rules_node(state: TriageState):
    rule_class, rule_name = classify_by_rules(state["issue"], state["context"])
    return {"rule_class": rule_class, "rule_name": rule_name}


def classify_node(state: TriageState, rules_hint=True):
    """rules_hint=False keeps the rules' pick out of the prompt. That is one of
    the experiment variants: it measures what telling the model is worth. The
    rules still run, and are still the fallback."""
    rule_class = state["rule_class"]

    # Two answers are decided by code and the model is not asked: nothing is
    # wrong any more, or the release line is having an outage.
    if state["rule_name"] in CODE_DECIDED:
        return {"triage_class": rule_class}

    prompt = classify_prompt(state["issue"], state["context"], rule_class if rules_hint else None)
    try:
        choice = classifier.invoke(prompt)
    except Exception as error:
        # Fallback: the model step failed, so the rules' answer stands.
        return {"triage_class": rule_class, "degraded_reasons": degraded_reason("classify", error)}

    # The model may not declare that nothing is wrong. Only the rules can,
    # from the gate status or the submission's state in the store. This also
    # blunts a log that says "nothing wrong". The model's own answer is kept in
    # model_class, so a result can never look as if the model agreed with the
    # rules when it did not.
    if choice.triage_class == "nothing_wrong":
        return {"triage_class": rule_class, "model_class": choice.triage_class}
    return {"triage_class": choice.triage_class, "model_class": choice.triage_class}


def analyze_node(state: TriageState):
    """The quick look: one model call on what went wrong, and whether it is sure."""
    if state["triage_class"] == "nothing_wrong":
        return {"finding": "There is nothing left to triage.", "conclusive": True}

    prompt = analyze_prompt(state["issue"], state["context"], state["triage_class"])
    try:
        analysis = analyzer.invoke(prompt)
    except Exception as error:
        # No finding, and no deep dive either: the model is not answering.
        return {
            "finding": "No analysis was available.",
            "conclusive": True,
            "degraded_reasons": degraded_reason("analyze", error),
        }
    return {"finding": analysis.finding, "conclusive": analysis.conclusive}


def route_after_analyze(state: TriageState):
    """Where to go after the quick look: straight on, or to the deep dive."""
    if state["conclusive"]:
        return "synthesize"
    return "deep_dive"


def deep_dive_node(state: TriageState, tools=True):
    """The deep dive: only runs when the quick look could not decide.

    This is the one step where the model chooses what to look at. It is given
    the store's lookup tools and may call them before it answers.

    tools=False is one of the experiment variants: the same prompt, answered in
    one model call with no tools. Comparing the two measures what the lookups
    are worth.
    """
    check_store()  # stops the run here if the store is missing or out of date
    prompt = deep_dive_prompt(state["issue"], state["context"], state["triage_class"], state["finding"])
    try:
        if tools:
            analysis, tool_calls = investigate(model, Analysis, prompt)
        else:
            analysis, tool_calls = analyzer.invoke(prompt), []
    except Exception as error:
        # Keep the quick look's finding; it is better than nothing.
        return {"deep_dive": True, "degraded_reasons": degraded_reason("deep_dive", error)}
    return {"deep_dive": True, "finding": analysis.finding, "tool_calls": tool_calls}


def synthesize_node(state: TriageState):
    triage_class = state["triage_class"]

    if triage_class == "nothing_wrong":
        # Worded without the word "withdrawn", which the guards' scan would
        # take for advice to withdraw.
        return {
            "conclusion": "There is nothing left to triage: the gate is no longer failing "
            "this run, or the submission is no longer active. No action is "
            "needed.",
            "action": "none",
        }

    prompt = synthesize_prompt(state["issue"], state["context"], triage_class, state["finding"])
    try:
        synthesis = synthesizer.invoke(prompt)
    except Exception as error:
        # Fallback: a fixed sentence and the default action of the class we
        # have. That class is the rules' own if classify failed too; if classify
        # succeeded, the model's class is kept.
        return {
            "conclusion": f"Automatic analysis was unavailable. Classified as {triage_class}.",
            "action": ALLOWED_ACTIONS[triage_class][0],
            "degraded_reasons": degraded_reason("synthesize", error),
        }
    return {"conclusion": synthesis.conclusion, "action": synthesis.action}


def guards_node(state: TriageState):
    """No model here. Code checks the model's answer and corrects it if needed."""
    action, conclusion, notes = apply_guards(
        state["triage_class"], state["action"], state["conclusion"], state["context"]
    )
    return {"action": action, "conclusion": conclusion, "guard_notes": notes}


def answer_problem(allowed_actions, answer):
    """Return what is wrong with a reviewer's answer, or None if it is usable.

    A person may overrule the agent, but only within the class's own list.
    This function must never raise, whatever it is given.
    """
    if not isinstance(answer, dict) or answer.get("decision") not in ("approve", "edit", "reject"):
        return "the decision must be approve, edit or reject"
    if answer["decision"] == "edit" and answer.get("action") not in allowed_actions:
        return f"an edit must be one of: {', '.join(allowed_actions)}"
    return None


def approval_node(state: TriageState, runtime: Runtime[ReviewContext]):
    """Pause the run until a person decides. Only used with a checkpointer.

    Nothing is carried out here: this step only records the decision. The
    step after it, act, has the effect. With a store, the conclusion is
    shown the way the named reviewer has asked for it; the stored conclusion
    is never changed.
    """
    allowed = ALLOWED_ACTIONS[state["triage_class"]]

    # Nothing to approve when there is nothing left to triage.
    if state["action"] == "none":
        return {"review": "not_needed", "final_action": "none"}

    conclusion = state["conclusion"]
    # A reviewer who prefers brief is shown the text up to the first full stop,
    # a rough first sentence. Not when a guard changed something: then either
    # the text is the guard's own notice, or the action was changed and the
    # text may no longer match it. Either way the person approving needs all
    # of it.
    if conclusion_style(runtime) == "brief" and not state["guard_notes"]:
        conclusion = conclusion.split(". ")[0].rstrip(".") + "."

    # The argument is what the reviewer is shown. On resume this node runs
    # again from its first line. (On that second pass the caller usually gives no context, so the text
    # above is worked out again in full. Nothing uses it; what the reviewer
    # was shown is the saved copy from the first pass.)
    answer = interrupt(
        {
            "submission_id": state["issue"]["submission_id"],
            "triage_class": state["triage_class"],
            "action": state["action"],
            "conclusion": conclusion,
            "allowed_actions": allowed,
        }
    )

    # This code must not raise. The answer is saved with this step as soon as
    # interrupt() returns it. If the step then failed, every retry would be
    # handed that saved answer again, not a new one, and the run could never
    # finish. So answers are checked with answer_problem() before resuming,
    # and one that is unusable anyway counts as a rejection.
    if answer_problem(allowed, answer):
        return {"review": "rejected", "final_action": None}
    if answer["decision"] == "approve":
        return {"review": "approved", "final_action": state["action"]}
    if answer["decision"] == "edit":
        return {"review": "edited", "final_action": answer["action"]}
    return {"review": "rejected", "final_action": None}


def act_node(state: TriageState, config: RunnableConfig):
    """Carry out what the person approved. Only used with a checkpointer.

    Here that means recording the action against the submission (see
    actions.py), which stands in for opening a ticket or starting a rerun.

    A separate step, for two reasons. The
    approval step runs again from its first line when a run is resumed, so
    nothing with an effect may sit in it. And a saved run only guarantees that
    a step runs at least once: if the process died after the record was
    written and before this step was saved, the step would run again. The
    record is keyed by the run's thread ID, so the second time changes nothing.
    """
    if state["review"] not in ("approved", "edited"):
        return {"action_recorded": False}  # rejected, or there was nothing to approve
    thread_id = config["configurable"]["thread_id"]
    recorded = record_action(
        thread_id, state["issue"]["submission_id"], state["final_action"], state["review"]
    )
    return {"action_recorded": recorded}


# --- the graph: which node runs after which ---


def build_graph(checkpointer=None, store=None, tools=True, rules_hint=True, approval=None):
    """Without a checkpointer: the triage steps, ending after the guards.
    With one: the same steps, then a pause for a person's approval.

    tools and rules_hint switch on the two experiment variants; see
    deep_dive_node and classify_node. Both are on in normal use.

    store is the memory store, holding each reviewer's saved preference. Given
    without a checkpointer it is not used: only the approval step reads it.

    approval=True adds the approval steps with no checkpointer given here. It
    is for a server that supplies its own storage for paused runs, such as
    `langgraph dev` (see agent/served.py). Left alone, the steps are added
    only when a checkpointer is given.
    """
    if approval is None:
        approval = checkpointer is not None
    builder = StateGraph(TriageState, context_schema=ReviewContext)
    builder.add_node("context", context_node)
    builder.add_node("rules", rules_node)
    builder.add_node("classify", partial(classify_node, rules_hint=rules_hint))
    builder.add_node("analyze", analyze_node)
    builder.add_node("deep_dive", partial(deep_dive_node, tools=tools))
    builder.add_node("synthesize", synthesize_node)
    builder.add_node("guards", guards_node)

    builder.add_edge(START, "context")
    builder.add_edge("context", "rules")
    builder.add_edge("rules", "classify")
    builder.add_edge("classify", "analyze")
    builder.add_conditional_edges("analyze", route_after_analyze, ["deep_dive", "synthesize"])
    builder.add_edge("deep_dive", "synthesize")
    builder.add_edge("synthesize", "guards")

    if not approval:
        builder.add_edge("guards", END)
    else:
        builder.add_node("approval", approval_node)
        builder.add_node("act", act_node)
        builder.add_edge("guards", "approval")
        builder.add_edge("approval", "act")
        builder.add_edge("act", END)

    return builder.compile(checkpointer=checkpointer, store=store)


# The plain graph, with no pause. The experiments and the gate use this one.
graph = build_graph()
