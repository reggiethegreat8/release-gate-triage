"""A terminal console for the live demo: one case, from the issue to the decision.

  uv run python console.py                  pick a case from a list: arrow keys, then Enter
  uv run python console.py roger            the same list, for the reviewer roger (a word that is not a case)
  uv run python console.py test-25          run one case and decide on it
  uv run python console.py dev-04 roger     the same, started for the reviewer roger

This is a display layer over review.py. It builds the run with review.new_run,
runs review.graph step by step so each step can be shown, and hands the answer
to review.decide, which checks it, resumes the run and sends the feedback. It
makes no decision of its own.

Colours: cyan is code, violet is the model, green is a person. Gold and blue
mark titles and labels, red a failure or a refusal, orange a guard's note.
"""

import io
import os
import re
import select
import shutil
import sys
import textwrap
from contextlib import contextmanager, redirect_stdout

from langchain_core.tracers.langchain import wait_for_all_tracers
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import review
from agent.rules import CODE_DECIDED

CODE, MODEL, PERSON = "cyan", "medium_purple1", "green"
# Titles and labels, so the eye finds them. Not one of the three colours above.
TITLE = "bold gold1"
LABEL = "bold light_sky_blue1"
BORDER = "gold1"
GUARD_NOTE = "dark_orange"
LOG_PREFIX = re.compile(r"^\S+\s+(ERROR|WARN|INFO|DEBUG)\b")

# Never wider than 100 columns: this is shown over a video call.
screen = Console(highlight=False, width=min(shutil.get_terminal_size((100, 24)).columns, 100))

# The record's fields, in the order shown, with the words used for each.
ISSUE_FIELDS = [
    ("submission_id", "submission"),
    ("gate_status", "status at the test gate"),
    ("stage", "stage"),
    ("hours_in_status", "hours in this status"),
    ("job_events", "job events, in order"),
]

# The cases the demo uses, listed first when a case is picked from the list.
SHORTLIST = ["dev-04", "test-25", "test-35", "test-13", "test-41", "test-17"]
PAGE = 10

STAGES = {
    "Stage 1, code": ["context", "rules"],
    "Stage 2, model": ["classify", "analyze", "deep_dive", "synthesize"],
    "Stage 3, code + person": ["guards", "approval", "act"],
}

# Who does each step. The words on each line come from the run's state.
STEPS = {
    "context": CODE,
    "rules": CODE,
    "classify": MODEL,
    "analyze": MODEL,
    "deep_dive": MODEL,
    "synthesize": MODEL,
    "guards": CODE,
    "approval": PERSON,
    "act": CODE,
}

NO_ANSWER = "the model did not answer; the fallback was used"

# How many lines of the screen the issue last took, with its question, so that
# going back to the list can wipe it.
drawn = {"issue": 0}


def wipe_up(lines):
    """Wipe the last `lines` lines of the screen, leaving the cursor where they
    began. This is how the list is redrawn in place. Clearing the whole screen
    is avoided: terminals do not all do that the same way.

    If the lines no longer all fit on the screen, the first of them have
    scrolled into the terminal's history, where they cannot be reached one by
    one. Then the history is wiped as well, so nothing stale is left above.
    """
    if not screen.is_terminal or not lines:
        return
    screen.file.write(f"\x1b[{lines}A\r\x1b[J")
    if lines >= screen.size.height:
        screen.file.write("\x1b[3J")
    screen.file.flush()


def printed_by(function, *args):
    """Call one of review.py's functions and return (its result, what it printed).

    If the call fails, what it had printed by then goes with the error, as
    `error.printed`, so it can still be shown.
    """
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            result = function(*args)
    except BaseException as error:
        error.printed = captured.getvalue().strip("\n")
        raise
    return result, captured.getvalue().strip("\n")


def show_stopped(thread_id, error, what):
    """Say that a run stopped part-way, why, and how to carry it on. Without
    this the person watching would get a traceback and no way forward."""
    printed = getattr(error, "printed", "")
    if printed:
        screen.print(Text(printed))
    reason = (
        "interrupted with Ctrl+C"
        if isinstance(error, KeyboardInterrupt)
        else f"{type(error).__name__}: {error}"
    )
    screen.print()
    screen.print(Text(f"{what} stopped: {reason}"[:400], style="red"))
    screen.print(Text(f"  the run is saved as {thread_id}; carry it on with:"))
    screen.print(Text(f"    uv run python review.py resume {thread_id}"))


def reviewer_problem(name):
    """What stops a name being used for a reviewer, or None. The check a run
    makes on the name, and the memory store's own rules, asked here so a bad
    name is refused with a sentence and not a traceback."""
    try:
        review.ReviewContext(user_id=name)
        review.saved_style(review.memory, name)
    except ValueError as problem:
        return str(problem)
    return None


def show_issue(issue):
    """THE ISSUE panel: the six fields the test gate reports, and nothing else
    from the case. Returns how many lines of the screen it took."""
    fields = Table.grid(padding=(0, 2))
    for field, words in ISSUE_FIELDS:
        value = issue[field]
        if isinstance(value, list):
            value = " → ".join(value)  # the list is in the order the test gate recorded it
        fields.add_row(Text(f"{words}:", style=LABEL), Text(str(value), style="bold"))
    log = Text()
    log.append("\nlog of this submission, as the test gate reports it:", style=LABEL)
    for line in issue["log_excerpt"].rstrip("\n").splitlines():
        # The time and the level are greyed, the same for every level; the message is plain.
        found = LOG_PREFIX.match(line)
        prefix = found.group() if found else ""
        log.append("\n")
        log.append(prefix, style="dim")
        log.append(line[len(prefix) :])
    shown = panel(Group(fields, log), "THE ISSUE: what the test gate reports")
    screen.print()
    screen.print(shown)
    return 1 + len(screen.render_lines(shown, pad=False))  # the lines this took, with the blank one


def panel(body, title, border=BORDER):
    return Panel(body, title=Text(title, style=TITLE), title_align="left", border_style=border)


def step_lines(name, colour, lines):
    """Print one step: its name, then what it found, one or more lines."""
    for number, words in enumerate(lines):
        wrapped = textwrap.wrap(words, width=screen.width - 16) or [""]
        for part_number, part in enumerate(wrapped):
            first = number == 0 and part_number == 0
            line = Text("    ")
            line.append(f"{name.replace('_', ' ') if first else '':<12}", style=f"bold {colour}")
            line.append(part, style=colour)
            screen.print(line)


def found_by(name, update, state):
    """What one finished step found: (colour, lines). Read from the run's state;
    nothing here is decided.

    `update` is what the step returned, `state` everything so far with it added.
    """
    colour = STEPS.get(name, CODE)
    failed = bool(update.get("degraded_reasons"))  # this step's model call failed
    nothing_wrong = state.get("triage_class") == "nothing_wrong"

    if name == "context":
        facts, issue = update["context"], state["issue"]
        return colour, [
            f"{facts['peers_failing']} of {facts['peers_total']} other submissions "
            "on this release line are failing",
            f"{issue['hours_in_status']} hours in this status; "
            f"this stage typically takes {facts['typical_hours']}",
        ]
    if name == "rules":
        lines = [f"{update['rule_class']}   (rule: {update['rule_name']})"]
        if update["rule_name"] in CODE_DECIDED:
            lines.append("code decides this one; the model is not asked for the class")
        return colour, lines
    if name == "classify":
        verdict = review.class_verdict(state, update["triage_class"])
        return colour, [f"{update['triage_class']}{verdict}"]
    if name in ("analyze", "synthesize") and nothing_wrong:
        return CODE, ["nothing left to triage; the model was not asked"]
    if name == "analyze":
        if failed:
            return colour, [NO_ANSWER]
        sure = "sure of its finding, so no deep dive"
        return colour, [sure if update["conclusive"] else "not sure, so it takes a closer look"]
    if name == "deep_dive":
        if failed:
            return colour, [NO_ANSWER]
        lookups = [call["name"] for call in update.get("tool_calls", [])]
        return colour, [f"looked up {', '.join(lookups) if lookups else 'nothing'}"]
    if name == "synthesize":
        picked = f"picked {update['action']}"
        return colour, [f"{NO_ANSWER}: {picked}" if failed else f"wrote the conclusion; {picked}"]
    if name == "guards":
        return colour, update["guard_notes"] or ["checked the answer; changed nothing"]
    if name == "approval":
        waiting = "review" not in update
        return colour, ["waiting for a person" if waiting else "not needed: there is nothing to approve"]
    return colour, ["finished"]


def show_run(run_input, config, context):
    """THE RUN: run the graph, and as each step finishes print what it found."""
    stage_shown = None
    state = dict(run_input)
    for chunk in review.graph.stream(run_input, config, context=context, stream_mode="updates"):
        for name, update in chunk.items():
            if name == "__interrupt__":  # the approval step has paused the run
                name, update = "approval", {}
            update = update or {}
            # Keep the state as the graph does: failures are added to, the rest replaced.
            failures = state.get("degraded_reasons", []) + update.get("degraded_reasons", [])
            state = state | update | {"degraded_reasons": failures}
            if name == "classify" and state.get("rule_name") in CODE_DECIDED:
                continue  # the rules' line has already said that code decided

            stage = next((title for title, steps in STAGES.items() if name in steps), None)
            if stage and stage != stage_shown:
                screen.print(Text(f"  {stage}", style=TITLE))
                stage_shown = stage
            step_lines(name, *found_by(name, update, state))


def show_proposal(thread_id, shown, state):
    """THE PROPOSAL panel: the lines from review.proposal_lines, word for word.
    Not review.py's banner or its approve, edit and reject commands: decide()
    asks for the answer here."""
    body = Text()
    for line in review.proposal_lines(shown, state):
        label, colon, rest = line.partition(":")
        if line.startswith("  a model step failed:"):
            body.append(line, style="red")
        elif line.startswith("  a guard stepped in:"):
            body.append(line, style=GUARD_NOTE)
        elif line.startswith("      ") or not colon:  # a line of the conclusion's text
            body.append(line)
        elif line.startswith("  proposed action:"):
            body.append(label + colon, style=LABEL)
            body.append(rest[: len(rest) - len(rest.lstrip())])
            body.append(rest.lstrip(), style="bold reverse")
        else:
            body.append(label + colon, style=LABEL)
            body.append(rest)
        body.append("\n")
    body.rstrip()
    title = f"THE PROPOSAL: {shown['submission_id']}   (run {thread_id})"
    screen.print(panel(body, title))


def ask_person(prompt):
    return screen.input(Text(prompt, style=f"bold {PERSON}"))


def result_text(said):
    """What review.py printed about a run, with a failure to reach LangSmith in red."""
    text = Text()
    for line in said.splitlines():
        text.append(line + "\n", style="red" if line.lstrip().startswith("NOT sent") else None)
    text.rstrip()
    return text


def decide(thread_id, shown, ask):
    """Ask for the decision, hand it to review.py, and show what it printed in
    THE RESULT panel.

    Returns True once the run is no longer waiting, False if it was left waiting.
    """
    others = [action for action in shown["allowed_actions"] if action != shown["action"]]
    while True:
        try:
            choice = ask("[a]pprove  [e]dit  [r]eject  [q]uit and leave waiting: ").strip().lower()
            if choice in ("a", "approve"):
                answer = {"decision": "approve"}
            elif choice in ("r", "reject"):
                answer = {"decision": "reject"}
            elif choice in ("e", "edit"):
                screen.print(Text(f"  other actions this class allows: {' | '.join(others) or 'none'}"))
                answer = {"decision": "edit", "action": ask("  edit to which action: ").strip()}
            elif choice in ("q", "quit"):
                raise EOFError
            else:
                continue
        except (EOFError, KeyboardInterrupt):  # quit, no more input, or Ctrl+C
            screen.print()
            screen.print(Text(f"left waiting: {thread_id}"))
            screen.print(Text(f"  decide later:  uv run python review.py approve {thread_id}"))
            return False

        if not review.graph.get_state(review.config_for(thread_id)).interrupts:
            # Decided from somewhere else while this prompt was open.
            _, said = printed_by(review.show, thread_id)
            screen.print(Text("this run is no longer waiting; it was decided elsewhere", style="red"))
            screen.print(panel(result_text(said), "THE RESULT", border=CODE))
            return True

        # review.decide checks the answer, resumes the run and sends the feedback
        # to LangSmith, which can take a moment: say so, so the screen is not silent.
        screen.print(Text("  sending the decision...", style="dim"))
        try:
            _, said = printed_by(review.decide, thread_id, answer)
        except (Exception, KeyboardInterrupt) as error:
            show_stopped(thread_id, error, "the decision")
            return False
        if review.graph.get_state(review.config_for(thread_id)).interrupts:
            screen.print(Text(said, style="red"))  # refused: the run is still waiting
            continue
        screen.print(panel(result_text(said), "THE RESULT", border=PERSON))
        return True


# The terminal's settings from before key mode, while key mode is on.
_line_settings = []


@contextmanager
def key_mode():
    """Read single keys for a while: no waiting for Enter, and nothing echoed.

    Held for the whole of a stretch of key reading, not switched on and off
    around each key: a key that arrived in a gap would be echoed onto the
    screen and throw off the list drawn in place. Does nothing where there is
    no terminal or no termios.
    """
    try:
        import termios
        import tty

        keyboard = sys.stdin.fileno() if sys.stdin.isatty() else None
    except (ImportError, OSError, ValueError):
        keyboard = None
    if keyboard is None:
        yield
        return
    # Settings are changed at once (TCSANOW): waiting for the screen to catch up,
    # or throwing away keys already pressed, would lose a fast typist's keys.
    before = termios.tcgetattr(keyboard)
    _line_settings.append(before)
    try:
        tty.setcbreak(keyboard, termios.TCSANOW)
        yield
    finally:
        _line_settings.pop()
        termios.tcsetattr(keyboard, termios.TCSANOW, before)


@contextmanager
def line_mode():
    """Inside key mode, go back to ordinary typed lines for one question."""
    if not _line_settings:
        yield
        return
    import termios
    import tty

    keyboard = sys.stdin.fileno()
    termios.tcsetattr(keyboard, termios.TCSANOW, _line_settings[-1])
    try:
        yield
    finally:
        tty.setcbreak(keyboard, termios.TCSANOW)


def read_key():
    """One key from the keyboard, without waiting for Enter.

    None if single keys cannot be read here: no terminal, or a system without
    termios. Ctrl+D ("\\x04") if the input has closed. A key that is not plain
    text, such as an accented letter, comes back as a character no caller
    acts on, so it is ignored and never mistaken for closed input.
    """
    try:
        import termios  # noqa: F401  only to learn whether single keys are possible
    except ImportError:
        return None
    if not sys.stdin.isatty():
        return None
    keyboard = sys.stdin.fileno()
    with key_mode():
        try:
            # Read from the terminal itself, not through Python's buffered stdin:
            # an arrow key is three bytes (Esc, "[" and a letter) that arrive
            # together, and a buffered read of one character hides the other two.
            pressed = os.read(keyboard, 1)
            while pressed in (b"\x1b", b"\x1b[") and select.select([keyboard], [], [], 0.05)[0]:
                pressed += os.read(keyboard, 1)
        except OSError:  # the terminal went away
            return "\x04"
    if not pressed:
        return "\x04"
    return pressed.decode(errors="replace")


def enter_or(prompt, ask, key, letters):
    """Ask a question answered by Enter or by one of a few letters.

    Returns "" for Enter, or the letter. With `key` the letter needs no Enter
    after it, and any other key is ignored. Without it, or if single keys
    cannot be read, a line is typed, and a line that does not start with one
    of the letters counts as Enter. Ctrl+C, Ctrl+D or closed input is "q".
    """
    try:
        if key:
            screen.print(Text(prompt, style=f"bold {PERSON}"), end="")
            with key_mode():
                while True:
                    pressed = key()
                    if pressed is None:  # single keys cannot be read: ask for a line
                        screen.print()
                        break
                    if pressed in ("\r", "\n"):
                        screen.print()
                        return ""
                    if pressed in ("\x03", "\x04"):  # Ctrl+C, Ctrl+D or closed input
                        screen.print()
                        return "q"
                    if pressed.lower() in tuple(letters):
                        screen.print()
                        return pressed.lower()
        first = ask(prompt).strip().lower()[:1]
        return first if first and first in letters else ""
    except (EOFError, KeyboardInterrupt):  # no keyboard to wait for, or Ctrl+C
        screen.print()
        return "q"


def start_wanted(ask, key, back=False):
    """Wait before the run. Returns what was chosen, and how many lines of the
    screen the question took. The choice is "start", "quit", or "back" to pick
    another case, which is offered only when the case came from the list."""
    if back:
        prompt = "press Enter to have the agent triage this issue, b to go back to the list, or q to quit: "
    else:
        prompt = "press Enter to have the agent triage this issue, or q to quit: "
    pressed = enter_or(prompt, ask, key, "qb" if back else "q")
    return {"": "start", "q": "quit", "b": "back"}[pressed], rows(prompt)


def run_case(case_id, user_id=None, ask=ask_person, key=None, back=False):
    """Show one case and run it: the issue, Enter to start, the run, the
    proposal and the decision.

    Returns "back" if the person went back to the list before the run (offered
    when `back` is set), "quit" if they quit before it, and "done" otherwise.
    """
    # At the keyboard the start prompt reads single keys. A caller that brings
    # its own `ask` is asked for a line, unless it brings `key` too.
    if key is None and ask is ask_person and sys.stdin.isatty():
        key = read_key
    (thread_id, run_input, config, context), said = printed_by(review.new_run, case_id, user_id)

    issue_lines = show_issue(run_input["issue"])
    wanted, prompt_lines = start_wanted(ask, key, back)
    if wanted != "start":
        # Nothing has run and nothing is saved, so there is nothing to undo.
        screen.print(Text("not started"))
        drawn["issue"] = issue_lines + prompt_lines + 1
        return wanted

    screen.print(Text("\nTHE RUN", style=TITLE))
    screen.print(Text(said, style="dim"))
    try:
        show_run(run_input, config, context)
    except (Exception, KeyboardInterrupt) as error:
        # The graph lets some failures through on purpose, such as a store
        # that is out of date. Say what happened; the steps so far are saved.
        show_stopped(thread_id, error, "the run")
        return "done"
    screen.print()

    snapshot = review.graph.get_state(review.config_for(thread_id))
    if not snapshot.interrupts:
        # Nothing is waiting for a person: show where the run stands, as review.py does.
        _, said = printed_by(review.show, thread_id)
        screen.print(panel(result_text(said), "THE RESULT", border=CODE))
        return "done"
    shown = snapshot.interrupts[0].value
    show_proposal(thread_id, shown, snapshot.values)
    decide(thread_id, shown, ask)
    return "done"


def cases_in_order():
    """Every case that can be run, the demo's shortlist first, then the rest
    in the order of the case files."""
    cases = {case["id"]: case for case in review.cases_with_history()}
    first = [case_id for case_id in SHORTLIST if case_id in cases]
    return [cases[case_id] for case_id in first + [case_id for case_id in cases if case_id not in first]]


def page_panel(cases, page, highlighted=None):
    """One page of cases, with only what the test gate reports about each. One
    of them may be highlighted, by its place in the whole list."""
    table = Table(title_justify="left", header_style=LABEL, box=None, padding=(0, 2))
    for heading in ("", "#", "case", "submission", "test gate status", "stage", "hours in status"):
        table.add_column(heading, no_wrap=True)
    first = page * PAGE
    for place in range(first, first + PAGE):
        if place >= len(cases):
            table.add_row()  # the last page is padded, so every page is the same height
            continue
        case, issue = cases[place], cases[place]["input"]
        # Text, so a value with square brackets in it is shown and not read as markup.
        table.add_row(
            ">" if place == highlighted else "",
            str(place + 1),
            Text(case["id"], style="bold"),
            Text(str(issue["submission_id"])),
            Text(str(issue["gate_status"])),
            Text(str(issue["stage"])),
            str(issue["hours_in_status"]),
            style="reverse" if place == highlighted else None,
        )
    last = min(first + PAGE, len(cases))
    return panel(table, f"CASES {first + 1} to {last} of {len(cases)}")


UP, DOWN, RIGHT, LEFT = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"
# What the list takes up at 100 columns: the ten rows, the headings, the
# panel's two edges, the reviewer, a blank line and the keys. The list is
# measured each time it is drawn, since a narrower window wraps the keys.
LIST_HEIGHT = PAGE + 6
# What r does depends on whether the reviewer was named on the command line.
KEYS = "up/down move   left/right page   Enter run this case   {r}   q quit"


def rows(text):
    """How many lines of the screen a line of this length takes up."""
    return max(1, -(-len(text) // screen.width))


def list_block(cases, place, reviewer, about_reviewer, given):
    """The list as it is drawn: the page, who the run is for, and the keys."""
    # The name is shown with a capital; the saved name is as it was typed.
    named = Text(
        "  reviewer: ", style=LABEL, no_wrap=True, overflow="ellipsis"
    )  # one line, whatever was typed
    named.append(shown_name(reviewer) if reviewer else "nobody named", style="bold")
    named.append(about_reviewer)
    r_does = "r choose brief or full" if given else "r name the reviewer"
    keys = Text(f"  {KEYS.format(r=r_does)}", style=f"bold {PERSON}")
    return Group(page_panel(cases, place // PAGE, highlighted=place), named, Text(""), keys)


def choose_with_keys(cases, ask, key, given=None, reviewer=None, place=0):
    """Move a highlight through the list and pick with Enter.

    The list is drawn again in the same place on each key, so it does not
    scroll down the screen. Returns (case ID, reviewer or None), None if the
    person quit, or "typed" if single keys cannot be read here.
    """
    # `given` is a reviewer named on the command line: shown from the start, and
    # not asked for. `reviewer` and `place` are where an earlier visit to the list left off.
    reviewer = reviewer or given
    about_reviewer = about(reviewer) if reviewer else ""
    lines_up = 0  # how far above the cursor the list on the screen starts
    with key_mode():
        while True:
            # Wipe the list and anything typed under it, to draw it again in
            # the same place. Nothing above the list is touched.
            wipe_up(lines_up)
            block = list_block(cases, place, reviewer, about_reviewer, given)
            screen.print(block)
            lines_up = len(screen.render_lines(block, pad=False))

            try:
                pressed = key()
            except (EOFError, KeyboardInterrupt):
                return None
            if pressed is None:
                return "typed"
            if pressed in (UP, "k"):
                place = (place - 1) % len(cases)
            elif pressed in (DOWN, "j"):
                place = (place + 1) % len(cases)
            elif pressed in (RIGHT, " "):
                place = min(place + PAGE, len(cases) - 1) if place // PAGE < (len(cases) - 1) // PAGE else 0
            elif pressed == LEFT:
                place = max(place - PAGE, 0) if place >= PAGE else (len(cases) - 1) // PAGE * PAGE
            elif pressed in ("\r", "\n"):
                # The list has done its job: wipe it, with the blank line above
                # it, so the issue is shown in its place and not under it.
                wipe_up(lines_up + 1)
                return cases[place]["id"], reviewer
            elif pressed in ("q", "Q", "\x03", "\x04"):  # q, Ctrl+C, Ctrl+D or closed input
                return None
            elif pressed in ("r", "R"):
                try:
                    reviewer, about_reviewer, question_lines = name_reviewer(ask, given, key)
                except (EOFError, KeyboardInterrupt):
                    return None
                lines_up += question_lines  # the questions are wiped with the list, and the list drawn again


def shown_name(name):
    return name[:1].upper() + name[1:]


def about(name):
    """A few words for the list on how a reviewer is shown the agent's conclusion."""
    # Full is what a reviewer with nothing saved is shown.
    return f" (agent's conclusion shown: {review.saved_style(review.memory, name) or 'full'})"


def one_key(prompt, key, wanted):
    """Ask a question answered by one key, with no Enter after it.

    Returns the key if it is one of `wanted`, "" for Enter, or None if single
    keys cannot be read here. Any other key is ignored. Ctrl+C, Ctrl+D or
    closed input raises KeyboardInterrupt.
    """
    screen.print(Text(prompt, style=f"bold {PERSON}"), end="")
    while True:
        pressed = key()
        if pressed is None:
            screen.print()
            return None
        if pressed in ("\x03", "\x04"):
            screen.print()
            raise KeyboardInterrupt
        if pressed in ("\r", "\n") or pressed in tuple(wanted):
            screen.print(Text(pressed.strip()))
            return pressed.strip()


def name_reviewer(ask, given=None, key=None):
    """Ask who the reviewer is, and how they want the agent's conclusion shown.

    A reviewer `given` on the command line is not asked for again; only the
    preference is. The preference is saved the way `review.py prefer` saves
    it, in the memory store, so it holds for later runs too. Returns (the
    reviewer or None, a few words about them for the list, how many lines of
    the screen the questions took, so the list can be drawn over them).
    """
    name, lines = given, 0
    if name is None:
        prompt = "  type the reviewer's name and press Enter (empty for nobody): "
        with line_mode():
            typed = ask(prompt)
        lines += rows(prompt + typed)
        name = typed.strip() or None
        if name is None:
            return None, "", lines
        if reviewer_problem(name):
            return None, f" (not a usable name: {name})", lines
    kept = review.saved_style(review.memory, name) or "full"
    prompt = (
        f"  agent's conclusion for {shown_name(name)}: b brief (first sentence), f full, Enter keeps {kept}: "
    )
    wanted = one_key(prompt, key, "bBfF") if key else None
    lines += rows(prompt + " ")
    if wanted is None:  # single keys cannot be read here: a typed line
        with line_mode():
            wanted = ask(prompt)
        lines += rows(prompt + wanted)
    style = {"b": "brief", "brief": "brief", "f": "full", "full": "full"}.get(wanted.strip().lower())
    if style:
        try:
            printed_by(review.prefer, name, style)
        except ValueError:  # one of the store's rarer rules about names
            return None, f" (not a usable name: {name})", lines
    return name, about(name), lines


def choose_case(ask=ask_person, key=None, reviewer=None, chosen_before=None):
    """Pick a case from the list. Returns (case ID, reviewer or None), or None
    if nothing was picked.

    With `key`, a highlight is moved with the arrow keys. Without it, where
    single keys cannot be read, or in a window too short to hold the list, the
    list is paged and a number or ID is typed.

    `reviewer` is one named on the command line. `chosen_before` is what an
    earlier visit to the list returned: the list opens on that case, for that
    reviewer.
    """
    cases = cases_in_order()
    ids = [case["id"] for case in cases]
    before_case, before_reviewer = chosen_before or (None, None)
    place = ids.index(before_case) if before_case in ids else 0
    pages = (len(cases) + PAGE - 1) // PAGE
    screen.print()
    if key and screen.height > LIST_HEIGHT + 3:  # drawing in place needs the whole list on the screen
        chosen = choose_with_keys(cases, ask, key, reviewer, before_reviewer, place)
        if chosen != "typed":
            return chosen
    page = place // PAGE
    reviewer = before_reviewer or reviewer
    screen.print(page_panel(cases, page))
    while True:
        try:
            typed = ask("a number or a case ID to run it, Enter for more, b to go back, q to quit: ")
        except (EOFError, KeyboardInterrupt):
            screen.print()
            return None
        words = typed.split()
        if not words:  # Enter: the next page, and back to the first page after the last
            page = (page + 1) % pages
            screen.print()
            screen.print(page_panel(cases, page))
        elif words[0].lower() in ("b", "back"):
            page = (page - 1) % pages
            screen.print()
            screen.print(page_panel(cases, page))
        elif words[0].lower() in ("q", "quit"):
            return None
        else:
            # A reviewer may follow the case: "1 alice" or "dev-04 alice".
            named = words[1] if len(words) > 1 else reviewer
            if len(words) > 1 and reviewer_problem(named):
                screen.print(Text(f"  {named} cannot be a reviewer: {reviewer_problem(named)}", style="red"))
            elif words[0].isdigit() and 1 <= int(words[0]) <= len(cases):
                return ids[int(words[0]) - 1], named
            elif words[0] in ids:
                return words[0], named
            else:
                screen.print(Text(f"  there is no case {words[0]}", style="red"))


def from_command_line(arguments, key, ask=ask_person):
    """Work out what was asked for. Returns (case ID, reviewer or None), or
    None if nothing is to be run.

    One word that is a case runs that case. One word that is not a case is a
    reviewer: the list is shown for them. Two words are a case and a reviewer.
    """
    if len(arguments) > 2:
        sys.exit(__doc__)
    known = {case["id"] for case in review.cases_with_history()}
    case_id = arguments[0] if arguments and (len(arguments) == 2 or arguments[0] in known) else None
    reviewer = arguments[-1] if arguments and (len(arguments) == 2 or not case_id) else None
    if reviewer and reviewer_problem(reviewer):
        not_a_case = "" if case_id else " is not a case, and"
        sys.exit(f"{reviewer}{not_a_case} cannot be a reviewer: {reviewer_problem(reviewer)}")
    if case_id:
        return case_id, reviewer
    return choose_case(ask, key, reviewer)


def main(arguments, ask=ask_person, key=None):
    """Run what the command line asked for. A case named there is run once.
    Otherwise cases are picked from the list, one after another, until the
    person quits."""
    known = {case["id"] for case in review.cases_with_history()}
    named_case = len(arguments) == 2 or (len(arguments) == 1 and arguments[0] in known)
    chosen = from_command_line(arguments, key, ask)  # the first pick from the list, if no case was named
    if named_case:
        run_case(*chosen, ask=ask, key=key)
        return
    reviewer = arguments[0] if arguments else None
    while chosen:
        outcome = run_case(*chosen, ask=ask, key=key, back=True)
        if outcome == "quit":
            return
        if outcome == "done" and enter_or(
            "press Enter to go back to the list, or q to quit: ", ask, key, "q"
        ):
            return
        if outcome == "back":
            # The person changed their mind before anything ran: wipe the issue
            # they looked at, and show the list in its place.
            wipe_up(drawn["issue"])
        chosen = choose_case(ask, key, reviewer, chosen_before=chosen)
    screen.print(Text("no case chosen"))


if __name__ == "__main__":
    try:
        main(sys.argv[1:], key=read_key if sys.stdin.isatty() else None)
    finally:
        wait_for_all_tracers()  # send what has been traced, however the console ends
