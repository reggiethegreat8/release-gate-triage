# Demo

How to run the demo yourself.

One path of about twelve minutes covers the systems the agent reads from, a stuck submission that goes to a person, an outage that code decides, an edit the system refuses, and the results in LangSmith.

A recorded run of the terminal part is in [`demo/recorded_run.txt`](recorded_run.txt), for when a model call fails live.

## Before the session

1. `uv run python reset_demo.py` deletes saved runs, saved preferences and recorded actions, and rebuilds the store.
2. `uv run pytest -q` passes in full, with no network.
3. Port 8000 is free.
4. In LangSmith, confirm new traces are being accepted (the account's monthly trace limit), and that the online evaluator in `evaluation/online_evaluator.py` is attached to the `release-gate-triage` tracing project.
5. Open three pages in the browser. They are the comparison of `rules-only-test-49856071` against `rerun-agent-test-339bb803`, the trace of `test-41` in the second, and the dataset `release-gate-triage` filtered to the `test` split.
6. Have `demo/recorded_run.txt` open in a second window.

## The steps

**1. The systems the agent reads from (1 minute).** In a second terminal run `uv run uvicorn store.api:app`, then open `http://127.0.0.1:8000/docs` and call `GET /submissions/SUB-6104/gate-runs`.

This API stands in for the customer's gate, ticketing and source systems. The data behind it is synthetic. The three routes tagged "investigate" are the tools the model may call. The rest serve facts that code fetches for every run. In production the same routes would sit in front of the real systems. Stop the server. The agent calls the same routes inside its own process.

**2. The case (1 minute).** `uv run python show_case.py dev-04`

A run has been in integration tests for 15 hours with no heartbeat. Point out what the agent is handed (six fields from the gate) and what it has to look up. Stop before the answer at the bottom. This is a development case, one the system was built against. The one in step 5 is a held-out case.

**3. A reviewer preference, and the run (2 minutes).** `uv run python review.py prefer roger brief`, then `uv run python review.py start dev-04 roger`

While it runs (about ten seconds), say what is happening. Code fetches the facts and does the arithmetic, the rules make the first call, the model confirms or overrides it and takes a quick look. Only if the quick look is not sure does the deep dive run, and it does not decide the same way every time. The printout shows the rules' pick, the agent's, any lookups, the proposed action and the conclusion, cut to its first sentence because roger asked for brief.

Say what the evaluation found. Switching off the deep dive's tools did not change a single answer on the held-out cases. The tools are the first thing I would remove. The second look itself was never switched off, so that is still to test.

**4. Approve (30 seconds).** `uv run python review.py approve THREAD` (the printout gives the exact command)

The decision is saved in the run, the approved action is recorded once for this run, and the decision goes to LangSmith as feedback on the trace.

**5. An outage that code decides (2 minutes).** `uv run python review.py start test-25`

A held-out case, one the system was never tuned on. Its log reads like an ordinary test failure in a file the submission changed. But three other submissions on the line are failing, and that is an outage. Fixing or rerunning would send more jobs into a broken environment. The printout says the class was decided by code and the model was not asked for it. The model still writes the conclusion and proposes an action, and a guard in code forces the action to escalate if the model proposed anything else.

**6. An edit the system refuses, then a decision (1 minute).** `uv run python review.py edit THREAD fix_then_rerun`, then `uv run python review.py approve THREAD`

A person may overrule the agent, but only within the class's own list. Fixing the code is not an action for an environment error, so the edit is refused before anything reaches the run, and the run keeps waiting. Then approve.

**Optional, the same graph behind a server (1 minute).** In one terminal `uv run langgraph dev --allow-blocking --no-browser`, in another `uv run python demo/serve_check.py`. The server supplies the storage, the run is started and approved through the SDK, and the reviewer arrives as runtime context. A recorded output is in `demo/serve_check_output.txt`.

**7. LangSmith (4 minutes).**

- **The trace of step 3:** each step as a span, any tool calls with their arguments and results, the `human_review` feedback from step 4, and the two online scores, `guards_quiet` and `not_degraded`, which arrive about two minutes after the trace.
- **The experiment comparison** on the 45 held-out cases. The figures are 27 for the fallback rules, 36 for the policy applied in code, and 44 for the agent in the first runs and 43 in this one. The agent's margin over the best code-only baseline is 9 cases to 1, and it comes from reading the log to get the class.
- **The `test-41` trace**, the one consistent miss. The class was chosen without the policy. Once it was wrong, the last step saw only that class's lines. The facts that should have decided it (a hold note, three stuck peers) were on the screen and could not be used. This is a limit of the design. Moving the action into code, the first change I would make, would not fix it, because the class would still be wrong.

## The same demo through the console

Steps 2 to 6 can be driven through `console.py`. It shows the issue as the test gate reports it, waits for Enter, names each step and what it found as it finishes, shows the proposal, and asks for the decision at the keyboard.

```
uv run python console.py roger            # a list of cases, the demo's six first, with runs started for the reviewer roger
uv run python console.py dev-04 roger     # steps 2 to 4 with no list: the issue, Enter, the run, then "a" to approve
uv run python console.py test-25          # steps 5 and 6: "e" then fix_then_rerun (refused), then "a"
```

In the list, the arrow keys move the highlight, left and right change the page, and Enter runs the highlighted case. `r` names a reviewer and saves whether they are shown the brief or the full conclusion. When the reviewer was named on the command line, `r` asks only for that. A single word on the command line that is not a case is taken as a reviewer's name, so a mistyped case opens the list for a reviewer of that name. The list shows only what the test gate reports about each case. Picking a case replaces the list with the issue. Before the run starts, `b` replaces the issue with the list again. After a case is finished, Enter shows the list below its result, for the next one.

When a case is given on the command line there is no brief-or-full question, so run `uv run python review.py prefer roger brief` first, as in step 3.

The console is a display over `review.py`, with the same run, the same checks and the same words in the proposal. If a run fails part-way, it says so and prints the `review.py resume` command. The recorded run is of `review.py`.

## If something fails

| What fails | What to do |
| --- | --- |
| A model call, or it is slow | Switch to `demo/recorded_run.txt` and walk through the same steps |
| The API server will not start | Show `store/api.py` and move on |
| The deep dive does not run in step 3 | Say that this is the design. `demo/recorded_run.txt` shows one |
| LangSmith refuses traces | The run and the approval still work, and the printout says when the decision could not be sent. Show the comparison, which is already there |
| The online scores have not arrived | Show the trace from rehearsal, which has them |
| The store is out of date | The run stops and prints the command to fix it. Run `uv run python reset_demo.py` |
| A wrong thread ID | `uv run python review.py list` shows every saved run |
