# Evaluation

How this system is measured before it ships and watched after. The data is synthetic. The results say how well the agent follows the operator's policy on hard cases. They do not say how often it would be right on real traffic.

## What is built and what is planned

| Built and running in this repo | Planned for production, not built |
| --- | --- |
| A development set (31 cases written alongside the system, plus 1 from a reviewed run of a stand-in live issue) and a held-out set (45 cases), checked before any run | A test set grown from real, operator-labeled cases |
| One primary metric, `action_match`, and eight supporting scores | The evaluation gate run automatically on every change |
| Experiments for four variants, one of them a code-only baseline, and a second code-only baseline computed from saved runs. LangSmith holds the development experiments, the rules-only held-out run and the second agent run. The rest are in `data/experiments/` only (see "The second run") | A scheduled rerun on the held-out set |
| A results report with intervals and a case-by-case comparison (`results.py`) | Dashboards and alerts on live runs |
| An LLM judge for the written conclusion, checked against hand labels on 20 conclusions (`label.py`, `calibrate_judge.py`) | The judge sampling live conclusions |
| A gate script that passes or fails a change (`gate.py`) | A second labeler, to measure how often people agree |
| One online evaluator with two scores on live traces, and the reviewer's decision saved on each trace | |
| A script that turns a reviewed live run into a permanent test case (`add_case.py`) | |

## The result

On 45 held-out cases. Each agent variant was run three times per case, except where marked.

| Variant | Right action | 95% interval |
| --- | --- | --- |
| Rules alone, as the agent's fallback (no model) | 27 of 45 (60%) | 45% to 73% |
| Rules, with the policy applied in code (no model) | 36 of 45 (80%) | 66% to 89% |
| Full agent | 44 of 45 (98%) | 88% to 100% |
| Agent without lookup tools | 44 of 45 (98%) | 88% to 100% |
| Agent without the rules' hint (one run) | 44 of 45 (98%) | 88% to 100% |
| Full agent, a second run made later (one run) | 43 of 45 (96%) | 85% to 99% |

A case counts as right when most of its runs were right. An even split counts as wrong. The intervals are over the 45 cases. Every run, with its output and scores, is in `data/experiments/`, and `uv run python -m evaluation.results test` prints this table and the comparisons below from those files, with no model call. The models were `claude-haiku-5-5` for the agent and `claude-sonnet-5-5` for the judge. Those names are not pinned to a dated version.

**What is real and what is noise.**

- **The agent beats the best code-only baseline, 9 cases to 1.** A split that uneven has about a 2% chance of happening if the two were equally good. Against the second run alone the split is 9 to 2 (a 6.5% chance, which the report calls "could be chance"). Across all four runs of each case it is 9 to 1 again. Against the fallback as built, the split is 18 to 1, but that comparison overstates the agent's margin, because the fallback takes the class's default action and never applies the policy to the facts the system has already looked up. The second row does, in a short function (`evaluation/code_baseline.py`), written after the held-out run.
- **The model's value is reading the log to get the class.** Given the right class, that same function picks the right action on 44 of 45. The one it misses (`test-20`) has an open ticket about a different failure, which takes reading.
- **The lookup tools are not shown to help.** The score is the same without them, case for case. What was switched off is the deep dive's tools, not the deep dive. Without tools it still takes its second look, in one model call. So the tools are not shown to help, and whether the second look itself helps is untested. Code already fetches every fact the policy depends on. The deep dive ran in 26 of 135 runs, on 12 cases, and only 5 of those cases took it in all three runs. When it ran it called all three tools in 24 of the 26 runs and two in the others, so it was not choosing among them. 25 of the 26 were on `stuck` cases.
- **The rules' hint is not shown to help.** Telling the model what the rules picked made no difference. The rules still matter, because they decide ten cases in code and they are the fallback when the model fails.
- **Per-class results are not reported as findings.** With 6 to 11 cases per class, every interval is wide.
- **The action is mostly stable and the route is not.** Across the first three runs, one case changed its action, once. The second run changed one more (below). Whether a case goes to the deep dive varies from run to run.

**What the first change would do.** Let the model pick the class and let code pick the action. Computed from the saved runs, with the agent's own class, that scores 43 of 45. It keeps the `test-41` miss (the class is wrong) and loses `test-20` (the ticket about a different failure). No case changes its answer between runs. So the design I would take forward has code pick the action and the model keep one judgment, whether an open ticket is about the same failure. This has been computed from saved runs. It has not been run as a system. One limit matters here. The cases were built so that the presence of a hold note, or a dependency that has not landed, decides the "held for a reason" line, and the label checks require it. No case has a hold note that is not a reason to wait. Real hold notes need reading, so on real data this judgment may have to stay with the model too.

**The first miss, `test-41`.** A build job queued for nine hours behind a backlog that affects its whole release line, with a hold note saying so. The policy says wait. The agent called it a job no runner had picked up, and said rerun, in every run of every variant. There are two causes.

- **The class cannot be corrected later.** The class is chosen from the class definitions, without the policy. Once it is wrong, the last step is shown only that class's policy lines. The looked-up facts held the hold note and three other stuck runs, and the agent's conclusion mentions the hold, but no step could act on it.
- **One clarification was given to the case writer and not to the agent.** `data/HELDOUT_SPEC.md` says a job "never picked up" means its peers run normally and nothing says it is held. The agent's policy does not carry that qualifier. A sentence I added to one class definition after the development run, to fix a different case, made this more likely.

Nothing was changed, because nothing may be tuned after the held-out run. A rerun into a recovering runner pool is also the expensive kind of mistake, and every safety score reads 100% on it, because a rerun is allowed for the class the agent chose. The safety scores bound the action to the class's list. They do not catch a wrong class.

**The second run, and the second miss, `test-27`.** The first held-out experiments ran on November 22. LangSmith stopped storing their traces part-way through, because the account had reached its monthly trace limit (see `FRICTION_LOG.md`). The scores are computed locally, so the results above are complete, but only the rules-only experiment and seven runs of the first agent experiment are in LangSmith. On November 24 I ran the unchanged agent once more on the same 45 cases (`rerun-agent-test-339bb803`), so that its traces, cost and timing exist there. It was run with the same script, given a different experiment name (`run_experiment.py agent test 1 rerun-agent-test`). Nothing a model reads was changed in between. `tests/test_frozen.py` holds a fingerprint of every prompt, definition, policy line, tool description, rule and guard, taken before the first held-out run, and fails if any of them changes. The saved runs show that the looked-up facts and the rules' class are identical in both runs for all 45 cases, and that the agent's class is the same on all 45. The model name is not pinned to a dated version, so the model itself may have changed in two days. It scored 43 of 45, with the same `test-41` miss and a new one on `test-27`, which the agent had right in all three earlier runs. There the looked-up facts said one other run on the line was stuck, the policy says wait in that case, and the model wrote that this was the only stuck run and escalated. Code picking the action would have got it right. I report both runs. Across all four runs of each case, 44 of 45 are right by majority.

**One policy line is too simple.** The policy says to rerun a job that no runner ever picked up. Two cases, one in each set, show a job that can never be picked up, because it asks for a kind of machine no runner offers. A rerun cannot help there, and an operator would escalate. The labels follow the policy as written, so the agent is marked right for saying rerun. I would split that line with the customer into "rerun once" and "escalate when the cause is permanent".

**Other findings.**

- **Flaky tests are not recognized.** Four unscored cases describe a test that passes on some runs of the same version and fails on others. The agent called all four a code defect to fix. The policy has no line for this, and the classes have no place for it. A class for flaky tests is a proposed next step.
- **The guards stepped in once** in the 315 runs of the three agent variants, withholding a conclusion that mentioned cancelling (`test-04`), and once more in the second run (`test-24`).
- **The model's answer was replaced without a trace in any score.** On `test-36` the model answered "nothing wrong" in every run, and code used the rules' class, as designed, since only code may declare that nothing is wrong. No score counts this. What the model said is saved with every run (`model_class`), so a score for it is a small addition. The results report prints the count.
- **The written conclusions.** Of the 117 conclusions the judge scored (every run except those where nothing was wrong), it found 111 fully supported by the evidence and 116 in agreement with the chosen action. One of the 117 is the guard's withheld notice, which is text the code wrote and passes almost by construction. At least one of the six "unsupported" verdicts is the judge's own limit. On `test-28` it marked a mention of a policy line as unsupported, because the judge is not shown the policy.
- **The judge against a person.** On 20 conclusions (14 real, 6 written to be wrong in a known way), the judge agreed with the hand labels on 19 for grounded and on all 20 for matching the action. It caught all 6 planted errors. The one disagreement was the judge being stricter than the person. Twenty items is a small sample, and the only failures in it are the planted ones, so the judge has not been checked against an error the agent made by itself. In the second run it passed the `test-27` conclusion as supported, though that conclusion contradicts one of the looked-up facts.

## Cost and speed

These figures are from the second run, read from LangSmith. They cover the agent's own run for each of the 45 cases, without the evaluators or the judge.

| Runs | How many | Median time | 95th percentile | Median cost |
| --- | --- | --- | --- | --- |
| Nothing wrong (decided by code, no model call) | 6 | under 0.1 s | under 0.1 s | $0 |
| No deep dive (three model calls, or two for the four outages) | 30 | 5.9 s | 9.1 s | $0.0009 |
| With the deep dive | 9 | 12.6 s | 14.3 s | $0.0020 |
| All 45 | 45 | 6.0 s | 13.1 s | $0.0009 |

The 45 issues cost $0.046 in all, about a tenth of a cent each. The deep dive ran in 20% of runs. The step itself costs a median of $0.0008 and 3.4 seconds, and is 16% of the total cost. (Runs that include it are 41% of the cost, but they also make the three ordinary calls.) The same figures for the variant without tools are not available, because its traces were among those not stored.

## Why `action_match` is the primary metric

It asks one question, whether the agent recommended the action the operator would have taken. The action is what a person acts on, so it is the closest thing here to a business outcome. A right diagnosis with the wrong action still wastes the operator's time.

The supporting scores each answer a different question.

| Score | Question | Why it is not the primary metric |
| --- | --- | --- |
| `class_match` | Was the diagnosis right? | A means to the action, not the outcome |
| `action_allowed`, `no_forbidden_advice` | Did the safety rules hold? | Always 100% by construction, because they read the answer after the guards |
| `guards_quiet` | Did the model's answer get through the guards unchanged? | Says the model behaved, not that it was right |
| `not_degraded`, `tool_calls_valid` | Did every model step and lookup work? | Health, not correctness |
| `grounded`, `conclusion_matches_action` | Is the written conclusion true to the evidence, and does it match the action? | Scored by a model, checked against a person on 20 conclusions |

The metric was chosen before any held-out case existed.

## Dataset design

**How the cases are built.**

- **Every label traces to one line of the operator's policy** (`policy.py`). The policy is in one file, and the prompt, the labels and the checks all read it, so the agent is not graded on a rule it was not told. One exception was found afterward. A clarification given to the writer of the held-out cases is not in the agent's policy, and the first miss turns on it (see "The first miss").
- **Pairs sit on either side of each condition in the policy.** Examples are two failing peers and three, seven hours and nine, an open ticket and none. A miss on one of a pair shows which rule went wrong.
- **A program checks each label against the facts in its case** (`tests/test_cases.py`). An outage label needs three failing peers, and a "too early" label needs under eight hours.
- **The record holds only what the gate reports.** Everything else is in a store and has to be looked up, so the answer is never handed to the agent.

**Two sets, kept apart.**

| Set | Written by | Used for |
| --- | --- | --- |
| Development, 32 cases | 31 alongside the system, 1 added from a reviewed run of a stand-in live issue | Finding and fixing problems. Tuned against. |
| Held-out, 45 cases | A separate AI session that was shown only `data/HELDOUT_SPEC.md` in two requests, 36 cases and then nine more (noted at the top of that file). It was not shown the prompts, the rules, the guards or any development case | The result. Nothing was changed after the first run. Run a second time, unchanged, for the reason given under "The second run". |

The held-out cases were written, checked and committed before any run. LangSmith's record shows the order. The held-out examples were uploaded at 23:51:57 UTC on November 22, 2026, and the first experiment on them started at 23:52:20. A program checked every label, and the policy was applied by hand to all 45 with the label hidden. All agreed.

**Why these sizes.** The development set is sized by coverage, with every policy line and every pair. The held-out set is larger because it is the one reported, and 45 is what could be checked by hand before the run. It is enough to show the agent beats the rules on the same cases. It is not enough for accuracy to the percent.

**Limits.**

- The data is invented. It tests whether the agent follows the policy at its boundaries. It is not a sample of real traffic, which would be mostly ordinary code defects and environment errors.
- The held-out cases were written by a model from the same family as the agent, so their logs may be easier for it than real ones.
- The two cases of a pair share most of their log, so 45 cases carry less independent information than 45 unrelated ones.
- A score of 98% leaves almost no room to show that a part of the system helps. "Not shown to help" is not "shown not to help".

**In production.** The set grows from real use.

1. Every run a reviewer rejects or edits becomes a labeled case. The step exists today as a command a person runs for each run (`add_case.py`). One case has been added this way (`data/regression_cases.jsonl`). That case is a stand-in live issue, and it is an outage, which code decides. So the evaluation gate's rule that such a case may never get the wrong action has not yet been tested by a change to the model.
2. A random sample of approved runs is labeled too. Without it the set holds only the agent's mistakes and cannot estimate accuracy.
3. Before any accuracy figure is quoted to a customer, a second person labels a sample, to learn how often two people agree. That is the ceiling for any system.

About 200 real cases would narrow the uncertainty to roughly plus or minus five points.

## Offline validation

**Built.** A change to a prompt, the model or the policy is checked by running experiments on the same cases and comparing them. Four things support that.

- Four variants (rules alone, the agent, the agent without tools, the agent without the rules' hint), and the policy applied in code, computed from saved runs.
- Repeats, to see how much answers move from run to run.
- A report that gives each result with its interval and compares two variants case by case (`results.py`).
- A gate that passes or fails a change (`gate.py`). Safety must be 100%, the guards may not step in too often, accuracy may not fall by more than one case, and a case that came from production may never get the wrong action.

Before an experiment runs, the script checks that the cases in LangSmith match the local files and that the store was built from the current data. Either mismatch stops the run.

**In production.**

- **Run the gate automatically on every change,** so a change that fails cannot merge.
- **Gate on the primary metric, with repeats,** and block on a real drop compared case by case, not on one average.
- **Rerun the held-out set on a schedule,** changing nothing in response, to catch drift when the model provider changes a model. The held-out set stays a fair test only if nobody tunes to it.

## Online monitoring

After launch there is no label on a live issue. Two kinds of signal are available.

**Built.**

- **Two automatic checks on every live trace** (`online_evaluator.py`). One asks whether the answer got through the guards unchanged (`guards_quiet`), and the other whether every model step worked without a fallback (`not_degraded`). The first was false once in 315 offline runs, so on its own it is close to a constant. The second is the one that moves when the model provider has trouble.
- **The reviewer's decision saved on the trace,** as approved, edited or rejected (`review.py`).

**In production.**

- **The reviewer as the main signal.** Track approvals, edits and rejections by class and over time, and alert when edits or rejections rise. An edit also says what the right action was, so it becomes a test case.
- **Alerts on the health checks that need no label.** They cover how often a model step fell back and how often the guards stepped in (both recorded today), lookup errors, how often the deep dive ran, how often code replaced the model's class, and time and cost per issue. These say the system is healthy, not that it is right.
- **Later, the judge on a sample of live conclusions,** if operators report conclusions that do not match the evidence.

## What a stakeholder can take from this

- The agent follows the written policy on hard cases better than the best code-only alternative, 9 cases to 1, at about a tenth of a cent and six seconds an issue. What the model adds is reading the log to name the problem.
- Once the problem is named, code should choose the action. That is the first change. It is cheaper, and the case that changed its answer between runs stops changing.
- Two parts of the design, the lookup tools and the rules' hint, did not earn their place on this data. The deep-dive step is 16% of the cost. What was switched off is the deep dive's tools, not the deep dive. Without tools it still takes its second look, in one model call. So the tools are not shown to help, and whether the second look itself helps is untested. That is a saving to test, not yet one to take.
- What is not known is accuracy on real traffic. The first weeks in production are for measuring that, with operators' decisions as the labels.
