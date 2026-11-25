# Path to production

What it would take to run this system for real customers. Each section says what is built in this repo today, then what production needs and why I would choose it.

Nothing under "Production needs" is built. The repo is an MVP on synthetic data, on one machine.

One step toward deployment is taken. The same graph runs under the local LangGraph server (`uv run langgraph dev`, `langgraph.json`, `agent/served.py`). There the server supplies the storage for paused runs and for reviewers' memory, a run is started and resumed through the SDK, and the reviewer's ID arrives as runtime context on the request. `demo/serve_check.py` drives one issue through it. It showed two things still to do. The graph's steps make blocking calls, so the server needs `--allow-blocking`. A reviewer's answer is checked before resuming only in `review.py`, so a client of the server must make the same check. An unusable answer is still safe, because it counts as a rejection.

## Summary

| Topic | Built today | First step in production |
| --- | --- | --- |
| Memory and state | Three kinds of data, each tied to one run or one reviewer, in local files | A shared database, with every key tied to the customer |
| Auth and multi-tenancy (sign-in, and keeping customers apart) | The user ID comes from the calling program, never from a model | The customer's own sign-in, and separation enforced in the data layer |
| Failure recovery | Fallback to rules, saved progress, a store check that stops the run | Time limits, safer saving, and a capped retry for runs that fell back |
| Guardrails | Code decides two answers, three rules the model cannot override, a person approves | Alert when the guards step in more often than usual |
| Scaling | One process, one issue at a time | A queue, several workers, fewer model calls per issue |

## 1. Memory and state

**Built today.** The system keeps three kinds of data, and each is tied to exactly one thing.

| Data | Where | Tied to |
| --- | --- | --- |
| One run's progress, so it can pause for approval and resume | LangGraph checkpointer, `checkpoints.db` (`review.py`) | the run's thread ID |
| A reviewer's display preference | LangGraph store, `memory.db` (opened in `review.py`, read in `agent/graph.py`) | the reviewer's user ID, as the first part of the store's namespace |
| The approved action | `actions.db` (`actions.py`) | the run's thread ID |

The user ID is passed in by the calling program as runtime context. It is not part of the run's state, no model sees it, and the code that reads a preference cannot be asked for another reviewer's.

**Production needs.**

- **A shared database in place of local files.** Several copies of the agent must be able to reach the same saved runs, and a run paused for approval has to survive the machine it started on. Postgres is the usual choice, because LangGraph ships a checkpointer and a store for it.
- **The customer (tenant) in every key.** Thread IDs and store namespaces start with the customer, so one customer's runs and memory can never be read for another.
- **A retention period for run state.** A saved run holds the log it triaged. How long that is kept is a decision for each customer.

**Named next step, a memory of resolved cases.** When a new issue arrives, look up a few similar past issues and the outcome an operator confirmed, and show them to the model as examples. I would not build this first, because the agent's answers would then depend on stored data. It becomes safe under three conditions. It is written only from outcomes a person confirmed, it is kept per customer, and it is evaluated against a frozen copy so a test result still describes one fixed system.

## 2. Auth and multi-tenancy (sign-in, and keeping customers apart)

**Built today.** Nothing proves who a user is, because the reviewer's name is typed on the command line. There is one store for everyone. What is already right is where identity comes from, which is the calling program, never a model and never text the agent read.

**Production needs.**

- **The customer's own sign-in, checked before the agent runs.** The service in front of the graph verifies the user's token and passes the user and the customer into the graph as runtime context.
- **Separation enforced in the data layer.** The lookup tools take a submission ID that the model chose. If the store trusted the caller, a carefully worded log could lead the model to ask for another customer's submission. So every lookup carries the customer, and the API and the database refuse rows that belong to anyone else.
- **A dedicated deployment where a customer requires it.** A regulated customer may not be allowed to share infrastructure at all. That is their decision, and the design allows it, since each deployment has its own store and keys.

## 3. Failure recovery

**Built today.**

| What fails | What happens | Where |
| --- | --- | --- |
| A model call | The step falls back to a safe value and records the reason. If the classify step fails, the rules' class is used. | `graph.py` |
| The model service is busy | The client retries six times, waiting longer each time | `graph.py` |
| The store is missing or out of date | The run stops with a message that says how to fix it | `api.check_store` |
| The process dies | Progress is saved after each step, and the run resumes from the last saved step | `review.py` |
| A step runs twice after a crash | The approved action is still recorded once, because the record is keyed by the run | `actions.py` |

There are four known gaps. Progress is saved in the background by default, so a crash can repeat one finished step. No step has a time limit. A run that fell back to the rules is never tried again. The action is recorded once per run, not once per submission, so two runs on one submission can record two different actions. A real effect needs a key on the submission and its gate run, and a check for an open decision before a second run starts.

**Production needs.**

- **Save each step before the next one starts,** for any run that will have an effect.
- **A time limit on each model step,** so a slow call becomes a fallback and not a stuck run.
- **An alert when fallbacks become common.** One fallback is the design working. Many is an outage. The signal is recorded on every live trace today (`not_degraded`). The alert is still to build.
- **A capped retry for runs that fell back.** The operator gets the rules' answer now, and the run is queued for one more attempt later. The cap matters, because without it an outage turns into a flood of retries.

I would not add a second model provider yet. The rules already give a predictable answer when the model is unavailable, and a second model would need its own evaluation before it could be trusted as a fallback.

## 4. Guardrails

**Built today.** The model proposes, and code and a person decide. No safety rule lives only in a prompt.

- **Two answers are decided by code,** and the model is not asked. They are that nothing is wrong any more, and that there is an outage (`rules.py`).
- **The action must be one the class allows** (`guards.py`).
- **Three rules the model cannot override.** Nothing wrong means no action, a withdrawn submission gets no action, and an environment error during an outage is escalated (`guards.py`).
- **A conclusion that mentions cancelling or withdrawing is withheld.** This is a word scan. It cannot tell "cancel it" from "do not cancel it", so it errs on the safe side and sometimes withholds good advice.
- **Text from other systems is fenced** (logs, hold notes, tickets), so the model treats it as evidence and not as instructions (`prompts.py`).
- **The lookup tools are read-only** at three layers, the database connection, the API and the tool list (`api.py`, `mcp_server.py`).
- **A person approves** before anything is recorded (`graph.py`, `review.py`).

**Production needs.**

- **Watch how often the guards step in, and alert when it rises.** A guard that fires is a safety success and a quality warning at the same time, because the model has drifted and code is covering for it. The safety scores cannot show this, because they read the answer after the guards have corrected it. This is already measured as `guards_quiet`, offline and on live traces. It does not show code replacing the model's class, or a wrong action that the class allows. Those need scores of their own.
- **Later, replace the word scan with a small model that is checked against hand labels,** keeping the word scan behind it. I would do this when false alarms start costing operators time, not before.

## 5. Scaling

**Built today.** One process handles one issue at a time. An issue usually takes three model calls. It takes none when nothing is wrong, usually two during an outage, and up to nine when the deep dive runs. The median issue takes about six seconds and costs about a tenth of a cent (EVALUATION.md, "Cost and speed"). Storage is SQLite. Nothing has been tested under load.

**Production needs, in this order.**

1. **A queue in front and several workers behind.** Each worker takes one issue and triages it, so ten workers means ten issues in progress. Runs share nothing, and the shared database lets any worker resume any run. The queue absorbs a spike, and the real ceiling becomes the model provider's rate limit.
2. **Fewer model calls per issue.** Cost and speed both scale with model calls. The evaluation already points to one saving. Switching off the deep dive's lookup tools made no measurable difference on the held-out cases, and the deep-dive step is 16% of the cost. The tools are the first candidate to remove. Removing the second look itself needs its own experiment, because it was never switched off. Moving the choice of action into code would then leave one model call to name the problem and two to explain it.
3. **Asynchronous steps throughout,** so one worker can handle many runs at once. This touches every step and is only worth it at high volume.

## Other things a customer would ask

- **Real integrations.** The store here is a local stand-in built from synthetic data. In production the same read-only API sits in front of the customer's real gate, ticketing and source systems. The routes and the shape of their answers stay the same. The context step would call them over HTTP instead of as Python functions, and the store's freshness check would become a health check on the real systems. Two small things in the stand-in would be tightened then, not now, because the model reads their answers. Tickets come back in storage order, and the latest-run query assumes no two runs of one submission share an age.
- **Where approval happens.** Here it is a command line script. In production the request for approval goes to where operators already work, such as their ticketing or chat tool.
- **What is sent to tracing.** Logs and tickets can hold sensitive text, and every run is traced. Production decides what is masked before it is sent and how long traces are kept. A regulated customer may need tracing hosted inside their own network.
- **Cost and speed per issue.** LangSmith records both on every trace. Measured on 45 held-out issues, the median is 6.0 seconds and $0.0009 an issue, and 12.6 seconds and $0.0020 when the deep dive runs (EVALUATION.md, "Cost and speed").
- **A new model version.** A model change is treated like a prompt change. The model is named in one setting (`TRIAGE_MODEL`). The results here used `claude-haiku-5-5`, a name that is not pinned to a dated version. Production pins one where the provider offers it, the old and new models are run on the same held-out cases and compared case by case, and the switch happens only if the new one is at least as good.

## What I would do differently at scale or under other constraints

| If | Then |
| --- | --- |
| Volume grows tenfold | Queue and workers first, then cut model calls, then asynchronous steps |
| The customer is regulated | A dedicated deployment, tracing kept inside their network, a retention period agreed up front |
| The model provider has an outage | Rules-only answers continue, marked as such, with a capped retry later |
| Operators stop trusting the conclusions | Sample live conclusions with the checked judge, and feed every edit back as a test case |
| The policy changes | Edit `policy.py`, rerun the test sets, and let the evaluation gate decide. The policy is data, not prompt wording scattered through the code. |
