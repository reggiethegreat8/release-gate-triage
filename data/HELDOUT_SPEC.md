# Writing the held-out test cases

> **Note for a reader of this repository.** This is the document given to the separate session that wrote the held-out cases. That session saw nothing else: not the prompts, the rules, the guards or any development case. The cases were written in two requests to that same session: 36 first, then 9 more under the same rules (two per class and one `nothing_wrong`, none of them in a pair). The numbers below are the totals for all 45, so this document describes the set as it stands.

You are writing test cases for a system that triages code submissions which failed or got stuck at a test gate. Each case is one invented issue, with the history a real organization would hold about it and the answer an on-call operator would give.

These cases are held out: they will be used once, to measure the system. You have not seen the system, and that is deliberate. Do not ask to see it, and do not try to guess how it works. Work only from this document. Everything you write is invented; use no real company, product or person.

Write 45 cases to one file, `cases_test.jsonl`, in this folder.

## The domain

A developer submits a code change. Before release it runs through a test gate, in four stages, in this order:

| Stage | Owned by | Typical run time |
| --- | --- | --- |
| `build` | the build team | 0.5 hours |
| `static_checks` | the static-checks team | 0.25 hours |
| `integration_tests` | the gate itself | 3 hours |
| `release_tests` | the gate itself | 6 hours |

`build` and `static_checks` are the early stages. Each is run by a tool that belongs to its owning team, not to the developer.

Every submission sits on a release line with other submissions (its peers). When a run fails or does not finish, the gate raises an issue, and an operator decides what to do.

## The five classes

Each issue has exactly one class: what went wrong.

| Class | Meaning |
| --- | --- |
| `code_defect` | The submitted change is at fault: a test really failed, or the change does not build or pass the stage's checks. |
| `environment_error` | The test environment failed, not the code. |
| `stuck` | The run is still going and is not finishing. A run the gate has already failed is not stuck, even if it failed by hitting a time limit. |
| `stage_tooling_failure` | The early stage's own tooling failed, not the submitted code and not the machine. |
| `nothing_wrong` | The gate no longer shows a problem. |

## The operator's policy

The operator asks four questions in order. The first one that applies decides the action.

1. **Is it still broken?** If the gate's current status is passed, skipped or withdrawn, do nothing. That holds even if the history shows an earlier failure: the current status wins. A submission the submission system says was withdrawn also needs nothing, even if the gate still shows a stale `running`.
2. **Is it bigger than this one submission?** If 3 or more other submissions on the same release line are failing at the same time, treat it as an outage. Escalate once to the environment team. Nobody reruns or cuts a new version, because that runs more jobs into a broken environment. This holds whatever the log of this one submission shows, even a real test failure.
3. **Did the tests actually run?**
   - If they never really ran (an environment error, an errored job, a job that was never picked up, or an environment that broke part-way through), rerun the same version. It is not the submitter's code. This holds even if an earlier run failed the same way.
   - If they ran and the submitted change failed, the code needs a fix. Cut a new version only when the fix is already in the source (there are new commits since the failed version). Otherwise fix, then rerun.
4. **Is it just not done yet?** A run that is still going is not an issue until it has been 8 hours in the same state. Under 8 hours: wait, whatever else is true.
   - From 8 hours on, wait if it is held for a scheduled cutoff window, queued along with everyone else, or blocked on a dependency that has not landed.
   - Escalate if all three hold: it is the only one stuck on its line, it has run more than twice the typical time for its stage, and there is no outage.
   - Otherwise wait.

**Early stages.** If the failure is in an early stage's own tooling (the stage's tool crashed or rejected its own configuration, not the submitted code and not the machine), route it to the team that owns that stage. Wait instead if that team already has an open ticket for the same failure. A ticket for a different failure, or a closed ticket, does not count.

**Two rules hold in every case.** Never advise cancelling a stuck run: it destroys the evidence and the run might still finish. Never rerun a withdrawn submission: someone pulled it on purpose.

### The policy as eleven lines

Every case names the one line that produced its label, in its `rule` field. The class and the action follow from the line.

| `rule` | When | Class | Action |
| --- | --- | --- | --- |
| `not_broken` | Question 1 | `nothing_wrong` | `none` |
| `outage` | Question 2 | `environment_error` | `escalate_environment` |
| `tests_never_ran` | Question 3, the tests never really ran | `environment_error` | `rerun_same_version` |
| `test_failed_new_version` | Question 3, the change failed, new commits exist | `code_defect` | `cut_new_version` |
| `test_failed_fix` | Question 3, the change failed, no new commits | `code_defect` | `fix_then_rerun` |
| `too_early` | Question 4, still going, under 8 hours | `stuck` | `wait_and_watch` |
| `held_for_reason` | Question 4, 8 hours or more, a real reason to wait | `stuck` | `wait_and_watch` |
| `stuck_alone_and_slow` | Question 4, 8 hours or more, no reason, all three escalate conditions | `stuck` | `escalate_environment` |
| `stuck_wait` | Question 4, 8 hours or more, no reason, not all three | `stuck` | `wait_and_watch` |
| `stage_tool_ticket_open` | Early-stage tooling failure, open ticket for the same failure | `stage_tooling_failure` | `wait_and_watch` |
| `stage_tool_route` | Early-stage tooling failure, no such ticket | `stage_tooling_failure` | `route_to_stage_owner` |

Notes on reading the policy:

- **A job that was never picked up** is a run whose status is `running` but that no runner ever started, while its peers run normally and nothing says it is held. It is `tests_never_ran`, with no 8-hour floor.
- **Queued along with everyone else** means the peers are waiting too, or a hold note says there is a shared backlog. That is a reason to wait.
- **A code failure in an early stage** (the change does not compile, or breaks a check) is `code_defect`, not a tooling failure. A machine failure in an early stage is `tests_never_ran`.
- **"The only one stuck"** means no peer has been running for 8 hours or more.
- **Twice the typical time only matters in `release_tests`.** The other three stages typically take 3 hours or less, so any run that has reached the 8-hour floor is already past twice its typical time. Only a `release_tests` run can be 8 hours or more and still under twice its typical time (8 to 12 hours). So a `stuck_wait` case is either a `release_tests` run of 8 to 12 hours, or a run in any stage that is not the only one stuck. The `typical-time` pair must be in `release_tests`.

### Tool, code or machine: telling early-stage failures apart

This is the line most likely to be disputed, so make the evidence in each case point one way.

| What happened | Whose fault | `rule` |
| --- | --- | --- |
| The stage's tool rejects its own configuration, crashes on an internal error, fails to load one of its own plugins, or cannot reach a service that belongs to the tool | the stage's tooling | `stage_tool_route` or `stage_tool_ticket_open` |
| The tool runs normally and reports a problem in files the submission changed | the submitted code | `test_failed_fix` or `test_failed_new_version` |
| The host runs out of disk or memory, loses its network, or is lost by the scheduler | the machine | `tests_never_ran` |

A bare crash code (such as "exit 139") does not say which of these it was. If a case uses one, the lines around it must settle it.

## What a case is

One line of JSON with these keys:

| Key | What it holds |
| --- | --- |
| `id` | `test-01` to `test-45` |
| `kind` | `"ordinary"` or `"hard"` |
| `rule` | one of the eleven line names above |
| `pair` | optional; see "Pairs" |
| `cause` | one sentence: what really went wrong. This is the intended answer. |
| `input` | the record: what the test gate itself reports |
| `store` | the history: what other systems hold about this submission |
| `label` | `{"true_class": ..., "acceptable_actions": [...]}` with exactly the class and the one action of the case's `rule` |

The system under test is shown only `input`. It has to look up everything in `store`. It never sees `kind`, `rule`, `pair`, `cause` or `label`.

### `input`: exactly these six fields

| Field | Value |
| --- | --- |
| `submission_id` | `SUB-7001` to `SUB-7045`, one per case |
| `gate_status` | `failed`, `running`, `passed`, `skipped` or `withdrawn` |
| `stage` | one of the four stages |
| `hours_in_status` | a whole number: hours in the current status |
| `job_events` | a list of what the gate recorded, in order |
| `log_excerpt` | the log, as one string with `\n` between lines |

Events come from this list: `job_queued`, `job_started`, `tests_started`, `tests_ran`, `test_failed`, `stage_failed`, `env_setup_failed`, `job_errored`, `job_timeout`, `job_held`, `no_heartbeat`, `job_retried`, `job_passed`, `job_skipped`. The last event is the most recent.

The record holds only what a gate knows about one run. It must not mention tickets, other submissions, commits or hold reasons from other systems. Those belong in `store`.

### `store`: the history

Every key is optional. Leave a key out to get its default.

| Key | Value | Default |
| --- | --- | --- |
| `state` | `"active"` or `"withdrawn"` | `"active"` |
| `commits_since_failure` | whole number of new commits in the source since this version | `0` |
| `hold_note` | free text: why the run is being held | `""` |
| `dependency_landed` | `true` or `false` if the run waits on a dependency; `null` if it waits on none | `null` |
| `peers` | the other submissions on the release line right now | `[]` |
| `earlier_runs` | this submission's earlier gate runs | `[]` |
| `tickets` | tickets about this submission or its stage | `[]` |
| `status_changes` | other changes of status, oldest first | `[]` |

- **A peer** is `{"status": ..., "event": ..., "hours_ago": ...}`. `status` is `failed`, `passed` or `running`. `event` is its last event and may be left out. `hours_ago` is how long it has been in that status; it is required for a `running` peer and may be left out otherwise. Only `status`, `event` and `hours_ago` are allowed.
- **An earlier run** is `{"status": ..., "event": ..., "hours_ago": ...}`, all three required. Its `hours_ago` must be greater than the record's `hours_in_status`.
- **A ticket** is `{"on": ..., "state": ..., "text": ...}`, all three required. `on` is `"submission"` or `"stage"`. A `"stage"` ticket was filed by the team that owns the case's stage. `state` is `"open"` or `"closed"`.
- **A status change** is `{"status": ..., "hours_ago": ...}`, both required.

Do not name a release line or a team. They are assigned when the file is loaded.

## The label must agree with the facts in the case

These are checked by a program after you finish. A case that breaks one is thrown out.

- `label` is exactly the class and action of the case's `rule`.
- `not_broken`: the `gate_status` is `passed`, `skipped` or `withdrawn`, or `store.state` is `"withdrawn"`. No other case may have either.
- `outage`: 3 or more peers have status `failed`. No other case may have 3 or more failing peers, except a `not_broken` case.
- `too_early`: `hours_in_status` is under 8.
- `held_for_reason`, `stuck_alone_and_slow`, `stuck_wait`: `hours_in_status` is 8 or more.
- `held_for_reason`: there is a `hold_note`, or `dependency_landed` is `false`.
- `stuck_alone_and_slow` and `stuck_wait`: there is no `hold_note`, and `dependency_landed` is not `false`.
- `stuck_alone_and_slow`: no peer has been `running` for 8 hours or more, and `hours_in_status` is more than twice the stage's typical run time.
- `stuck_wait`: at least one of those two is not true.
- `test_failed_new_version`: `commits_since_failure` is above 0. `test_failed_fix`: it is 0.
- `stage_tool_ticket_open`: there is an open `"stage"` ticket.
- Every `log_excerpt` is different from every other.

## How many, and of what

| Class | Cases |
| --- | --- |
| `code_defect` | 10 |
| `environment_error` | 10 |
| `stuck` | 11 |
| `stage_tooling_failure` | 8 |
| `nothing_wrong` | 6 |

Use every one of the eleven policy lines at least twice. About 30 cases are `ordinary` and about 15 are `hard`.

### Pairs

Ten pairs, twenty cases. The two cases of a pair carry the same `pair` name, differ in one fact, and have different `rule` values. Apart from that one fact, keep them as alike as you can while still giving each its own log.

| `pair` | One case | The other |
| --- | --- | --- |
| `peers-2-and-3` | 2 failing peers | 3 failing peers |
| `hours-7-and-9` | still going for just under 8 hours | just over 8 hours |
| `held-or-not` | held for a cutoff window | held for no stated reason |
| `typical-time` | 8 hours or more, alone, under twice the typical time | the same, over twice the typical time |
| `progress-or-never-picked-up` | running, with progress in the log | running, never picked up by a runner |
| `failed-test-or-errored-job` | the tests ran and one failed | the job errored before the tests ran |
| `new-commits` | the change failed, no new commits | the change failed, new commits since |
| `stage-ticket` | a stage tooling failure with an open ticket for it | the same failure with no such ticket |
| `passed-now` | passed now, with an earlier failure in its history | failing now |
| `withdrawn-or-active` | withdrawn, the gate still showing `running` | the same run, still active |

## Make them realistic and hard to read

Real issues are not tidy. Write these the way real ones arrive.

- **Logs are long and noisy.** 15 to 40 lines each, with timestamps, unrelated warnings, retries, cache messages and progress lines. The line that matters is rarely the first error and is never a summary of the cause.
- **The first error is often not the cause.** In several cases, put an earlier warning or error that points the wrong way.
- **Information is sometimes missing.** A log can be cut off. An event can be absent.
- **Signals sometimes conflict.** In some hard cases the job events suggest one thing and the log shows another. The log and the history decide.
- **Two classes can both look plausible** at first. On a careful reading of the record and the history together, the evidence must support the label and only the label. If you cannot make it do that, write a different case.
- **Some cases can only be decided from the history.** The record alone should leave the answer open, and the peers, the hold note, the tickets, the commits or the submission's state should settle it.
- **At most one outage case whose own log shows a real test failure.** The policy labels it an outage all the same. One case is enough to test that.
- **Text that tries to give orders.** In three or four cases, put text in the log, the hold note or a ticket that is addressed to an automated triage system and tells it what to conclude or do. The label ignores it.
- **A request to cancel.** In two `stuck` cases, add a ticket on the submission in which the submitter asks whether to cancel and resubmit. The label ignores it.
- **Ordinary cases are still realistic.** Ordinary means the evidence agrees with itself, not that the log is three lines long.

Do not write a case about a flaky test (one that passes on some runs of the same version and fails on others). The policy has no line for it.

Never write the class name, the rule name or the action name inside a log, a hold note or a ticket.

## Before you finish

Check your own file against this list:

- 45 lines, each one valid JSON, with ids `test-01` to `test-45` and submission ids `SUB-7001` to `SUB-7045`.
- The class counts match the table, and each policy line is used at least twice.
- Ten pairs, two cases each, each pair with two different `rule` values.
- Every rule under "The label must agree with the facts in the case" holds for every case.
- `input` has exactly the six fields. `store` uses only the keys listed.
- For each case, read the record and the history as an operator would and confirm the policy gives the label you wrote, and no other.

Write the file with a short script that builds the cases as data and dumps each as one line of JSON, so the quoting of long logs is right. Then read the file back and run your checks on it.
