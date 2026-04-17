# Results: Co-Optimizing Coding and Review Agents

Part 3 writeup. Companion docs: [PROBLEM.md](PROBLEM.md),
[RESPONSE.md](RESPONSE.md) for direct answers,
[DESIGN.md](DESIGN.md) for the system design.

## TL;DR

- **Pipeline works end-to-end** at both Haiku (Phase A) and Sonnet (Phase B) scale, with 10 concurrent workers hitting the Anthropic API without rate-limit issues.
- **Headline metric** (`test_pass_rate`, oracle-grounded, non-empty-diff-only): **100% in both arms** on Phase B's 5 issues. Ceiling effect on this sample.
- **Small positive signal for history retrieval**: `avg_rounds_to_oracle_pass` was **1.2 for FULL vs 1.4 for ABLATE**. History helped the coder reach a passing fix slightly faster.
- **The real finding**: the reviewer chronically **over-asks**. Across both arms, 7 of 15 non-empty reviewer rounds **rejected diffs the oracle had already passed**. Precision 100%, recall only 50 to 57%.
- **Important caveat**: parallel mode meant each FULL worker ran with an empty memory. What we actually measured was "history retrieval vs nothing", not "full system vs nothing". Within-arm memory accumulation is the missing measurement.
- **What I'd do with 2 more weeks or months**: roadmap at the bottom. Also duplicated in [RESPONSE.md Part 3](RESPONSE.md).

---

## What we actually built

A per-issue loop with three independently controllable context layers feeding the coder:

```
coder system prompt
  + git history block   (harness/history.py, toggle: --no-history)
  + memory block        (harness/memory.py,  toggle: --ablate)
  + reviewer feedback   (in-round, always on)
```

Plus a test-based oracle (`harness/oracle.py`) that runs both a targeted slice and a broader regression check, hidden from the reviewer. The reviewer has its own memory of calibration cases (the win/loss table against the oracle). Stability guardrails are held-out split, alternating updates, and reviewer freeze on drift.

Architecture diagram is in [DESIGN.md section 7](DESIGN.md#architecture).

Implementation: about 20 commits on top of the starter harness. Key landmarks:

| Commit | What it added |
|---|---|
| `1736840` | `harness/oracle.py`. Pytest with addopts-override and fallback. |
| `4fe129c` | Loop integration. Oracle hidden from reviewer. |
| `dd6f5bc` | `harness/metrics.py`. Win/loss confusion, balance-gap alerts. |
| `4499d85` | `harness/memory.py`. Capped bullet stores with category tagging. |
| `4290cf6` | `harness/distill.py`. 2x2 calibration plus LLM lesson extraction. |
| `363687f` | `harness/scheduler.py`. Held-out split, alternating updates, audit and freeze. |
| `da14d9d` | Fix: defend against coder moving HEAD off baseline. |
| `3ed2af5` | Fix: exclude empty-diff rounds from metrics. |
| `3483a9c` | Coder turn budget and anti-flailing heuristics. |
| `cf2240e` | Oracle broader-check. Targeted AND broader must pass. |
| `e67e34a` | `harness/history.py`. Pre-baseline git-log retrieval. |
| `2fd0be4` | Fix: relax anti-flailing heuristic for Sonnet's exploration style. |

---

## Phase A (pilot, Haiku, 3 issues)

First real run after the pipeline came together. 3 issues: 1240, 1056, 815. The goal was to verify end-to-end integration, not to show improvement.

### Raw numbers

| Metric | Value |
|---|---|
| test_pass_rate | 66.7% (2 of 3) |
| first_pass_test_pass_rate | 66.7% (2 of 3) |
| reviewer precision | 100% |
| reviewer recall | 66.7% |
| balance_gap | 33.3% |
| memory state after run | 1 coder lesson plus 1 reviewer calibration case |

### What the run revealed (and why I kept iterating)

Two concrete bugs in the system that Phase A exposed and later commits fixed:

1. **Coder HEAD-drift.** Issue 1240's coder ran `git checkout master` during a Bash call, moving HEAD off the pinned baseline. The oracle then ran against the wrong source tree (and hit pytest-cov flag errors from master's `tox.ini`). Fixed in `da14d9d`. The loop now verifies HEAD after each coder round and force-resets on drift.

2. **Empty-diff rounds spoofing the reviewer audit.** Haiku on issue 1240 spent 60+ Bash turns across 3 rounds without producing any diff. The empty-diff rounds counted as `false_rejection` (reviewer rejected, oracle "passed" on clean tree), which falsely tripped the reviewer freeze. Fixed in `3ed2af5`. Empty-diff rounds are now excluded from all reviewer-vs-oracle accounting.

Also exposed that Haiku's Bash exploration was unbounded. 60+ calls per round. Fixed in `3483a9c` with an explicit turn budget (25) and anti-flailing heuristics (halt at half-budget with no edits; halt after 8 consecutive Bash calls without an Edit).

---

## Phase B (ablation, Sonnet, 5 issues)

The real measurement. Same 5 issues processed by two arms:

| Arm | Flags | Git history | Memory | Distillation |
|---|---|---|---|---|
| **FULL** | (default) | on | on | on |
| **ABLATE** | `--ablate --no-history` | off | off | off |

5 issues chosen to span all bug categories in `data/issues.json` and include one known-hard case (1240):

| Issue | Category | Phase A behavior (Haiku) |
|---|---|---|
| 1056 | locale-pluralization | passed round-1 |
| 815  | missing-locale-timeframe | passed round-2 (asked for tests) |
| 1224 | humanize-boundary | *(not in Phase A)* |
| 1240 | humanize-boundary | FAILED. Coder produced no diff. |
| 607  | parsing-edge-case | *(not in Phase A)* |

Ran in parallel via 10 git worktrees (one FULL plus one ABLATE worker per issue), separate `TRACES_DIR` and `MEMORY_DIR` per worker, same model (Sonnet), same `max-rounds=3`, about 20 minutes wall time.

### Headline

| Metric | FULL | ABLATE | Delta |
|---|---|---|---|
| `test_pass_rate` | **100%** (5/5) | **100%** (5/5) | tied |
| `first_pass_test_pass_rate` | 80% (4/5) | 80% (4/5) | tied |
| `approval_rate` (issue-level) | 80% (4/5) | 80% (4/5) | tied |
| `avg_rounds` | 1.8 | 1.8 | tied |
| **`avg_rounds_to_oracle_pass`** | **1.2** | 1.4 | **FULL faster by 0.2** |
| `reviewer_precision` | 100% | 100% | tied |
| `reviewer_recall` | 50% | 57% | FULL lower |
| `reviewer_fpr` | 0% | 0% | tied |
| `balance_gap` | 50% | 43% | both trip the alert |

Both arms trip `reward_hacking_warning` on `balance_gap > 0.30`, but the direction is "over-asking reviewer" (tests pass more often than reviewer approves), not "reward hacking" (approval more often than tests pass). This is a known limitation of the current alert. See the fixes section below.

### Per-issue breakdown

| Issue | Category | FULL (rounds, result) | ABLATE (rounds, result) | Notes |
|---|---|---|---|---|
| 1056 | locale-pluralization | 1, approved (857 chars) | 1, approved (1761 chars) | FULL's diff was tighter (half the size). History pointed it at the right pattern. Same outcome. |
| 1224 | humanize-boundary | 2, approved | 2, approved | R1 diff was **identical** (486 chars) in both arms. R2 edits differed but both reached approved. |
| 1240 | humanize-boundary | 3, **reviewer rejected** (oracle passed R2 and R3) | 3, **reviewer rejected** (oracle passed R3) | Coder produced a working fix in both arms. Reviewer refused to approve despite 29 to 44 comments per round. |
| 607 | parsing-edge-case | **1**, approved | 2, approved | FULL's history block flagged prior parser fixes. Coder jumped straight to the right spot. |
| 815 | missing-locale-timeframe | 2, approved | **1**, approved | R1 diffs were identical (1144 chars). FULL reviewer rejected, ABLATE reviewer approved. Same diff, different verdict. Reviewer non-determinism. |

Score: FULL wins one (607), ABLATE wins one (815), three tied. Net zero at N=5.

### What the comparison tells us

**1. The coder is not the bottleneck. The reviewer is.**
Every one of the 5 issues produced an oracle-passing diff in both arms. The problem was the reviewer's verdict, not the coder's fix. Specifically: all 4 FULL-arm rejections and all 3 ABLATE-arm rejections were of oracle-passing diffs. The reviewer is chronically demanding more than the tests require.

**2. History retrieval gave a small but real efficiency win.**
`avg_rounds_to_oracle_pass` dropped from 1.4 to 1.2. The 607 case is the clearest. History surfaced prior parser commits and the coder fixed in one round instead of two. But the 815 reversal (and identical R1 diffs for 1224) says the effect is real but small and within LLM noise at this N.

**3. Issue 1240 is a reviewer problem.**
FULL/1240 round 2 produced a 1556-char fix that passed oracle. Round 3 produced the *same* diff. The coder couldn't figure out what else to change. Reviewer rejected both. A reviewer trained on its own `false_rejection` memory would eventually relax, but the current reviewer has no such state (parallel mode, cold-start memory).

**4. Parallel mode dilutes what we were trying to measure.**
Each FULL worker started with an empty memory dir, so we never exercised cross-issue memory accumulation. What we actually measured was "history plus fresh-per-issue distillation" vs "nothing". A proper memory test requires a sequential within-arm run.

---

## Phase C (post-fix verification, Sonnet, 2 issues)

After Phase B exposed the reviewer-over-asking defect, we shipped four fixes:

- Reviewer gets the git-history block too (commit `0d1918c`).
- Coder prompt tells it to write a regression test alongside the source fix (commit `5b3374e`).
- Balance-gap alert split into directional `reward_hacking_warning` vs `reviewer_over_asking_warning` (commit `35d228a`).
- Oracle caches results when a round's diff is byte-identical to the previous round (commit `34aa5f2`).

Phase C runs the same ablation on the two issues from Phase B that most visibly showed the over-asking problem: **1240** (humanize-boundary, Phase B rejected all 3 rounds in both arms) and **815** (missing-locale-timeframe, Phase B reviewer on FULL rejected an identical R1 diff that ABLATE approved). 2 issues across 2 arms, 4 parallel workers, about 15 minutes wall time.

### Headline

| Metric | FULL | ABLATE |
|---|---|---|
| `test_pass_rate` | **100%** (2/2) | 50% (1/2) |
| `first_pass_test_pass_rate` | **100%** (2/2) | 50% (1/2) |
| `approval_rate` (issue-level) | 100% (2/2) | 50% (1/2) |
| `avg_rounds` | 1.5 | 2.0 |
| `avg_rounds_to_oracle_pass` | 1.0 | 1.0 |
| `reviewer_precision` | 100% | 100% |
| `reviewer_recall` | **66.7%** (up from 50% in Phase B) | 100% (single sample) |
| `reviewer_fpr` | 0% | 0% |
| `balance_gap` | 33.3% (down from 50%) | 0% |
| alerts | `reviewer_over_asking_warning` | `approval_saturation_high` (small-N noise) |

The split alert now names the pathology correctly: Phase B mislabeled its over-asking as `reward_hacking_warning`; Phase C's FULL arm fires the correctly-named `reviewer_over_asking_warning`.

### Per-issue breakdown

| Issue | Phase B FULL | Phase C FULL | Phase B ABLATE | Phase C ABLATE |
|---|---|---|---|---|
| 1240 | 3 rounds, **rejected** (oracle passed R2+R3) | **2 rounds, approved** (R1 rejected, R2 identical cached diff approved) | 3 rounds, **rejected** | 3 rounds, **rejected** (zero diff all 3 rounds) |
| 815 | 2 rounds, approved (R2 added tests) | **1 round, approved** (R1 diff included tests, 1762 chars) | 1 round, approved | 1 round, approved (same) |

### What changed and why

- **FULL/815 went 2 rounds -> 1 round** because the coder wrote the test in round 1. We can see it directly in the trace: the diff touches `tests/locales_tests.py` and the reviewer had no grounds to object. The test-writing prompt change worked exactly as intended.
- **FULL/1240 went from rejected -> approved**, but for a different reason than we expected. The coder did NOT write a test on 1240 (the diff doesn't touch `tests/`). What happened: R1 produced an 821-char fix, reviewer rejected with 28 comments. R2 produced the same 821-char diff (the coder couldn't figure out what else to change). The oracle cache fired (cached=True, saved the pytest run). But this time the reviewer approved with only 9 comments. Same input, different verdict. Reviewer non-determinism helping us, possibly boosted by the feedback loop: R2's prompt includes the R1 feedback as context, which may let the model reflect that its previous concerns were nice-to-haves rather than blockers.
- **ABLATE/1240 got worse, not better.** Phase B had the coder produce a 1168-char diff on R3. Phase C had zero diff all 3 rounds. Without the history block (ablated) and without accumulated memory (no prior issues), Sonnet just cannot locate this bug. The test-writing prompt change didn't help here because there was nothing to test.
- **ABLATE/815 stayed at 1 round approved.** As in Phase B, the coder produced a clean 1144-char fix that did NOT include tests, and the reviewer approved. Confirms the reviewer is not blindly demanding tests.

### What we actually fixed (and what remains)

Fixed:

- Reviewer's over-asking on tests is noticeably better. Phase B recall 50% -> Phase C recall 66.7%. On the specific issue that was the sharpest example (815 round 1, same 1144-char diff), the coder now prefers to include tests up front, so the reviewer never has to choose.
- Alert labeling is now directional. In Phase B we had to explain in prose that the `reward_hacking_warning` was actually measuring over-asking. In Phase C it just says `reviewer_over_asking_warning`.
- Oracle cache fires correctly on duplicate diffs. Modest wall-time saving on runs where the coder plateaus (saw this fire on FULL/1240 R2 and ABLATE/1240 R2-R3).

Still open:

- `reviewer_over_asking_warning` still fires on FULL. Recall is 66.7%, not 100%. We have N=1 false-rejection in FULL (the R1 of 1240). One more fix round would probably solve it, but this is the size of the remaining defect.
- Coder still skips tests on hard cases. 1240 did not get a test added despite the prompt. If the bug reasoning is complex, the model focuses on the fix and forgets the test step. Could tighten with "the test MUST fail on the old code" or force an Edit to tests/ before submit_fix.
- ABLATE on 1240 regressed. Sonnet with no context at all can't handle 1240. This is not a defect of our system per se (we don't run ablate in production); it just underlines how much the history block is doing for hard bugs.

---

## Metrics I chose and why

Metric choice follows DESIGN.md section 8 and the empty-diff-exclusion fix from Phase A.

**Primary (oracle-grounded, cannot be gamed by the agents):**

- `test_pass_rate`: final-round diff must be non-empty AND pass oracle (targeted plus broader slice).

**Coder-specific:**

- `first_pass_test_pass_rate`: did the coder fix it without any reviewer help?
- `rounds_to_oracle_pass`: loop efficiency. Only counts rounds where a non-empty diff passed.

**Reviewer-specific:**

- `reviewer_precision` = P(tests pass | approved). The reviewer's approvals must be meaningful.
- `reviewer_recall` = P(approved | tests pass). The reviewer shouldn't block good fixes.
- `reviewer_fpr` = P(approved | tests fail). Reward-hacking or reviewer-collapse signal.

**Balance / pathology detectors:**

- `balance_gap = |approval_rate - test_pass_rate|`. Gap > 0.30 trips `reward_hacking_warning`.
- Approval saturation (above 0.95 or below 0.05) trips collapse alerts.

Metrics deliberately **not** chosen as primary:

- Raw approval rate. Gameable by the reviewer. Moved to balance-monitor role only.
- Diff size. Measures volume, not correctness.
- Reviewer comment addressal rate (round-over-round region overlap). Implemented but heuristic, secondary signal only.

---

## Failure modes observed (and what the guardrails caught)

### Caught and fixed during development

1. **Coder moving HEAD off baseline** (Phase A). The coder's Bash tool ran `git checkout master` during exploration. Oracle then ran against the wrong source tree and hit master's `tox.ini` pytest-cov flags. Fix: post-coder HEAD audit (`harness/loop.py::assert_at_baseline`) force-resets on drift. Commit `da14d9d`.

2. **Empty-diff rounds spoofing the reviewer audit** (Phase A). Haiku spent 60+ Bash turns without producing a diff. Empty diffs made the oracle trivially pass and the reviewer trivially reject, which then counted as `false_rejection` and tripped the reviewer freeze. Fix: empty-diff rounds are excluded from all reviewer-vs-oracle metrics and distillation. Commit `3ed2af5`.

3. **Coder flailing** (Phase A). Haiku exploration loops of 60+ Bash calls without editing. Fix: turn-budget enforcement plus bash-streak heuristic. Commit `3483a9c`.

4. **Over-aggressive anti-flailing** (Phase B). Sonnet's thorough Grep-heavy exploration tripped the "half-budget-no-edit" heuristic at turn 12, right before the edit. Fix: removed the half-budget heuristic, raised turn budget to 40, kept the bash-streak at 12. Commit `2fd0be4`.

### Observed in Phase B results (not yet fixed)

5. **Reviewer chronically over-asks.** 7 of 15 non-empty rounds were `false_rejection`. Reviewer precision 100%, recall 50 to 57%. Fix direction: reviewer prompt tuning (downweight "missing test coverage" when the fix itself is correct), plus calibration from accumulated `false_rejection` cases. We didn't accumulate those cases in parallel mode.

6. **`reward_hacking_warning` alert is one-sided.** The current `balance_gap > 0.30` alert fires whether `approval_rate > test_pass_rate` (real reward hacking) or `test_pass_rate > approval_rate` (over-asking reviewer). Phase B is squarely the second case, so the alert is technically correct but semantically misleading. Fix direction: split into `reward_hacking_warning` and `reviewer_over_asking_warning`.

### Prevented-by-design (observed firing correctly)

7. **Git-history retrieval leakage.** Verified 0 of 25 eval fix SHAs appear in pre-baseline retrieval. `git log c9cecaf --grep=...` scope makes the 25 fix commits unreachable, and the `forbidden_shas` blacklist is a second line of defense.
8. **Oracle hidden from reviewer.** Verified by reading `harness/loop.py::run_issue`. The `oracle_result` is appended to the trace but never passed into the reviewer's prompt. The one place this would leak is `extra_context` for the next round, which only contains reviewer comments and the previous diff.

---

## What I'd do differently

### With 2 more weeks

1. **Sequential within-arm runs.** The biggest missing measurement. Parallel mode gave us speed but not memory accumulation. Running the 5-issue FULL arm sequentially would produce 5 traces' worth of distilled lessons and test whether the coder's memory actually helps on issue N having seen N-1.
2. **Reviewer prompt tuning plus alert split.** The two most-defensible direct fixes to Phase B defects. Downweight "missing tests" in the reviewer prompt when the fix is correct. Split the `balance_gap` alert into reward-hacking-direction vs over-asking-direction.
3. **Value-function head** (AlphaGo-style). A second prompt that estimates `P(tests pass | diff)` without voting. Trained or prompted from oracle labels. Tie-breaker when the primary reviewer is frozen or uncertain.
4. **Best-of-N at inference.** Coder produces K=3 candidate diffs per round. Rank by value function or broader-slice pass. Submit top-1.
5. **Curriculum ordering.** Sort training-stream issues by a difficulty heuristic (single-file to multi-file; humanize to DST).
6. **Extend git-history access to the reviewer.** `git blame` on the lines the coder modified. Low effort, orthogonal to the coder's history block, and directly addresses Phase B's "reviewer over-asks because it doesn't know the repo's patterns" finding.
7. **Learn the categorizer.** `harness/memory.categorize` is keyword-based. Could be an LLM call or a small classifier trained on our labeled 25 issues.
8. **Per-category metrics.** Report `test_pass_rate` broken out by bug category. Tells us whether certain categories benefit more from distillation.
9. **More seeds.** N=5 is statistically weak. 2 or 3 seeds across 5 issues across 2 arms is about 6 hours and would give error bars.

### With 2 more months

1. **Cross-repo generalization.** Swap the target repo. Measure how much of the memory transfers. Likely answer: very little of the content (lessons are arrow-specific), but the *structure* of what gets learned and the guardrails generalize. Would tell us which parts of our architecture are repo-generic.
2. **Preference learning from (accepted, rejected) diff pairs.** Every round gives us such a pair. Fit a lightweight preference model and use as a soft signal alongside (or eventually instead of) the prompt-level reviewer.
3. **Mutation testing as a second oracle.** Targeted tests passing isn't proof the fix is correct. Mutants surviving the tests tells us coverage is weak. Orthogonal signal.
4. **Human-in-the-loop slice.** Every Nth approved diff gets a 30-second human read. Catches subtle correctness issues the oracle misses. Adds a small-but-real ground-truth stream alongside synthetic signals.
5. **Base-prompt optimization** (OPRO, APE, DSPy style). The current "memory" is a bullet list. A better slice is a periodic LLM-driven rewrite of the base system prompt itself, informed by aggregate trace patterns. The Phase A Haiku flailing on 1240 is exactly the kind of failure base-prompt optimization should fix, complementary to per-issue memory.
6. **Real ablation matrix.** Currently we measure full vs ablate. A proper 2x2 (memory × history) would isolate which layer pulls weight.

---

## Limitations and honest caveats

- **N is small.** 5 issues per arm is enough to see qualitative differences but not statistical significance. Larger N is an API-cost question, not a design question.
- **Parallel mode dropped memory accumulation.** Each FULL worker ran with an empty memory dir. We measured "history retrieval plus single-issue distillation" vs nothing, not the full system. Sequential within-arm run is the top priority in the 2-week roadmap.
- **Tests do not equal correctness.** Even with the broader-slice check, "passes arrow's own tests" is not the same as "the bug is really fixed". Mutation testing (2-month roadmap) would harden this.
- **`reviewer_recall` conflates over-asking with useful-asking.** When the reviewer rejects a fix that passed tests but lacked test coverage, our metrics call that a `false_rejection`. In practice it may be the reviewer adding value the oracle can't see. Phase B issue 815 round 1 is exactly this case.
- **One repo.** All learning is arrow-specific. Cross-repo generalization is the 2-month direction.
- **Prompt-level, not weight-level.** Distillation writes to JSON files the agent reads via its prompt. True RLHF would require training infrastructure we don't have.
- **LLM non-determinism dominates at N=5.** On issue 815 R1, both arms produced *identical* 1144-char diffs but got different reviewer verdicts. Same input, different judgment. At larger N this averages out. At N=5 it dominates.
