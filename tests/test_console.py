"""The demo console: a display layer over review.py. No model, no network."""

import importlib
import sqlite3

import pytest

from fakes import broken, context, fake_analyzer, fake_classifier, fake_synthesizer, issue

LONG_LINE = "ERROR [runner-7] " + "timeout waiting for the integration environment to answer " * 4
CASE = {
    "id": "case-1",
    "kind": "KIND-NEVER-SHOWN",
    "rule": "RULE-NEVER-SHOWN",
    "pair": "PAIR-NEVER-SHOWN",
    "cause": "CAUSE-NEVER-SHOWN",
    "input": issue(log_excerpt=f"first line of the log\n{LONG_LINE}\n[bracketed] last line"),
    "label": {"true_class": "LABEL-NEVER-SHOWN", "acceptable_actions": ["ACTIONS-NEVER-SHOWN"]},
}


@pytest.fixture
def demo(monkeypatch, tmp_path):
    """console.py over review.py's own graph, with fake model steps, one made-up
    case and a stand-in for sending feedback. Returns (console, review, sent)."""
    # review.py opens its two database files when imported; keep them out of the repo.
    monkeypatch.chdir(tmp_path)
    import console as console_module
    import review as review_module
    from agent import graph

    review = importlib.reload(review_module)
    console = importlib.reload(console_module)
    console.screen.width = 100

    monkeypatch.setattr(graph, "classifier", fake_classifier("stuck"))
    monkeypatch.setattr(graph, "analyzer", fake_analyzer(True))
    monkeypatch.setattr(graph, "synthesizer", fake_synthesizer("escalate_environment", "Escalate it."))
    monkeypatch.setattr(review, "cases_with_history", lambda: [CASE])
    sent = []

    def stand_in(trace_id, answer):
        sent.append(answer)
        print(f"  recorded human_review={answer['decision']} on trace {trace_id}")

    monkeypatch.setattr(review, "send_feedback", stand_in)
    return console, review, sent


def answers(*typed):
    """A stand-in for the keyboard: Enter to start the run, then each question
    gets the next answer."""
    waiting = ["", *typed]

    def ask(prompt):
        if not waiting:
            raise EOFError
        return waiting.pop(0)

    return ask


def answers_only(*typed):
    """A stand-in for the keyboard in the list of cases: each question gets the
    next answer."""
    waiting = list(typed)

    def ask(prompt):
        if not waiting:
            raise EOFError
        return waiting.pop(0)

    return ask


def the_run(review):
    """The one run these tests start: (its thread ID, its saved state)."""
    (thread_id,) = {saved.config["configurable"]["thread_id"] for saved in review.checkpointer.list(None)}
    return thread_id, review.graph.get_state(review.config_for(thread_id))


def test_the_run_waits_for_enter_after_the_issue_is_shown(demo, capsys):
    console, review, _ = demo
    seen = []

    def ask(prompt):
        # What is true at the moment of each question: the prompt, what is on
        # the screen so far, and whether any step of the run has been saved.
        seen.append((prompt, capsys.readouterr().out, list(review.checkpointer.list(None))))
        if len(seen) > 1:
            raise EOFError
        return ""

    console.run_case("case-1", ask=ask)
    prompt, on_screen, saved = seen[0]
    assert prompt == "press Enter to have the agent triage this issue, or q to quit: "
    assert "THE ISSUE" in on_screen and "[bracketed] last line" in on_screen
    assert "THE RUN" not in on_screen
    assert saved == []  # nothing has run yet
    assert seen[1][2]  # after Enter, the run was made


@pytest.mark.parametrize("at_the_prompt", ["q", " Q ", EOFError(), KeyboardInterrupt()])
def test_quitting_before_the_run_starts_nothing(demo, capsys, at_the_prompt):
    console, review, _ = demo

    def ask(prompt):
        if isinstance(at_the_prompt, BaseException):  # no keyboard, or Ctrl+C
            raise at_the_prompt
        return at_the_prompt

    console.run_case("case-1", ask=ask)
    printed = capsys.readouterr().out
    assert "not started" in printed
    assert "THE RUN" not in printed
    assert list(review.checkpointer.list(None)) == []


@pytest.mark.parametrize("pressed", ["q", "Q", "\x03", "\x04"])
def test_one_key_quits_before_the_run_with_no_enter(demo, capsys, pressed):
    console, review, _ = demo
    keys = iter(["x", pressed])  # a stray key is ignored; the next one quits
    console.run_case("case-1", ask=answers(), key=lambda: next(keys))
    printed = capsys.readouterr().out
    assert "press Enter to have the agent triage this issue, or q to quit: " in printed
    assert "not started" in printed
    assert list(review.checkpointer.list(None)) == []


def test_enter_as_one_key_starts_the_run(demo, capsys):
    console, review, _ = demo
    typed = iter(["q"])  # the decision is still a typed line
    console.run_case("case-1", ask=lambda prompt: next(typed), key=lambda: "\r")
    printed = capsys.readouterr().out
    assert "THE RUN" in printed
    thread_id, snapshot = the_run(review)
    assert snapshot.interrupts
    assert f"left waiting: {thread_id}" in printed


def test_ctrl_c_at_the_decision_leaves_the_run_waiting(demo, capsys):
    console, review, sent = demo
    typed = iter([""])

    def ask(prompt):
        try:
            return next(typed)
        except StopIteration:
            raise KeyboardInterrupt

    console.run_case("case-1", ask=ask)
    thread_id, snapshot = the_run(review)
    assert snapshot.interrupts  # still waiting
    assert sent == []
    assert f"left waiting: {thread_id}" in capsys.readouterr().out


def test_the_proposal_carries_every_line_review_prints(demo, capsys):
    console, review, _ = demo
    console.run_case("case-1", ask=answers("q"))
    printed = capsys.readouterr().out
    _, snapshot = the_run(review)
    state = snapshot.values
    for line in review.proposal_lines(snapshot.interrupts[0].value, state):
        assert line.strip() in printed
    assert f"the rules said:   {state['rule_class']}" in printed
    assert "the agent says:   stuck   (overrode the rules)" in printed
    assert "proposed action:  escalate_environment" in printed
    assert "Escalate it." in printed


def test_the_issue_is_shown_in_full_and_the_steps_are_named_by_stage(demo, capsys):
    console, _, _ = demo
    console.run_case("case-1", ask=answers("q"))
    printed = capsys.readouterr().out
    assert "status at the test gate" in printed
    assert "[bracketed] last line" in printed
    assert " ".join(LONG_LINE.split()) in " ".join(printed.replace("│", " ").split())
    stage_1, stage_2, stage_3 = (printed.index(f"Stage {n}") for n in (1, 2, 3))
    assert stage_1 < printed.index("context ") < printed.index("rules ") < stage_2
    assert stage_2 < printed.index("classify ") < printed.index("synthesize ") < stage_3
    assert stage_3 < printed.index("guards ") < printed.index("waiting for a person")
    assert "deep dive   " not in printed  # the quick look was sure, so the step did not run


def test_each_step_says_what_it_found(demo, capsys):
    console, review, _ = demo
    console.run_case("case-1", ask=answers("q"))
    run = capsys.readouterr().out.split("THE RUN")[1].split("THE PROPOSAL")[0]
    _, snapshot = the_run(review)
    state = snapshot.values
    assert "context     0 of 0 other submissions on this release line are failing" in run
    assert "2 hours in this status; this stage typically takes 3.0" in run
    assert f"rules       {state['rule_class']}   (rule: {state['rule_name']})" in run
    assert "code decides" not in run
    assert "classify    stuck   (overrode the rules)" in run
    assert "analyze     sure of its finding, so no deep dive" in run
    assert "synthesize  wrote the conclusion; picked escalate_environment" in run
    assert "guards      checked the answer; changed nothing" in run
    assert "approval    waiting for a person" in run


def test_a_deep_dive_shows_its_lookups_and_a_failed_step_says_so(demo, capsys, monkeypatch):
    from agent import graph

    console, _, _ = demo
    monkeypatch.setattr(graph, "analyzer", fake_analyzer(False))
    lookups = [{"name": "list_gate_runs"}, {"name": "list_tickets"}]
    found = graph.Analysis(finding="found", conclusive=True)
    monkeypatch.setattr(graph, "investigate", lambda model, schema, prompt: (found, lookups))
    monkeypatch.setattr(graph, "synthesizer", broken())
    console.run_case("case-1", ask=answers("q"))
    run = capsys.readouterr().out.split("THE RUN")[1].split("THE PROPOSAL")[0]
    assert "analyze     not sure, so it takes a closer look" in run
    assert "deep dive   looked up list_gate_runs, list_tickets" in run
    assert "synthesize  the model did not answer; the fallback was used: picked " in run


def test_a_failed_deep_dive_says_the_model_did_not_answer(demo, capsys, monkeypatch):
    from agent import graph

    console, _, _ = demo
    monkeypatch.setattr(graph, "analyzer", fake_analyzer(False))

    def down(model, schema, prompt):
        raise TimeoutError("down")

    monkeypatch.setattr(graph, "investigate", down)
    console.run_case("case-1", ask=answers("q"))
    printed = capsys.readouterr().out
    assert "deep dive   the model did not answer; the fallback was used" in printed
    assert "deep dive:        failed; the quick look's finding is used" in printed


def test_a_failed_model_step_and_a_guard_note_are_shown(demo, capsys, monkeypatch):
    from agent import graph

    console, review, _ = demo
    monkeypatch.setattr(graph, "classifier", broken())
    # Not an action the rules' class allows, so a guard replaces it.
    monkeypatch.setattr(graph, "synthesizer", fake_synthesizer("wait_and_watch", "Wait."))
    console.run_case("case-1", ask=answers("q"))
    printed = capsys.readouterr().out
    _, snapshot = the_run(review)
    assert snapshot.values["guard_notes"]
    assert "a model step failed: classify: TimeoutError: model unavailable" in printed
    assert "the model did not answer; the rules' class stands" in printed
    assert "a guard stepped in:" in printed
    # In the run: the classify line carries the same verdict, and each note has a line.
    assert "classify    code_defect   (the model did not answer; the rules' class stands)" in printed
    for note in snapshot.values["guard_notes"]:
        assert " ".join(note.split()) in " ".join(printed.split("THE PROPOSAL")[0].split())
    assert "changed nothing" not in printed


def test_a_class_decided_by_code_is_said_in_place_of_the_classify_line(demo, capsys, monkeypatch):
    from agent import graph

    console, _, _ = demo
    monkeypatch.setattr(graph, "fetch_context", lambda record: context(outage=True, peers_failing=3))
    console.run_case("case-1", ask=answers("q"))
    printed = capsys.readouterr().out
    run = printed.split("THE RUN")[1].split("THE PROPOSAL")[0]
    assert "rules       environment_error   (rule: outage)" in run
    assert "code decides this one; the model is not asked for the class" in run
    assert "classify" not in run
    assert "3 of 0 other submissions on this release line are failing" in run


def test_a_refused_edit_leaves_the_run_waiting(demo, capsys):
    console, review, sent = demo
    console.run_case("case-1", ask=answers("e", "fix_then_rerun", "q"))
    printed = capsys.readouterr().out
    assert "not sent: an edit must be one of: " in printed
    assert "THE RESULT" not in printed
    thread_id, snapshot = the_run(review)
    assert snapshot.interrupts  # still waiting
    assert sent == []
    assert f"left waiting: {thread_id}" in printed


def test_approve_records_the_action_once(demo, capsys, tmp_path):
    console, review, sent = demo
    console.run_case("case-1", ask=answers("a"))
    printed = capsys.readouterr().out
    thread_id, snapshot = the_run(review)
    assert snapshot.values["review"] == "approved"
    assert sent == [{"decision": "approve"}]
    actions = sqlite3.connect(tmp_path / "actions.db")
    rows = actions.execute("SELECT thread_id, action FROM actions").fetchall()
    assert rows == [(thread_id, "escalate_environment")]
    assert "FINISHED, review=approved, final action=escalate_environment" in printed
    assert "recorded against SUB-1: escalate_environment (approved" in printed
    assert "recorded human_review=approve on trace" in printed


def test_an_edit_within_the_class_is_recorded(demo, capsys):
    console, review, sent = demo
    console.run_case("case-1", ask=answers("e", "wait_and_watch"))
    assert "final action=wait_and_watch" in capsys.readouterr().out
    assert sent == [{"decision": "edit", "action": "wait_and_watch"}]


def test_nothing_from_the_answer_is_printed_and_no_line_is_too_wide(demo, capsys):
    console, _, _ = demo
    console.run_case("case-1", "alice", ask=answers("x", "e", "fix_then_rerun", "a"))
    printed = capsys.readouterr().out
    assert "NEVER-SHOWN" not in printed
    assert "reviewer alice: nothing saved, showing full" in printed
    assert max(len(line) for line in printed.splitlines()) <= 100


# --- picking a case from the list ---


def listed(case_id):
    """A made-up case for the list, with an answer that must never be shown."""
    return CASE | {"id": case_id, "input": issue(submission_id=f"SUB-{case_id}")}


@pytest.fixture
def many(demo, monkeypatch):
    """Twenty-three cases in file order, the demo's shortlist scattered among them."""
    console, review, _ = demo
    in_files = [f"dev-{n:02d}" for n in range(1, 8)] + [f"test-{n:02d}" for n in range(10, 26)]
    monkeypatch.setattr(review, "cases_with_history", lambda: [listed(case_id) for case_id in in_files])
    return console


def test_the_list_shows_the_shortlist_first_and_ten_at_a_time(many, capsys):
    console = many
    assert console.choose_case(ask=answers_only("q")) is None
    printed = capsys.readouterr().out
    assert "CASES 1 to 10 of 23" in printed
    rows = [line.split()[2] for line in printed.splitlines() if "SUB-" in line]
    assert ">" not in printed  # nothing is highlighted when a number or ID is typed
    # test-35 and test-41 are on the shortlist but not among these cases, so they are left out.
    assert rows == ["dev-04", "test-25", "test-13", "test-17", "dev-01", "dev-02", "dev-03", "dev-05"] + [
        "dev-06",
        "dev-07",
    ]
    assert "test gate status" in printed and "hours in status" in printed
    assert "NEVER-SHOWN" not in printed


def test_enter_pages_on_and_round_again_and_b_goes_back(many, capsys):
    console = many
    console.choose_case(ask=answers_only("", "", "", "b", "q"))
    printed = capsys.readouterr().out
    shown = [line.split("CASES ")[1].split(" ─")[0] for line in printed.splitlines() if "CASES " in line]
    assert shown == ["1 to 10 of 23", "11 to 20 of 23", "21 to 23 of 23", "1 to 10 of 23", "21 to 23 of 23"]
    assert max(len(line) for line in printed.splitlines()) <= 100


def test_a_case_is_picked_by_number_or_by_id_with_or_without_a_reviewer(many):
    console = many
    assert console.choose_case(ask=answers_only("2")) == ("test-25", None)
    assert console.choose_case(ask=answers_only("", "12 alice")) == ("test-11", "alice")
    assert console.choose_case(ask=answers_only("test-20")) == ("test-20", None)
    assert console.choose_case(ask=answers_only("dev-04 alice")) == ("dev-04", "alice")


def test_a_case_that_does_not_exist_is_asked_for_again(many, capsys):
    console = many
    assert console.choose_case(ask=answers_only("test-99", "0", "24", "1")) == ("dev-04", None)
    printed = capsys.readouterr().out
    assert "there is no case test-99" in printed
    assert "there is no case 24" in printed


def test_ctrl_c_in_the_list_picks_nothing(many):
    console = many

    def ctrl_c(prompt):
        raise KeyboardInterrupt

    assert console.choose_case(ask=ctrl_c) is None


# --- picking with the arrow keys ---

UP, DOWN, RIGHT, LEFT, ENTER = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D", "\r"


def keys(*pressed):
    waiting = list(pressed)
    return lambda: waiting.pop(0)


def highlighted(printed):
    """The case on the highlighted line each time the list was drawn."""
    return [line.split()[3] for line in printed.splitlines() if line.split()[1:2] == [">"]]


def test_the_highlight_starts_on_the_first_case_and_enter_picks_it(many, capsys):
    console = many
    assert console.choose_case(ask=answers_only(), key=keys(ENTER)) == ("dev-04", None)
    printed = capsys.readouterr().out
    assert highlighted(printed) == ["dev-04"]
    assert "up/down move" in printed and "r name the reviewer" in printed
    assert "reviewer: nobody named" in printed
    assert "NEVER-SHOWN" not in printed
    assert max(len(line) for line in printed.splitlines()) <= 100


def test_the_arrows_move_the_highlight_and_round_at_both_ends(many, capsys):
    console = many
    picked = console.choose_case(ask=answers_only(), key=keys(DOWN, DOWN, UP, UP, UP, ENTER))
    moves = highlighted(capsys.readouterr().out)
    # Up from the first case is the last one: test-24, since test-25 is on the shortlist.
    assert moves == ["dev-04", "test-25", "test-13", "test-25", "dev-04", "test-24"]
    assert picked == ("test-24", None)


def test_left_and_right_change_the_page_and_the_highlight_goes_with_it(many, capsys):
    console = many
    picked = console.choose_case(ask=answers_only(), key=keys(DOWN, RIGHT, RIGHT, RIGHT, LEFT, ENTER))
    printed = capsys.readouterr().out
    shown = [line.split("CASES ")[1].split(" ─")[0] for line in printed.splitlines() if "CASES " in line]
    assert shown == ["1 to 10 of 23", "1 to 10 of 23", "11 to 20 of 23", "21 to 23 of 23"] + [
        "1 to 10 of 23",
        "21 to 23 of 23",
    ]
    assert picked == (highlighted(printed)[-1], None)


def test_every_page_is_the_same_height_so_it_can_be_drawn_in_place(many, capsys):
    console = many
    console.choose_case(ask=answers_only(), key=keys(RIGHT, RIGHT, "q"))
    lines = capsys.readouterr().out.strip("\n").splitlines()
    assert len(lines) == 3 * console.LIST_HEIGHT


def test_a_reviewer_is_named_and_their_preference_saved_from_the_list(many, capsys):
    import review

    console = many
    # b needs no Enter after it; a stray key before it is ignored
    picked = console.choose_case(ask=answers_only(" alice "), key=keys("r", "x", "b", DOWN, ENTER))
    assert picked == ("test-25", "alice")
    assert review.saved_style(review.memory, "alice") == "brief"
    printed = capsys.readouterr().out
    assert "reviewer: Alice (agent's conclusion shown: brief)" in printed
    # The list is drawn three times, each the same height, so the second and
    # third can be drawn over the one before and no second box appears. The
    # one extra line is the question, which is wiped with the list.
    assert len(printed.strip("\n").splitlines()) == 3 * console.LIST_HEIGHT + 1
    assert "agent's conclusion for Alice: b brief (first sentence), f full, Enter keeps full: b" in printed


def test_enter_keeps_what_a_reviewer_has_saved(many, capsys):
    import review

    console = many
    review.prefer("alice", "brief")
    # Enter at the question keeps brief; then r again and an empty name: nobody
    console.choose_case(ask=answers_only("alice", ""), key=keys("r", ENTER, "r", ENTER))
    assert review.saved_style(review.memory, "alice") == "brief"
    printed = capsys.readouterr().out
    assert "reviewer: Alice (agent's conclusion shown: brief)" in printed
    assert printed.rstrip().splitlines()[-3].strip() == "reviewer: nobody named"


def test_a_reviewer_with_nothing_saved_is_shown_as_full_and_nothing_is_saved(many, capsys):
    import review

    console = many
    assert console.choose_case(ask=answers_only("bob"), key=keys("r", ENTER, ENTER)) == ("dev-04", "bob")
    assert review.saved_style(review.memory, "bob") is None
    assert "reviewer: Bob (agent's conclusion shown: full)" in capsys.readouterr().out


def test_a_name_the_store_cannot_use_is_refused_and_said(many, capsys):
    console = many
    long_name = "a." + "b" * 200
    picked = console.choose_case(ask=answers_only("alice.smith", long_name), key=keys("r", "r", ENTER))
    assert picked == ("dev-04", None)
    printed = capsys.readouterr().out
    assert "reviewer: nobody named (not a usable name: alice.smith)" in printed
    # Even a very long name keeps the reviewer on one line, so the list keeps its height.
    assert len(printed.strip("\n").splitlines()) == 3 * console.LIST_HEIGHT
    assert max(len(line) for line in printed.splitlines()) <= 100


def test_a_brief_preference_set_in_the_list_shortens_the_conclusion_shown(many, capsys, monkeypatch):
    from agent import graph

    console = many
    monkeypatch.setattr(
        graph, "synthesizer", fake_synthesizer("escalate_environment", "Escalate it. Then wait.")
    )
    picked = console.choose_case(ask=answers_only("alice"), key=keys("r", "b", ENTER))
    capsys.readouterr()
    console.run_case(*picked, ask=answers("q"))
    printed = capsys.readouterr().out
    assert "reviewer alice: brief" in printed
    assert "Escalate it." in printed and "Then wait." not in printed


@pytest.mark.parametrize("pressed", ["q", "\x03", "\x04"])
def test_q_in_the_highlighted_list_picks_nothing(many, pressed):
    console = many
    assert console.choose_case(ask=answers_only(), key=keys(DOWN, pressed)) is None


def test_where_single_keys_cannot_be_read_a_number_is_typed(many):
    console = many
    assert console.choose_case(ask=answers_only("3"), key=lambda: None) == ("test-13", None)


# --- a reviewer named on the command line ---


def test_one_word_that_is_a_case_runs_that_case_with_no_list(many, capsys):
    console = many
    assert console.from_command_line(["test-20"], keys()) == ("test-20", None)
    assert console.from_command_line(["test-20", "roger"], keys()) == ("test-20", "roger")
    assert "CASES" not in capsys.readouterr().out


def test_one_word_that_is_not_a_case_is_the_reviewer_for_the_list(many, capsys):
    import review

    console = many
    review.prefer("roger", "full")
    capsys.readouterr()
    assert console.from_command_line(["roger"], keys(DOWN, ENTER)) == ("test-25", "roger")
    printed = capsys.readouterr().out
    # Shown from the first draw, and again in the one after DOWN.
    assert printed.count("reviewer: Roger (agent's conclusion shown: full)") == 2


def test_r_asks_a_reviewer_from_the_command_line_only_for_the_preference(many, capsys):
    import review

    console = many
    picked = console.choose_case(ask=answers_only(), key=keys("r", "b", ENTER), reviewer="roger")
    assert picked == ("dev-04", "roger")  # nothing was typed as a line: the name was not asked for
    assert review.saved_style(review.memory, "roger") == "brief"
    printed = capsys.readouterr().out
    assert "agent's conclusion for Roger: b brief (first sentence), f full, Enter keeps full: b" in printed
    assert "r choose brief or full" in printed and "name the reviewer" not in printed
    assert "reviewer: Roger (agent's conclusion shown: full)" in printed
    assert "reviewer: Roger (agent's conclusion shown: brief)" in printed
    assert len(printed.strip("\n").splitlines()) == 2 * console.LIST_HEIGHT + 1


def test_where_single_keys_stop_working_the_preference_is_a_typed_line(many):
    import review

    console = many
    console.name_reviewer(answers_only("f"), given="roger", key=lambda: None)
    assert review.saved_style(review.memory, "roger") == "full"


def test_a_word_that_is_neither_a_case_nor_a_usable_name_stops_with_a_reason(many):
    console = many
    with pytest.raises(SystemExit, match="roger.smith is not a case, and cannot be a reviewer"):
        console.from_command_line(["roger.smith"], keys())


def test_no_words_shows_the_list_for_nobody(many, capsys):
    console = many
    assert console.from_command_line([], keys(ENTER)) == ("dev-04", None)
    assert "reviewer: nobody named" in capsys.readouterr().out


# --- the other answers, and runs that need no person ---


def rows_in_actions(tmp_path):
    if not (tmp_path / "actions.db").exists():
        return []
    return (
        sqlite3.connect(tmp_path / "actions.db").execute("SELECT thread_id, action FROM actions").fetchall()
    )


@pytest.mark.parametrize("typed", ["r", "reject"])
def test_reject_resumes_the_run_and_records_no_action(demo, capsys, tmp_path, typed):
    console, review, sent = demo
    console.run_case("case-1", ask=answers(typed))
    printed = capsys.readouterr().out
    _, snapshot = the_run(review)
    assert snapshot.values["review"] == "rejected"
    assert sent == [{"decision": "reject"}]
    assert "THE RESULT" in printed and "FINISHED, review=rejected, final action=None" in printed
    assert rows_in_actions(tmp_path) == []


def test_the_long_word_approve_is_taken_too(demo, capsys):
    console, review, sent = demo
    console.run_case("case-1", ask=answers("approve"))
    assert sent == [{"decision": "approve"}]
    assert "THE RESULT" in capsys.readouterr().out


def test_a_refused_edit_then_approve_records_once(demo, capsys, tmp_path):
    console, review, sent = demo
    console.run_case("case-1", ask=answers("e", "fix_then_rerun", "e", "", "a"))
    printed = capsys.readouterr().out
    assert printed.count("not sent: an edit must be one of: ") == 2  # a wrong action, then an empty one
    assert printed.index("not sent:") < printed.index("THE RESULT")
    assert sent == [{"decision": "approve"}]
    thread_id, _ = the_run(review)
    assert rows_in_actions(tmp_path) == [(thread_id, "escalate_environment")]


def test_a_run_that_needs_no_person_ends_in_a_result_panel(demo, capsys, monkeypatch):
    from agent import graph

    console, review, sent = demo
    monkeypatch.setattr(graph, "fetch_context", lambda record: context(submission_state="withdrawn"))
    console.run_case("case-1", ask=answers())  # only Enter to start: nothing else is asked
    printed = capsys.readouterr().out
    _, snapshot = the_run(review)
    assert snapshot.values["review"] == "not_needed"
    assert "code decides this one; the model is not asked for the class" in printed
    assert printed.count("nothing left to triage; the model was not asked") == 2  # analyze and synthesize
    assert "approval    not needed: there is nothing to approve" in printed
    assert "act         finished" in printed
    assert "THE PROPOSAL" not in printed and "[a]pprove" not in printed
    assert "THE RESULT" in printed and "FINISHED, review=not_needed, final action=none" in printed
    assert sent == []


def test_a_failed_quick_look_says_the_model_did_not_answer(demo, capsys, monkeypatch):
    from agent import graph

    console, _, _ = demo
    monkeypatch.setattr(graph, "analyzer", broken())
    console.run_case("case-1", ask=answers("q"))
    assert "analyze     the model did not answer; the fallback was used" in capsys.readouterr().out


def test_the_issue_panel_shows_every_field_and_nothing_else_from_the_record(demo, capsys, monkeypatch):
    console, review, _ = demo
    record = issue(
        submission_id="SUB-77",
        gate_status="running",
        stage="build",
        hours_in_status=7,
        job_events=["queued", "started", "stalled"],
        log_excerpt="2031-01-01T00:00:00Z INFO  a line",
    ) | {"expected": "EXTRA-NEVER-SHOWN"}
    monkeypatch.setattr(review, "cases_with_history", lambda: [CASE | {"input": record}])
    console.run_case("case-1", ask=answers("q"))
    printed = capsys.readouterr().out
    for shown in ("SUB-77", "running", "build", "7", "queued → started → stalled", "INFO  a line"):
        assert shown in printed
    assert "log of this submission, as the test gate reports it:" in printed
    assert "NEVER-SHOWN" not in printed
    console.choose_case(ask=answers_only("q"))
    assert "NEVER-SHOWN" not in capsys.readouterr().out


def test_each_step_is_in_the_colour_of_who_does_it(demo):
    console, _, _ = demo
    state = {"rule_class": "stuck", "issue": issue()}
    assert console.found_by("rules", {"rule_class": "stuck", "rule_name": "x"}, state)[0] == console.CODE
    assert console.found_by("classify", {"triage_class": "stuck"}, state)[0] == console.MODEL
    assert console.found_by("analyze", {"conclusive": True}, state)[0] == console.MODEL
    assert console.found_by("guards", {"guard_notes": []}, state)[0] == console.CODE
    assert console.found_by("approval", {}, state)[0] == console.PERSON
    # No model is called when nothing is wrong, so those lines are code's colour.
    nothing = state | {"triage_class": "nothing_wrong"}
    assert console.found_by("analyze", {"conclusive": True}, nothing)[0] == console.CODE
    assert console.found_by("synthesize", {"action": "none"}, nothing)[0] == console.CODE


# --- when something goes wrong in front of people ---


def test_a_run_that_fails_part_way_says_so_and_how_to_carry_on(demo, capsys, monkeypatch):
    from agent import graph

    console, review, _ = demo

    def store_is_stale(record):
        raise RuntimeError("store.db is out of date")

    monkeypatch.setattr(graph, "fetch_context", store_is_stale)
    console.run_case("case-1", ask=answers())  # does not raise
    printed = capsys.readouterr().out
    thread_id, _ = the_run(review)
    assert "the run stopped: RuntimeError: store.db is out of date" in printed
    assert f"uv run python review.py resume {thread_id}" in printed
    assert "THE PROPOSAL" not in printed


def test_ctrl_c_during_the_run_is_said_plainly(demo, capsys, monkeypatch):
    from agent import graph

    console, _, _ = demo

    def ctrl_c(record):
        raise KeyboardInterrupt

    monkeypatch.setattr(graph, "fetch_context", ctrl_c)
    console.run_case("case-1", ask=answers())
    assert "the run stopped: interrupted with Ctrl+C" in capsys.readouterr().out


def test_a_decision_that_fails_while_resuming_says_so_and_how_to_carry_on(demo, capsys, monkeypatch):
    from agent import graph

    console, review, sent = demo

    def locked(*args):
        raise RuntimeError("actions.db is locked")

    monkeypatch.setattr(graph, "record_action", locked)
    console.run_case("case-1", ask=answers("a"))  # does not raise
    printed = capsys.readouterr().out
    thread_id, _ = the_run(review)
    assert "sending the decision..." in printed
    assert "the decision stopped: RuntimeError: actions.db is locked" in printed
    assert f"uv run python review.py resume {thread_id}" in printed
    assert "THE RESULT" not in printed and sent == []


def test_a_failure_to_reach_langsmith_is_shown_and_the_decision_stands(demo, capsys, monkeypatch):
    console, review, _ = demo

    def refused(trace_id, answer):
        print("  NOT sent to LangSmith (LangSmithError: usage limit); the decision is saved")

    monkeypatch.setattr(review, "send_feedback", refused)
    console.run_case("case-1", ask=answers("a"))
    printed = capsys.readouterr().out
    assert "NOT sent to LangSmith (LangSmithError: usage limit); the decision is saved" in printed
    assert console.result_text("ok\n  NOT sent to LangSmith (x)").spans[0].style == "red"
    _, snapshot = the_run(review)
    assert snapshot.values["review"] == "approved"


def test_a_run_decided_elsewhere_is_not_shown_as_this_decision(demo, capsys):
    console, review, sent = demo

    def ask(prompt):
        # While the prompt is open, someone approves the run from another terminal.
        thread_id, snapshot = the_run(review)
        if snapshot.interrupts:
            review.decide(thread_id, {"decision": "reject"})
            capsys.readouterr()
        return "a"

    console.run_case("case-1", ask=lambda prompt: "" if "press Enter" in prompt else ask(prompt))
    printed = capsys.readouterr().out
    assert "this run is no longer waiting; it was decided elsewhere" in printed
    assert "review=rejected" in printed
    assert sent == [{"decision": "reject"}]  # the approve typed here was not sent


def test_a_name_the_store_refuses_only_when_saving_is_said_in_the_list(many, capsys):
    import review

    console = many
    # "langgraph" can be looked up but not saved under: one of the store's rarer rules.
    picked = console.choose_case(ask=answers_only("langgraph"), key=keys("r", "b", ENTER))
    assert picked == ("dev-04", None)
    assert "reviewer: nobody named (not a usable name: langgraph)" in capsys.readouterr().out
    assert review.saved_style(review.memory, "langgraph") is None


def test_a_reviewer_the_store_cannot_use_stops_with_a_sentence_not_a_traceback(many):
    console, name = many, "alice.smith"
    with pytest.raises(SystemExit, match=f"{name} cannot be a reviewer: "):
        console.from_command_line(["test-20", name], keys())
    with pytest.raises(SystemExit, match=f"{name} is not a case, and cannot be a reviewer: "):
        console.from_command_line([name], keys())


def test_a_bad_reviewer_typed_after_a_case_is_asked_for_again(many, capsys):
    console = many
    assert console.choose_case(ask=answers_only("1 a.b", "1 alice")) == ("dev-04", "alice")
    assert "a.b cannot be a reviewer: " in capsys.readouterr().out


def test_more_than_two_words_shows_how_to_use_it(many):
    console = many
    with pytest.raises(SystemExit, match="uv run python console.py"):
        console.from_command_line(["a", "b", "c"], keys())


# --- keys: the ones that are not arrows, and the ones to ignore ---


def test_j_k_space_and_left_from_a_middle_page_move_as_the_arrows_do(many, capsys):
    console = many
    picked = console.choose_case(ask=answers_only(), key=keys("j", "j", "k", " ", LEFT, ENTER))
    assert highlighted(capsys.readouterr().out) == [
        "dev-04",
        "test-25",
        "test-13",
        "test-25",
        "test-11",
        "test-25",
    ]
    assert picked == ("test-25", None)


@pytest.mark.parametrize("stray", ["�", "é", "\x1b", "x"])
def test_a_key_that_means_nothing_is_ignored_everywhere(many, capsys, stray):
    console = many
    # In the list, at the brief-or-full question, and before the run.
    picked = console.choose_case(ask=answers_only("alice"), key=keys(stray, "r", stray, "f", stray, ENTER))
    assert picked == ("dev-04", "alice")
    capsys.readouterr()
    assert console.start_wanted(answers_only(), keys(stray, ENTER))[0] == "start"
    assert console.start_wanted(answers_only(), keys(stray, "q"))[0] == "quit"


def test_ctrl_c_at_the_brief_or_full_question_leaves_the_list(many):
    console = many
    assert console.choose_case(ask=answers_only("alice"), key=keys("r", "\x03")) is None


def test_before_the_run_a_typed_line_is_asked_for_where_keys_cannot_be_read(demo):
    console, _, _ = demo
    assert console.start_wanted(answers_only(""), lambda: None)[0] == "start"
    assert console.start_wanted(answers_only("q"), lambda: None)[0] == "quit"


# --- going back to the list, and one case after another ---


def test_b_at_the_issue_goes_back_to_the_list_on_the_same_case(many, capsys):
    import review

    console = many
    # down twice and Enter: the third case. b: back. Enter: the same case again. q: quit before the run.
    console.main([], ask=answers_only(), key=keys(DOWN, DOWN, ENTER, "b", ENTER, "q"))
    printed = capsys.readouterr().out
    assert "b to go back to the list" in printed
    assert printed.count("THE ISSUE") == 2 and printed.count("SUB-test-13") >= 2
    assert highlighted(printed)[-1] == "test-13"  # the list came back with the highlight where it was
    assert printed.count("not started") == 2
    assert "THE RUN" not in printed
    assert list(review.checkpointer.list(None)) == []  # going back left nothing behind


def test_the_reviewer_named_in_the_list_is_kept_when_going_back(many, capsys):
    console = many
    console.main([], ask=answers_only("alice"), key=keys("r", "b", ENTER, "b", "q"))
    printed = capsys.readouterr().out
    lines = [line.strip() for line in printed.splitlines()]
    named = [line for line in lines if line.startswith("reviewer: ")]
    assert named[-1] == "reviewer: Alice (agent's conclusion shown: brief)"  # the list as it came back
    assert (
        "r name the reviewer" in [line for line in lines if line.startswith("up/down")][-1]
    )  # still changeable
    assert "reviewer alice: brief" not in printed  # the run was never started


def test_after_a_case_enter_goes_back_to_the_list_for_another(many, capsys):
    import review

    console = many
    # Run the first case and approve it; Enter: back to the list; down, Enter: the second; reject it; q.
    console.main(
        ["roger"], ask=answers_only("a", "r"), key=keys(ENTER, ENTER, ENTER, DOWN, ENTER, ENTER, "q")
    )
    printed = capsys.readouterr().out
    assert printed.count("THE RESULT") == 2
    assert printed.count("press Enter to go back to the list, or q to quit: ") == 2
    assert "reviewer roger:" in printed
    runs = {
        saved.config["configurable"]["thread_id"].rsplit("-", 1)[0]
        for saved in review.checkpointer.list(None)
    }
    assert runs == {"dev-04", "test-25"}


def test_q_at_the_issue_ends_the_console_and_a_named_case_is_run_once(many, capsys):
    console = many
    console.main([], ask=answers_only(), key=keys(ENTER, "q"))
    printed = capsys.readouterr().out
    assert printed.count("CASES ") == 1 and "no case chosen" not in printed
    # A case named on the command line: no list, no way back, and no second case.
    console.main(["test-20"], ask=answers_only("q"), key=keys(ENTER))
    printed = capsys.readouterr().out
    assert "CASES" not in printed and "go back to the list" not in printed
    assert "left waiting: test-20-" in printed


def test_quitting_the_list_says_no_case_was_chosen(many, capsys):
    console = many
    console.main([], ask=answers_only(), key=keys("q"))
    assert "no case chosen" in capsys.readouterr().out


def test_going_back_works_where_lines_are_typed_too(many, capsys):
    console = many
    # No single keys: 12 picks a case on the second page, b goes back, and the list opens on that page.
    console.main([], ask=answers_only("", "12", "b", "q"), key=None)
    printed = capsys.readouterr().out
    assert printed.count("THE ISSUE") == 1
    assert printed.rstrip().count("CASES 11 to 20 of 23") == 2


# --- drawing the list in place, on a real terminal ---


@pytest.fixture
def terminal(many):
    """The console writing to a terminal 100 columns wide, kept in a string."""
    import io

    from rich.console import Console

    console = many
    console.screen = Console(file=io.StringIO(), force_terminal=True, width=100, height=40, highlight=False)
    return console


def cursor_moves(console):
    import re

    return re.findall(r"\x1b\[(\d+)A\r\x1b\[J", console.screen.file.getvalue())


def test_the_list_is_drawn_over_itself_by_moving_up_its_own_height(terminal):
    console = terminal
    console.choose_case(ask=answers_only(), key=keys(DOWN, RIGHT, "q"))
    assert cursor_moves(console) == [str(console.LIST_HEIGHT)] * 2
    assert console.screen.file.getvalue().count("CASES ") == 3


def test_the_questions_under_the_list_are_wiped_with_it(terminal):
    console = terminal
    console.choose_case(ask=answers_only("alice"), key=keys("r", "b", "q"))
    assert cursor_moves(console) == [str(console.LIST_HEIGHT + 2)]  # the name, and brief or full
    console.screen.file.seek(0)
    console.screen.file.truncate()
    console.choose_case(ask=answers_only(), key=keys("r", "b", "q"), reviewer="roger")
    assert cursor_moves(console) == [str(console.LIST_HEIGHT + 1)]  # only brief or full


def test_in_a_narrow_window_the_wrapped_keys_line_is_counted(terminal):
    console = terminal
    console.screen.width = 70
    console.choose_case(ask=answers_only(), key=keys(DOWN, "q"))
    (moved,) = cursor_moves(console)
    assert int(moved) > console.LIST_HEIGHT  # the keys take two lines here
    drawn = console.screen.file.getvalue().split("\x1b[J")[1]
    assert drawn.count("\n") == int(moved)


def wipes(console):
    """Each wipe the console made: (how many lines it moved up, whether it also wiped the history)."""
    import re

    found = re.findall(r"\x1b\[(\d+)A\r\x1b\[J(\x1b\[3J)?", console.screen.file.getvalue())
    return [(int(lines), bool(history)) for lines, history in found]


def test_picking_a_case_wipes_the_list_and_going_back_wipes_the_issue(terminal):
    import re

    console = terminal
    console.main([], ask=answers_only(), key=keys(DOWN, ENTER, "b", "q"))
    out = console.screen.file.getvalue()
    assert "\x1b[2J" not in out  # the screen is never cleared: terminals differ in how they do it
    redraw, list_wipe, issue_wipe = wipes(console)
    assert redraw == (console.LIST_HEIGHT, False)  # DOWN: the list drawn over itself
    assert list_wipe == (console.LIST_HEIGHT + 1, False)  # Enter: the list goes, with the blank line above it
    # b: up over exactly what was printed since the list went: the issue, the question, "not started".
    since_the_list = out.split("\x1b[J")[2]
    assert "THE ISSUE" in since_the_list and "not started" in since_the_list
    assert issue_wipe == (since_the_list.count("\n"), False)
    after = re.sub(r"\x1b\[[0-9;]*m", "", out.split("\x1b[J")[3])
    assert "THE ISSUE" not in after and highlighted(after) == ["test-25"]  # then the list alone, where it was


def test_an_issue_too_long_for_the_window_takes_the_terminal_history_with_it(terminal, monkeypatch):
    import review

    console = terminal
    long_log = "\n".join(f"line {number} of the log" for number in range(60))  # taller than the 40-row window
    monkeypatch.setattr(review, "cases_with_history", lambda: [CASE | {"input": issue(log_excerpt=long_log)}])
    console.main([], ask=answers_only(), key=keys(ENTER, "b", "q"))
    _, (lines, history) = wipes(console)
    # The top of the issue has scrolled out of reach, so it cannot be wiped line by line.
    assert lines > 60 and history is True


def test_a_finished_case_is_left_on_the_screen_when_going_back_for_another(terminal):
    console = terminal
    console.main([], ask=answers_only("a"), key=keys(ENTER, ENTER, ENTER, "q"))  # run, approve, back, quit
    out = console.screen.file.getvalue()
    assert "THE RESULT" in out and out.count("CASES 1 to 10") == 2
    assert wipes(console) == [(console.LIST_HEIGHT + 1, False)]  # only the list, when the case was picked
    assert "\x1b[2J" not in out and "\x1b[3J" not in out


def test_going_back_from_the_typed_list_wipes_the_issue_too(terminal):
    console = terminal
    console.main([], ask=answers_only("2", "b", "q"), key=None)
    (wipe,) = wipes(console)
    assert wipe[1] is False
    assert console.screen.file.getvalue().count("CASES 1 to 10") == 2


def test_a_window_too_short_for_the_list_gets_the_typed_list(terminal):
    console = terminal
    console.screen.height = 12
    assert console.choose_case(ask=answers_only("2"), key=keys()) == ("test-25", None)
    assert cursor_moves(console) == []


def test_a_failed_step_is_red_and_the_action_stands_out_on_a_terminal(terminal, monkeypatch):
    from agent import graph

    console = terminal
    monkeypatch.setattr(graph, "classifier", broken())
    console.run_case("dev-01", ask=answers("q"))
    out = console.screen.file.getvalue()
    assert "\x1b[31m  a model step failed: classify" in out  # red
    assert "\x1b[1;7m" in out  # the proposed action, bold and reversed


# --- reading real keys, through a pseudo-terminal ---


@pytest.fixture
def keyboard(monkeypatch):
    """A pseudo-terminal standing in for the keyboard. Returns (console, press, settings)."""
    pytest.importorskip("termios")
    import os
    import pty
    import sys
    import termios

    import console

    master, slave = pty.openpty()
    monkeypatch.setattr(sys, "stdin", os.fdopen(slave, "r", closefd=False))

    def settings():
        # PENDIN is the terminal's own note that input is waiting, not a setting.
        now = termios.tcgetattr(slave)
        now[3] &= ~getattr(termios, "PENDIN", 0)
        return now

    yield console, lambda keys: os.write(master, keys), settings
    os.close(master)
    os.close(slave)


@pytest.mark.parametrize(
    "sent, read",
    [(b"\x1b[A", "\x1b[A"), (b"\x1b[B", "\x1b[B"), (b"q", "q"), (b"\r", "\n"), (b"\x1b", "\x1b")],
)
def test_a_key_is_read_whole_with_no_enter(keyboard, sent, read):
    console, press, _ = keyboard
    press(sent)
    assert console.read_key() == read  # the terminal turns Enter's \r into \n; both are taken as Enter


def test_a_key_that_is_not_plain_text_is_not_taken_for_closed_input(keyboard):
    console, press, _ = keyboard
    press("é".encode())
    first, second = console.read_key(), console.read_key()
    assert first not in ("", "\x04", None) and second not in ("", "\x04", None)


def test_the_terminal_is_left_as_it_was_found(keyboard):
    console, press, settings = keyboard
    before = settings()
    press(b"q")
    console.read_key()
    assert settings() == before
    with console.key_mode():
        assert settings() != before  # single keys, no echo
        with console.line_mode():
            assert settings() == before  # back to typed lines for one question
        assert settings() != before
    assert settings() == before


def test_without_a_terminal_single_keys_are_not_read(monkeypatch):
    import io
    import sys

    import console

    monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))
    assert console.read_key() is None
    with console.key_mode(), console.line_mode():  # neither does anything, and neither fails
        pass
