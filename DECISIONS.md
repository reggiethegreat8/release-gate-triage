# Decisions

What was chosen, what was considered instead, and what each choice costs. Grouped by topic. Where a decision came from something that failed first, the entry says so.

## The shape of the system

### 1. Rules first, model second, code last

**Chose.** Code makes the first call on every issue. A model confirms or overrides it by reading the log. Code then checks the model's answer, and a person approves.

**Instead of.** Handing the whole decision to a model with tools.

**Trade-off.** The model's part is narrow, so the system is less "agentic". In return it is predictable where it can be, and there is a safe answer when the model is unavailable. The evaluation supports the split in one respect and corrects it in another. The full system beats the best code-only baseline 9 held-out cases to 1, and that margin comes from the model reading the log to get the class. The fallback as built (the class's default action) understated what code can do. With the policy applied in code, the rules alone score 36 of 45, not 27.

### 2. Facts the policy always needs are fetched by code

**Chose.** A first step in code (`agent/context.py`) looks up the peers on the release line, the stage's typical run time, whether the submission was withdrawn, new commits, hold reasons and the stage owners' tickets. It also does the policy's three number comparisons and hands the model the answers.

**Instead of.** Giving the model tools for those facts, and leaving the arithmetic to it.

**Trade-off.** Every run pays for the lookups. If the store cannot answer, the run stops and is not marked "degraded", because the rules and the guards depend on those facts and a run without them would look healthy and be wrong. The rule of thumb is to fetch in code what is always needed, and to give the model a tool only where it has a real choice.

### 3. Two answers are decided by code, and the model is not asked

**Chose.** "Nothing is wrong any more" and "an outage" are decided from facts (`agent/rules.py`). During an outage the model still writes the conclusion, but does not pick the class.

**Instead of.** Asking the model and correcting it afterwards.

**Trade-off.** A real code defect that lands during an outage is classed as an environment error and escalated. The policy asks "is it bigger than this one submission?" first, because rerunning or fixing sends more jobs into a broken environment. One wrong escalation is the accepted cost.

### 4. The model chooses the action

**Chose.** The model picks the action from the policy lines for its class.

**Instead of.** Computing the action in code from the model's class and the looked-up facts.

**Trade-off.** Once the class and the facts are known, much of the action is a lookup, and code would be stricter. The reason for the choice was that the model has to judge free text, such as whether a hold note is a real reason to wait or whether a ticket is about the same failure. The held-out data does not support it. Given the right class, a short function picks the right action on 44 of 45. The free-text judgment mattered on one case (`test-20`). And in the second run the model chose an action that contradicted a looked-up fact (`test-27`). The next step is to move the action into code and keep the model for the one judgment about tickets (see EVALUATION.md, "What the first change would do").

### 5. The deep dive is a tool-calling agent, and it is not shown to help

**Chose.** When the first look is inconclusive, a `create_agent` loop may call three read-only tools before answering.

**Instead of.** One more plain model call. This was measured, with the same prompt and the tools switched off.

**Trade-off.** On the held-out cases the score is the same with and without tools. Code already fetches what the policy depends on. The deep dive ran in 26 of 135 runs, on 12 cases, and called all three tools nearly every time, so it was not choosing. The step is 16% of the cost. Only the tools were switched off. The second look itself was never removed, so whether it helps is untested. It stays in the build as the tested option. For production it is a candidate to remove.

### 6. The rules' pick is shown to the model, and that is not shown to help either

**Chose.** The classify prompt says what the rules picked.

**Instead of.** Keeping it from the model. Also measured.

**Trade-off.** No difference on the held-out cases. The rules still earn their place. They decide ten of the 45 cases in code, and they are the fallback.

## The model steps

### 7. Structured output enforced by the provider

**Chose.** Each model step uses `with_structured_output(Schema, method="json_schema")`, with the allowed values as a `Literal`.

**Instead of.** Asking for JSON in the prompt and parsing the text. That was tried first, on 30 runs, and 4 failed because the answer was cut off by a low token limit. With the limit raised, 3 of another 30 still failed because the answer came wrapped in a code fence.

**Trade-off.** Tied to providers with native structured output. Nothing to parse.

### 8. A failed model step falls back and does not stop the run

**Chose.** Each model call sits in a `try`/`except`. On failure the step returns a safe value and adds to `degraded_reasons`. If the classify step fails, the rules' class is used. If the quick look or the deep dive fails, the run carries on with the finding it has. If the last step fails, the class already chosen is kept and its default action is used.

**Instead of.** LangGraph's `retry_policy` and `error_handler` on the node.

**Trade-off.** Plain and visible in the step. Only the model call is inside the `try`, so a bug in our own code still stops the run. The model client retries six times by itself before the fallback applies.

### 9. Guards are code, after the model, on every run

**Chose.** An allowlist per class, three rules the model cannot override, and a scan that withholds any conclusion mentioning cancelling or withdrawing (`agent/guards.py`).

**Instead of.** Putting those rules in the prompt.

**Trade-off.** The scan cannot tell "cancel it" from "do not cancel it", so it sometimes withholds good advice. That is the safe direction to be wrong. The two rules with no exceptions, never advise cancelling and never act on a withdrawn submission, are deliberately not in the prompt. Telling a model "never advise cancelling" tends to make it write "do not cancel", which the scan then withholds.

### 10. Untrusted text is fenced and cannot close its own fence (prompt injection)

**Chose.** Logs, hold notes and tickets go inside tags, with every `<` replaced first. The model's own earlier finding is fenced too, because it was written from that text.

**Instead of.** Tags alone. A code review of this repository found that a log containing the closing tag ended the fence early.

## The policy and the data

### 11. The operator's policy lives in one file

**Chose.** `agent/policy.py` holds the three thresholds and eleven policy lines. The prompt, the case labels and the checks on the cases all read it.

**Instead of.** Policy wording spread across prompts and label files.

**Trade-off.** One more file to understand. In return the agent is not graded on a rule it was not told (with one exception found afterward, a clarification in the held-out spec that the first miss turns on), and a falling score cannot be caused by two copies of the policy drifting apart.

### 12. The record holds only what the gate reports

**Chose.** The record has six fields. Everything else is in a store and has to be looked up. Each test case carries its own history, and the store is built from that.

**Instead of.** A record that included the count of failing peers and the ticket text. That handed the agent the one number the outage rule turns on.

**Trade-off.** Every case needs a history written with it, and each case gets a release line of its own so cases cannot see each other's data.

### 13. The store is read-only at three layers, and only some routes are tools

**Chose.** The database connection is opened read-only, the API has only GET routes, and the MCP server is generated from the API and offers only routes tagged for investigation.

**Instead of.** Relying on any one layer, or writing the tools by hand.

**Trade-off.** Three places to keep in mind. A mistake in one does not reach the model, and the tools cannot drift from the API.

### 14. The rules were strengthened before measuring what the model adds

**Chose.** Before any held-out case existed, the rule chain was changed in three ways that follow from the class definitions. A running status means stuck. A failed setup or an errored job means an environment error. Both come before the early-stage rule.

**Instead of.** Leaving a rule that was known to be wrong, which would have made the model's gain look larger.

**Trade-off.** The measured gain is smaller. The early-stage rule still guesses a tooling failure, because telling tooling from code takes reading the log.

## Human approval and what follows it

### 15. Human-in-the-loop as an `interrupt()` inside a step

**Chose.** The approval step calls `interrupt()` with the proposal and resumes with the reviewer's answer.

**Instead of.** `interrupt_before` at compile time, which the docs describe as a debugging tool and which cannot hand the reviewer a tailored payload.

**Trade-off.** The step runs again from its first line on resume, so nothing with an effect may sit in it. A failing test found this. An answer is saved as soon as `interrupt()` returns it, so a step that then raised was handed the same answer on every retry. Answers are therefore checked before resuming, and an unusable one counts as a rejection.

### 16. The approved action is recorded in a step of its own, once (idempotent)

**Chose.** A step after approval records the action in its own table, keyed by the run's thread ID (`agent/actions.py`).

**Instead of.** Recording inside the approval step, or in the store the agent reads.

**Trade-off.** A saved run guarantees a step runs at least once, not exactly once. The key is what makes a repeat harmless. A real effect in another system would need that system to accept the same key.

### 17. Reviewer memory changes what is shown, never what is decided

**Chose.** A reviewer's saved preference (brief or full conclusions) lives in a store under a namespace that starts with their user ID. The user ID arrives as runtime context from the caller.

**Instead of.** A preference that changes the agent's prompt, or a user ID read from the state or from anything a model produced.

**Trade-off.** A small use of memory. In return every reviewer gets the same agent, so one evaluation describes all of them. There is no sign-in. The code cannot cross reviewers, but nothing proves who a person is.

## The evaluation

### 18. `action_match` is the primary metric

**Chose.** Did the agent recommend the action the operator would have taken? Chosen before any held-out case existed.

**Instead of.** The class (a means, not the outcome), the safety scores (always 100% by construction), or a judge's score (a model's opinion).

**Trade-off.** It depends on the class being right first, so the class is reported beside it as the diagnostic.

### 19. A held-out set, written blind, with nothing changed after its first run

**Chose.** 45 cases written by a separate AI session that was shown only `data/HELDOUT_SPEC.md`, checked before any run, committed, run, and nothing changed afterward. LangSmith's timestamps show the held-out examples were uploaded before any experiment touched them. The unchanged agent was run a second time later, because LangSmith had not stored the first runs' traces. Both results are reported.

**Instead of.** One set written alongside the system. The earlier result was 100% on 31 such cases, several sharing a log, with the agent tuned on them. That told a stakeholder nothing.

**Trade-off.** Wide intervals and no claim about real traffic. It caught something on its first run. A definition changed after the development run fixed one case there and most likely cost one on the held-out set.

### 20. Safety and accuracy are separate kinds of score

**Chose.** Safety scores must be 100%. The primary metric may not fall more than a set amount below the baseline. `guards_quiet`, the share of runs no guard had to correct, has a floor.

**Instead of.** One combined score.

**Trade-off.** More numbers to read, and a safety failure can never be averaged away. The safety scores read the answer after the guards, so they show the guards held, not how the model behaved. `guards_quiet` is the score that would collapse if the model started misbehaving in a way a guard catches. It does not see code replacing the model's "nothing wrong" with the rules' class, or a wrong action that the class allows. Its floor in the evaluation gate, 80%, is far below the observed rate (once in 315 runs), so the gate catches a collapse and not a drift.

### 21. The judge asks two yes-or-no questions and is checked against hand labels

**Chose.** A stronger, different model answers two questions. Is every statement supported by the evidence, and does the text match the chosen action? It is shown the same facts and lookups the agent was. It does not score the fixed sentence the code writes when nothing is wrong.

**Instead of.** A 1-to-5 quality score, which cannot be checked against a person, or an unchecked judge.

**Trade-off.** The check uses 20 items, 14 real conclusions and 6 written to be wrong in a known way. On the current cases the judge agreed with the hand labels on 19 of 20 for grounded and 20 of 20 for matching the action, and caught all 6 planted errors. The sample is small, and its only failures are the planted ones, so the judge has not been checked against an error the agent made by itself.

### 22. An experiment stops if its inputs disagree

**Chose.** Before running, the script compares the cases in LangSmith with the local files, and the store records a fingerprint of the files it was built from.

**Instead of.** Comparing row counts. A code review of this repository found the gap. A case could be edited without any count changing, and the run would carry on with wrong facts and a safety score of 100%.

## What the evaluation changed

- **One class definition was completed** after the development run, so that a job that errored or was never picked up is an environment error. The agent had been graded on something only the policy said.
- **The judge stopped scoring the fixed sentence** the code writes when nothing is wrong.
- **Two parts did not earn their place,** the lookup tools and the rules' hint.
- **One policy line turned out too simple.** "Rerun a job nobody picked up" is wrong when the job can never be picked up.
- **Flaky tests have no class.** The agent calls them a code defect.
- **The choice of action belongs in code.** A fairer code-only baseline, written after the held-out run, showed that the action is a lookup once the class is right. The model's measured value is the class.
- **The class is a hard constraint.** A wrong class cannot be corrected by a later step. That caused the one consistent miss.
