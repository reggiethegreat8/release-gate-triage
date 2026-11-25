# Friction log

Rough edges met with LangGraph, LangChain and LangSmith while building this repo.

**Versions.** `langgraph==1.2.22`, `langchain==1.5.0`, `langsmith==0.14.17`, `langchain-anthropic==1.7.10`, `langgraph-checkpoint-sqlite==3.1.3`. Docs read at docs.langchain.com between November 17 and 24, 2026.

**How to read this.**

- Part 1 is what cost real time. Part 2 is smaller surprises and gaps in the docs. Part 3 is a table of places where older examples no longer match the current API. Part 4 is things that belong to other libraries.
- "Docs" means the pages that were read for the task. A statement that the docs do not cover something means those pages did not. It may be covered elsewhere.
- Where the cause was this repo's own code, the entry says so. Where a cause was not isolated, the entry says that too.

---

## Part 1: What cost real time

### 1. An error after `interrupt()` leaves the run stuck, and every retry is given the first answer again

- **Trying to do.** Refuse a reviewer's edit when the action is not allowed for the class, by raising inside the approval step after `interrupt()` returned the answer.
- **Expected.** The run stays paused, and the next `Command(resume=...)` with a valid answer is used.
- **What happened.** The next, valid answer was ignored and the same error came back every time. The run could never finish. The mechanism was checked against the installed source. The answer is saved with the step as soon as `interrupt()` returns it. If the step then raises, no new checkpoint is made, and every retry is handed the saved answer and not the new one. It happened a second time in review, when an answer of the wrong shape (the plain text `"approve"`) made the checking code raise, with the same permanent result.
- **Workaround.** The answer is checked before resuming (`answer_problem` in `agent/graph.py`, called by `review.py`). Inside the step nothing raises after `interrupt()`, and an unusable answer counts as a rejection.
- **Suggestion.** Say plainly on the human-in-the-loop page that a resume value must be validated before it is sent, because a step that raises after `interrupt()` cannot be given a different answer. A documented way to discard a saved resume value would also help.

### 2. A paused and resumed run is two traces

- **Trying to do.** Attach the reviewer's decision to "the run" as feedback, and score each live run once with an online evaluator.
- **Expected.** One run of the graph, one trace.
- **What happened.** The part up to the pause is one trace and the resume is another. They share a thread ID and have different trace IDs. There were three consequences.
  - The decision is made in a later process, which does not know the first trace's ID.
  - The online evaluator scored both traces, so reviewed runs counted twice.
  - A run that errored has no guard notes in its outputs. The first version of the evaluator scored it as clean.
- **Workaround.** The trace ID is chosen by the caller (`config["run_id"]`) and kept in the state, so the later process can find it. The online evaluator returns `{}` unless the trace's inputs hold an issue (the resume trace's do not) and its outputs hold guard notes. One gap is left. A run that dies and is carried on makes its proposal in a second trace, and the feedback lands on the first.
- **Suggestion.** A short section in the human-in-the-loop docs on what an interrupted run looks like in LangSmith, and the recommended way to attach feedback and online evaluators to the thread and not to one trace.

### 3. MCP tools are async-only, and the first bridge to sync code dropped connections

- **Trying to do.** Call a `create_agent` loop with MCP tools from an ordinary synchronous graph step.
- **Expected.** Tools usable from sync code, or a simple `asyncio.run()` around the agent call.
- **What happened.** The tools `MCPAdapter` makes must be awaited. With `asyncio.run()` per call, two separate experiments each had exactly one deep dive fail with `AnthropicConnectionError`, on different cases. It was reproduced outside the graph, with one failure in 40 small async model calls on five threads, each in its own event loop. The likely cause is one HTTP client shared across the process and used under more than one event loop. The cause was not isolated.
- **Workaround.** One event loop in one background thread for the life of the process. Every deep dive is handed to it with `asyncio.run_coroutine_threadsafe` (`agent/tool_agent.py`). No failures in 120 small calls and 16 full deep dives afterwards, and none in the experiments since. The cleaner design is an async graph throughout.
- **Suggestion.** A documented pattern for using MCP tools from a sync graph, or sync wrappers on the tools the adapter returns. A warning against `asyncio.run()` per call under concurrency would have saved the most time.

### 4. `interrupt()` with no checkpointer does not raise

- **Trying to do.** See what happens without a checkpointer, before adding one.
- **Expected.** An error saying a checkpointer is required.
- **What happened.** `invoke` returned the interrupt and stopped. Nothing was saved and there was no thread, so the run could never be resumed. No error and no warning.
- **Workaround.** `build_graph` adds the approval step only when a checkpointer is given, or when asked to for a server that supplies its own (entry 24).
- **Suggestion.** Raise, or at least warn, when `interrupt()` is reached with no checkpointer.

### 5. The default durability mode does not guarantee the last step is saved

- **Trying to do.** Show that a run killed part-way carries on from the last finished step.
- **Expected.** A checkpoint is on disk after every step, before the next starts.
- **What happened.** There are three modes, `exit`, `async` and `sync`, chosen per call. The default in the installed version is `async`, where the save runs in the background while the next step starts. So a step that just finished may not be saved yet, and a kill before the first save leaves no run at all.
- **Workaround.** The default was kept. What is and is not guaranteed is written down, and a test shows recovery without depending on timing. The step that records the approved action is safe to repeat for this reason (`agent/actions.py`).
- **Suggestion.** State the default and its guarantee next to the first checkpointer example. "Resumes from the last checkpoint" reads as "from the last finished step" unless told otherwise.

### 6. Online evaluator: no way to tell "not yet" from "broken"

- **Trying to do.** Confirm an online code evaluator scores live traces.
- **Expected.** A score soon after the trace arrives, and scores on that day's earlier traces, because "apply to past runs" was selected when the evaluator was created.
- **What happened.** The docs say each evaluator runs on its own polling schedule and give no time. A fresh trace got its score about 135 seconds after arriving. Until then the only signal was the evaluator's Logs tab. The three earlier traces never got a score.
- **Suggestion.** Show "last ran at" and "next run" on the evaluator, and the state of a backfill.

### 7. A store namespace part cannot contain a dot, and it fails late

- **Trying to do.** Use a user ID such as `alice.smith` as the first part of a store namespace.
- **Expected.** Any string is a valid namespace part.
- **What happened.** The store refuses a part that contains a dot. The refusal came at the approval step, after every model call had been made and paid for, and left the run stopped part-way. A code review of this repository found it.
- **Workaround.** `ReviewContext` checks the ID when the caller creates it, so a bad ID stops the run before any model call.
- **Suggestion.** Name the restriction on the memory page. Email addresses are the obvious first choice of user ID and most contain a dot.

### 8. Runtime context is not saved with a run

- **Trying to do.** Show a reviewer their preferred style on a run that paused and was resumed in a new process.
- **Expected.** The easy assumption is that a run remembers who started it.
- **What happened.** Context is passed per call and is not in the checkpoint. On resume with no `context=`, `runtime.context` is `None`, and the step runs again from the top without the reviewer.
- **Workaround.** `review.py resume THREAD [USER]` takes the reviewer again. This is also what keeps the user ID out of the saved run, which is wanted here, and a test checks it.
- **Suggestion.** One sentence in the runtime-context docs, saying that context must be passed again on every resume.

### 9. An agent run inside a graph step picks up the graph's checkpointer

- **Trying to do.** Run the `create_agent` tool loop inside the deep-dive step of a graph built with a SQLite checkpointer, which is how runs that pause for approval are built.
- **Expected.** The inner agent is a separate call with its own config, so it would not use the outer graph's checkpointer.
- **What happened.** Every deep dive in that graph failed with `NotImplementedError: The SqliteSaver does not support async methods`, and fell back as designed, so the run still finished and looked normal apart from its `degraded_reasons`. The inner agent took the outer graph's checkpointer from the running context (it is handed across even to the background event loop of entry 3), and the inner agent is async. The experiments build the graph without a checkpointer, so they never showed it. Found while rehearsing the demo.
- **Workaround.** `create_agent(..., checkpointer=False)`, so the inner loop never saves its steps. A test runs the real tool agent inside a SQLite-saved graph.
- **Suggestion.** Say on the subgraph page that an agent invoked inside a node inherits the parent's checkpointer, and how to opt out. An error that named the inheritance would have saved the search.

### 10. An experiment finished with every score, and LangSmith had stored almost none of it

- **Trying to do.** Run the held-out experiments with `client.evaluate`, then show them in LangSmith.
- **Expected.** If the experiment's runs cannot be stored, the experiment fails, or says so at the end.
- **What happened.** The account reached its monthly trace limit seven cases into the first held-out agent experiment. `client.evaluate` carried on, computed every score locally and returned full results for that experiment and five more. In LangSmith those experiments exist as names with 0 runs (7 for the first). The only sign at the time was `429` warnings from the background sender, mixed into the progress output of a long run, and they were missed. It was found two days later, when reading cost and latency back from the experiments.
- **Workaround.** The script already saved every row to a local file, so no result was lost, and the report is built from those files. The unchanged agent was run once more after the limit was raised, to have traces, cost and timing in LangSmith. Both runs are reported.
- **Suggestion.** `client.evaluate` should finish with a clear error, or at least a summary line, when runs or feedback could not be uploaded, such as "45 results computed, 38 not stored". An experiment that is empty for this reason should say so in the UI.

### 11. Over the trace limit, tracing fails quietly and the project is never created

- **Trying to do.** Run the demo and send the reviewer's decision to LangSmith as feedback.
- **What happened.** The same limit as entry 10. Each refused batch of traces printed a `429` warning to the terminal and the program carried on, which is right for tracing. But because no trace had ever arrived for the renamed tracing project, the project did not exist, and the feedback call failed with `Project release-gate-triage not found`, which reads like a configuration mistake rather than a limit.
- **Workaround.** An approval no longer depends on LangSmith. The decision is saved in the run first, and a failure to send it is printed with its reason.
- **Suggestion.** A clear "trace limit reached" state on the project and in the SDK, and a hint in the not-found error when the workspace is over its limit.

---

## Part 2: Smaller surprises and gaps in the docs

### 12. An SDK evaluator may not return an empty list, though an online one may return `{}`

- **Trying to do.** Have the judge give no score for a run whose conclusion was written by code, by returning `[]`.
- **What happened.** Every such run failed with `ValueError('Expected a non-empty dict, str, bool, int, float, list, EvaluationResult, or EvaluationResults. Got []')`. The online code evaluator in the LangSmith UI accepts `{}` as "no score" (entry 2), so the two kinds behave differently.
- **Workaround.** The judge always returns a `conclusion_judged` score, true or false. That turned out useful, because its average is the share of runs the judge covered.
- **Suggestion.** Let an evaluator decline to score a run, the same way in both places.

### 13. A failed run or evaluator shows up as a score of `None`

- **Trying to do.** Average each score over an experiment's runs.
- **What happened.** When the target or an evaluator raises, that score is `None` with the error in its comment. The averaging code crashed on it. Skipping `None` would have been worse, because a failure would hide inside an average over fewer runs.
- **Workaround.** `average_scores` in `evaluation/run_experiment.py` leaves `None` out of the average and returns each one as an error, and the evaluation gate fails if there are any.
- **Suggestion.** A count of errored runs and errored evaluators on the experiment's result object.

### 14. Two different things are called a "code evaluator"

- **Trying to do.** Find out what an online code evaluator must look like.
- **What happened.** The page named "code evaluator" describes the kind written in the LangSmith UI. It is one function, `perform_eval(run, example=None)`, which runs inside LangSmith, cannot import the repo and has no network. The SDK kind is on another page with a different signature.
- **Workaround.** A second, self-contained function for the UI (`evaluation/online_evaluator.py`), kept in the repo so it is versioned and tested.
- **Suggestion.** Different names for the two, and a link between the pages.

### 15. No SDK call was found to attach an online evaluator to a project

- **Trying to do.** Attach the evaluator to the tracing project from code, so the setup is repeatable.
- **What happened.** The installed client has `client.evaluators.create(...)`, but nothing was found that attaches an evaluator to a project.
- **Workaround.** Created by hand in the UI and pasted in. Moving to a new tracing project meant doing it by hand again.

### 16. `create_feedback` wants a `session_id`, and "session" means project

- **What happened.** `create_feedback(run_id, key, score)` worked and printed a deprecation warning that leaving out `session_id` will stop working. "Session" is the older word for a tracing project, so the value wanted is the project's ID.
- **Workaround.** Look the project up, then pass `session_id=project.id` and `trace_id`.
- **Suggestion.** Accept a project name, or say "project" in the warning.

### 17. The SQLite checkpointer and store needed settings the docs did not show

- **What happened.** The docs show `SqliteSaver(sqlite3.connect("checkpoint.db"))`. With Python's default (`check_same_thread=True`) the first `invoke` fails with `ProgrammingError`, because checkpoints are written from a background thread. `SqliteStore` ships in `langgraph-checkpoint-sqlite` but the pages read name only the in-memory, Postgres, MongoDB and Redis stores, so it was set up from the installed package, with `isolation_level=None` on the connection and then `store.setup()` once.
- **Suggestion.** One complete SQLite example for each. It is the natural choice for a local build.

### 18. `create_agent`: no full signature in the docs read

- **What happened.** The arguments used (`system_prompt=`, `response_format=`) and the default `recursion_limit` of 9,999 were confirmed from the installed package.
- **Workaround.** `recursion_limit` is set to 12 (`MAX_STEPS` in `agent/tool_agent.py`).
- **Suggestion.** The default step limit deserves a line in the quickstart. 9,999 model calls is a large default for a loop that spends money.

### 19. Testing `create_agent` with a fake model needed a subclass

- **Trying to do.** Run the real agent loop in a test with a scripted fake model and no network.
- **What happened.** `GenericFakeChatModel` needed two changes. `bind_tools` had to be overridden to return the model itself. And `create_agent` picks how to get the structured final answer from the model's `profile`, so the fake needed a profile to take the same route as a real Claude model.
- **Workaround.** A small `Scripted` subclass in `tests/test_tool_agent.py`.
- **Suggestion.** A fake chat model that supports tools and structured output as it comes.

### 20. `StateSnapshot.interrupts` is not on the docs page read

- **What happened.** The page lists interrupts under `snapshot.tasks[i].interrupts`. The installed `StateSnapshot` also has a top-level `interrupts` field, which is simpler. `review.py` uses it.

### 21. Tests that run a graph send traces

- **What happened.** With `LANGSMITH_TRACING=true` in `.env`, every test that ran the graph with fake models sent a trace to LangSmith. A code review of this repository found it. Part of the cause is in this repo. The model client is built when `agent/graph.py` is imported, which also needs a key to exist.
- **Workaround.** `tests/conftest.py` turns tracing off and sets a fake key before anything is imported.
- **Suggestion.** A note in the testing docs. It is easy to pollute a project without noticing.

### 22. A short script can exit before its trace is sent

- **What happened.** Traces are sent from a background thread, so a script that ends straight away can exit first.
- **Workaround.** `wait_for_all_tracers()` at the end of `review.py`.

### 23. Two ways to say where traces go

- **What happened.** The docs describe two workspace types. Newer agent-based ones use `LANGSMITH_AGENT_ID` and `LANGSMITH_AGENT_ENVIRONMENT` and not `LANGSMITH_PROJECT`. It was not clear from the docs which kind a new account gets. The project variable worked.

### 24. Running under `langgraph dev` worked, and three things were not obvious

- **Trying to do.** Run the same graph behind the local LangGraph server, with the approval pause, the reviewer's memory and the tool-calling deep dive, and drive it through the SDK.
- **What happened.** It worked after three things were found.
  - **The approval steps had to exist without a checkpointer.** `build_graph` added them only when given a checkpointer, and the server supplies its own. A graph exported for a server is built with neither a checkpointer nor a store (`agent/served.py`).
  - **Blocking calls.** The steps are synchronous, and the server refuses blocking calls unless started with `--allow-blocking`. The docs say so, but it is easy to miss.
  - **The reloader watches the virtual environment.** Started from the repo root, the server reloaded itself on files changing under `.venv`, right after start-up.
- **What the docs did not answer, now answered.** A run paused by `interrupt()` with a custom payload comes back in the result under `__interrupt__`, and is resumed with `command={"resume": ...}`. Runtime context (`context_schema`) is supplied per request with `context={...}`, on the first call and again on the resume. The server's store is written with `client.store.put_item`, and the graph reads it through `runtime.store` with no change.
- **Outside this build.** Answering the pause from the Studio page, and a hosted deployment.
- **Suggestion.** A human-in-the-loop example for the server that shows the interrupt payload, the resume command and runtime context together. A default reload filter that leaves out `.venv`.

---

## Part 3: Older examples that no longer match

None of these is a fault. The current docs were right each time. They are listed because examples written for earlier versions are still easy to find, and each one cost a look-up.

| Older form | Current form | Used here |
| --- | --- | --- |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY` | `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | `.env.example` |
| `client.list_runs(...)` (deprecation warning, removal after January 31, 2027) | `client.runs.query()` | an early script, since removed |
| Tool calling behind `with_structured_output` | The provider's native method: `method="json_schema"` for Anthropic | every model step |
| `interrupt_before=[...]` to pause for a person | `interrupt()` inside a step. The older form is described as a debugging tool | `agent/graph.py` |
| Only `retry_policy=` on `add_node` | Also `error_handler=` and `timeout=` | not used. Each model step has a plain `try`/`except` |
| `from langsmith.evaluation import evaluate` | `client.evaluate(...)` | `evaluation/run_experiment.py` |
| Evaluators taking `(run, example)` | Named arguments: `inputs`, `outputs`, `reference_outputs` | `evaluation/evaluators.py` |
| `create_examples(inputs=[...], outputs=[...])` | One list of dicts: `examples=[{"inputs": ..., "outputs": ...}]` | `evaluation/upload_dataset.py` |
| `create_example(outputs=...)` (deprecation warning) | `reference_outputs=` | `evaluation/add_case.py` |
| `langchain-mcp-adapters`, `MultiServerMCPClient`, `load_mcp_tools` | `langchain.mcp.MCPAdapter`, installed with `langchain[mcp]` and marked beta | `agent/tool_agent.py` |
| A `store` parameter on the step, and the user ID in `config["configurable"]` | `runtime.store` and `runtime.context` through a `Runtime` parameter, with `context_schema=` on the graph | the approval step |

---

## Part 4: Adjacent, not LangGraph, LangChain or LangSmith

Kept separate so they are not mistaken for faults in the three libraries.

- **Thinking tokens count against `max_tokens` (Anthropic API, this repo's setting).** The first version asked for JSON in the prompt and parsed the text, with `max_tokens=300`. On harder cases the model's thinking used the whole limit and the JSON was cut off in 4 of 30 runs. With the limit raised, 3 of 30 still failed because the reply was wrapped in a code fence. Fixed with `max_tokens=2000` and structured output.
- **The Anthropic client retries on its own.** Twice by default. That has to be known before saying what happens when a model call fails. It is now set on purpose to `max_retries=6`, and the fallback applies after that.
- **FastMCP turns every route into a tool by default (FastMCP 4.2.1).** Writes included. Two route rules fix it. A tagged GET becomes a tool and everything else is excluded (`store/mcp_server.py`). A test builds a server from an app with a POST and a DELETE and checks only the GET is offered.
- **An account out of credit looks like any other model failure.** Every model step failed with a 400 and each run fell back to the rules, as designed. The reason was readable only because the fallback keeps the error's message in `degraded_reasons`.
