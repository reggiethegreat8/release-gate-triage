"""Start triage runs that pause for approval, and decide on them later.

Run from the repo root. Every command is its own process. Between them, the
runs live in checkpoints.db, what is remembered about reviewers in memory.db,
and each approved action, recorded once, in actions.db.

  uv run python review.py start dev-04         run one case until it pauses
  uv run python review.py start live-02        the same, for a new "live" issue
  uv run python review.py start dev-04 alice   the same, started for the reviewer alice
  uv run python review.py prefer alice brief   remember how alice wants conclusions
                                               shown: brief or full
  uv run python review.py list                 every saved run and its status
  uv run python review.py approve THREAD
  uv run python review.py edit THREAD ACTION
  uv run python review.py reject THREAD
  uv run python review.py resume THREAD        continue a run that died mid-way
  uv run python review.py resume THREAD alice  the same, for the reviewer alice
"""

import os
import sqlite3
import sys
import textwrap
import uuid

from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore
from langgraph.types import Command
from langsmith import Client

from agent.actions import action_for
from agent.graph import answer_problem, build_graph
from agent.memory import ReviewContext, save_conclusion_style, saved_style
from agent.rules import CODE_DECIDED
from evaluation.evaluators import human_review_feedback
from store.cases import cases_with_history

# Saved runs. check_same_thread=False because LangGraph writes checkpoints
# from a background thread; the saver has its own lock.
checkpointer = SqliteSaver(sqlite3.connect("checkpoints.db", check_same_thread=False))

# Reviewer memory, in a second file: runs and reviewers have different
# lifetimes. isolation_level=None lets the store manage its own transactions.
memory = SqliteStore(sqlite3.connect("memory.db", check_same_thread=False, isolation_level=None))
memory.setup()

graph = build_graph(checkpointer, memory)


def config_for(thread_id):
    """The config every call about one run passes: its thread ID."""
    return {"configurable": {"thread_id": thread_id}}


def show(thread_id):
    """Print where one run stands, read from the saved state."""
    snapshot = graph.get_state(config_for(thread_id))
    state = snapshot.values

    if not state:
        print(f"{thread_id}: no such run")
    elif snapshot.interrupts:
        show_proposal(thread_id, snapshot.interrupts[0].value, state)
    elif snapshot.next:
        # Steps remain, but nothing is asking for a person: the run stopped
        # part-way, because the process was killed or a step raised.
        print(f"{thread_id}: STOPPED MID-RUN, next step: {snapshot.next[0]}")
    else:
        print(
            f"{thread_id}: FINISHED, review={state.get('review')}, final action={state.get('final_action')}"
        )
        recorded = action_for(thread_id)
        if recorded:
            print(
                f"  recorded against {recorded['submission_id']}: {recorded['action']} "
                f"({recorded['review']}, {recorded['recorded_at']} UTC)"
            )


def class_verdict(state, agent_class):
    """How the agent's class came about, in the words the reviewer is shown."""
    failed_steps = {reason.split(":")[0] for reason in state.get("degraded_reasons", [])}
    if state.get("rule_name") in CODE_DECIDED:
        return "   (decided by code; the model was not asked for the class)"
    if "classify" in failed_steps:
        return "   (the model did not answer; the rules' class stands)"
    if agent_class == state["rule_class"]:
        return "   (agreed)"
    return "   (overrode the rules)"


def proposal_lines(shown, state):
    """The lines a reviewer reads before deciding: what the rules said, what the
    agent says, and anything that went wrong on the way.

    `shown` is exactly what the approval step handed over: the conclusion is
    in the form this reviewer asked for. `state` adds how the agent got there.
    """
    lookups = [call["name"] for call in state.get("tool_calls", [])]
    failed = state.get("degraded_reasons", [])
    failed_steps = {reason.split(":")[0] for reason in failed}
    lines = [f"  the rules said:   {state['rule_class']}"]
    lines.append(f"  the agent says:   {shown['triage_class']}{class_verdict(state, shown['triage_class'])}")
    if state.get("deep_dive"):
        if "deep_dive" in failed_steps:
            lines.append("  deep dive:        failed; the quick look's finding is used")
        else:
            lines.append(f"  deep dive:        looked up {', '.join(lookups) if lookups else 'nothing'}")
    lines.append(f"  proposed action:  {shown['action']}")
    lines.append("  conclusion:")
    for line in textwrap.wrap(shown["conclusion"], width=70):
        lines.append(f"      {line}")
    # Shown so a person never approves a fallback without knowing it is one.
    for reason in failed:
        lines.append(f"  a model step failed: {reason}")
    for note in state.get("guard_notes", []):
        lines.append(f"  a guard stepped in: {note}")
    return lines


def show_proposal(thread_id, shown, state):
    """Print what the reviewer is asked to approve, and how to answer."""
    print()
    print("=" * 78)
    print(f"WAITING FOR A PERSON   {shown['submission_id']}   (run {thread_id})")
    print("=" * 78)
    for line in proposal_lines(shown, state):
        print(line)
    others = [action for action in shown["allowed_actions"] if action != shown["action"]]
    print("-" * 78)
    print(f"  approve:  uv run python review.py approve {thread_id}")
    if others:
        print(f"  edit:     uv run python review.py edit {thread_id} {' | '.join(others)}")
    print(f"  reject:   uv run python review.py reject {thread_id}")
    print()


def reviewer_context(user_id):
    """Make the context for a run, and say which style will be used and why.

    A mistyped name is a different person with nothing saved. Without this
    line it would quietly get the default.
    """
    context = ReviewContext(user_id=user_id)
    if user_id:
        style = saved_style(memory, user_id)
        print(f"reviewer {user_id}: " + (style if style else "nothing saved, showing full"))
    return context


def new_run(case_id, user_id=None):
    """Make a run without starting it: its thread ID, its input, its config and
    the reviewer's context. Prints the thread ID and the reviewer's saved
    style. Exits if there is no such case."""
    cases = {case["id"]: case for case in cases_with_history()}
    if case_id not in cases:
        sys.exit(f"no case or live issue is called {case_id}; there are: {', '.join(cases)}")
    case = cases[case_id]
    thread_id = f"{case_id}-{uuid.uuid4().hex[:6]}"
    print(f"started {thread_id}", flush=True)

    # We choose the trace's ID ourselves and keep it in the state. The decision
    # comes later, from another process, and needs to know which trace it is
    # about. This is the trace of this start command. If the run dies and is
    # carried on with "resume", that makes a second trace, which this ID does
    # not name.
    trace_id = uuid.uuid4()
    config = config_for(thread_id) | {"run_id": trace_id}
    # The reviewer's ID goes in as context, beside the input and not in it.
    # Here it is typed on the command line; in a real service it would come
    # from the signed-in session. This project has no sign-in, so anyone can
    # type any name. The graph uses the ID to open that reviewer's saved
    # preference and nobody else's.
    context = reviewer_context(user_id)
    return thread_id, {"issue": case["input"], "trace_id": str(trace_id)}, config, context


def prefer(user_id, style):
    """Remember how one reviewer wants conclusions shown: brief or full."""
    save_conclusion_style(memory, user_id, style)
    print(f"saved: {user_id} prefers {style} conclusions")


def start(case_id, user_id=None):
    thread_id, run_input, config, context = new_run(case_id, user_id)
    graph.invoke(run_input, config, context=context)
    show(thread_id)


def list_runs():
    # checkpointer.list(None) yields every saved checkpoint of every run.
    # Collect all the IDs first: the saver is locked while it is listing.
    thread_ids = {saved.config["configurable"]["thread_id"] for saved in checkpointer.list(None)}
    for thread_id in sorted(thread_ids):
        show(thread_id)


def decide(thread_id, answer):
    snapshot = graph.get_state(config_for(thread_id))
    if not snapshot.interrupts:
        print(f"{thread_id} is not waiting for a decision")
        return
    problem = answer_problem(snapshot.interrupts[0].value["allowed_actions"], answer)
    if problem:
        print(f"not sent: {problem}")
        return

    # The decision is saved in the run before anything is sent to LangSmith:
    # an approval must not wait on the monitoring service.
    graph.invoke(Command(resume=answer), config_for(thread_id))
    show(thread_id)
    send_feedback(snapshot.values.get("trace_id"), answer)


def send_feedback(trace_id, answer):
    """Record a reviewer's decision as feedback on the trace of the start command.

    A trace's ID is the ID of its first run. This is a copy for monitoring; the
    decision itself is already saved in the run. So if LangSmith cannot take
    it, the operator is told and the decision stands.
    """
    if not trace_id:
        print("  no trace is recorded for this run; the decision was not sent to LangSmith")
        return
    try:
        # Sent now and not from a background queue, so a refusal (a usage
        # limit, say) raises here and is reported below, not just logged.
        client = Client(auto_batch_tracing=False)
        # session_id is LangSmith's older name for the tracing project.
        project = client.read_project(project_name=os.environ["LANGSMITH_PROJECT"])
        client.create_feedback(
            run_id=trace_id, trace_id=trace_id, session_id=project.id, **human_review_feedback(answer)
        )
    except Exception as error:  # any failure to reach LangSmith: say so, and keep the decision
        print(f"  NOT sent to LangSmith ({type(error).__name__}: {str(error)[:120]}); the decision is saved")
        return
    print(f"  recorded human_review={answer['decision']} on trace {trace_id}")


def resume(thread_id, user_id=None):
    # None as the input means "no new input: carry on from the last saved step".
    # The context is not saved with a run, so the reviewer has to be named
    # again here, or the run carries on as if nobody was.
    unfinished = bool(graph.get_state(config_for(thread_id)).next)
    graph.invoke(None, config_for(thread_id), context=reviewer_context(user_id))
    show(thread_id)

    # One case is covered here: a run that stopped after the reviewer's answer
    # was saved but before the run finished, for example because the act step
    # failed. decide() never got as far as sending the feedback, so it is sent
    # now. A run that finished and then failed to send is not covered: that
    # failure was printed as "NOT sent" at the time, and nothing retries it.
    state = graph.get_state(config_for(thread_id)).values
    answers = {
        "approved": {"decision": "approve"},
        "edited": {"decision": "edit", "action": state.get("final_action")},
        "rejected": {"decision": "reject"},
    }
    if unfinished and state.get("review") in answers:
        send_feedback(state.get("trace_id"), answers[state["review"]])


if __name__ == "__main__":
    command, *args = sys.argv[1:] or ["list"]
    arguments_needed = {"start": 1, "list": 0, "approve": 1, "edit": 2, "reject": 1, "resume": 1, "prefer": 2}
    if command not in arguments_needed or len(args) < arguments_needed[command]:
        print(__doc__)
    elif command == "start":
        start(*args[:2])
    elif command == "prefer":
        prefer(args[0], args[1])
    elif command == "list":
        list_runs()
    elif command == "approve":
        decide(args[0], {"decision": "approve"})
    elif command == "edit":
        decide(args[0], {"decision": "edit", "action": args[1]})
    elif command == "reject":
        decide(args[0], {"decision": "reject"})
    elif command == "resume":
        resume(*args[:2])
    wait_for_all_tracers()
