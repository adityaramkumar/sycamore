# Results: Co-Optimizing Coding and Review Agents

This is the Part 3 writeup for the worktrial. Companion docs:
- [DESIGN.md](DESIGN.md) — Part 1 system design.
- [PROBLEM.md](../PROBLEM.md) — original problem statement.

## TL;DR

- **Headline metric**: `test_pass_rate` (oracle-grounded, held-out-capable) on Phase B's 5 issues, comparing a fully-enabled arm (git-history retrieval + memory + distillation) against an ablated arm (neither).
- **Ablation design**: same 5 issues, same model (Sonnet), same seed. Only difference is whether the coder gets historical context and whether distillation runs.
- **Result**: *(filled in after runs complete)*
- **Failure modes observed**: *(filled in after runs complete)*
- **What I'd do with 2 more weeks / 2 more months**: see the roadmap section at the bottom.

---

## What we actually built

The system is a per-issue loop with three independently-controllable context layers feeding the coder:

```
coder system prompt
  ← git history block   (harness/history.py, toggle: --no-history)
  ← memory block         (harness/memory.py,  toggle: --ablate)
  ← reviewer feedback    (in-round, always on)
```

And a test-based oracle (`harness/oracle.py`) that runs both a targeted slice and a broader regression check, hidden from the reviewer. The reviewer has its own memory of calibration cases (win/loss table against the oracle). Stability guardrails are held-out split, alternating updates, reviewer freeze on drift.

Architecture diagram is in [DESIGN.md § 7](DESIGN.md#architecture).

Implementation: ~15 commits on top of the starter harness, ending at `TODO: FILL HEAD SHA`. Notable commits:

| Commit | What it added |
|---|---|
| `1736840` | `harness/oracle.py` — pytest with addopts-override and fallback |
| `4fe129c` | Loop integration (oracle hidden from reviewer) |
| `dd6f5bc` | `harness/metrics.py` — win/loss confusion, balance-gap alerts |
| `4499d85` | `harness/memory.py` — capped bullet stores + category tagging |
| `4290cf6` | `harness/distill.py` — 2×2 calibration + LLM lesson extraction |
| `363687f` | `harness/scheduler.py` — held-out + alternating + audit/freeze |
| `da14d9d` | Fix: defend against coder moving HEAD off baseline |
| `3ed2af5` | Fix: exclude empty-diff rounds from metrics |
| `3483a9c` | Coder turn budget + anti-flailing heuristics |
| `cf2240e` | Oracle broader-check (targeted AND broader must pass) |
| `e67e34a` | `harness/history.py` — pre-baseline git-log retrieval |

---

## Phase A (pilot, Haiku, 3 issues)

First real run after the pipeline came together. 3 issues: 1240, 1056, 815. Goal was to verify end-to-end integration, not to show improvement.

### Raw numbers

| Metric | Value |
|---|---|
| test_pass_rate | 66.7% (2/3) |
| first_pass_test_pass_rate | 66.7% (2/3) |
| reviewer precision | 100% |
| reviewer recall | 66.7% |
| balance_gap | 33.3% |
| memory state after run | 1 coder lesson + 1 reviewer calibration case |

### What the run revealed (and why I kept iterating)

Two concrete bugs in the system that Phase A exposed and later commits fixed:

1. **Coder HEAD-drift.** Issue 1240's coder ran `git checkout master` during a Bash call, moving HEAD off the pinned baseline. The oracle then ran against the wrong source tree (and hit pytest-cov flag errors from master's `tox.ini`). Fixed in `da14d9d`: loop verifies HEAD after each coder round and force-resets on drift.

2. **Empty-diff rounds spoofing the reviewer audit.** Haiku on issue 1240 spent 60+ Bash turns across 3 rounds without producing any diff. The empty-diff rounds counted as `false_rejection` (reviewer rejected, oracle "passed" on clean tree), which falsely tripped the reviewer freeze. Fixed in `3ed2af5`: empty-diff rounds are excluded from all reviewer-vs-oracle accounting.

Also exposed that Haiku's Bash exploration was unbounded — 60+ calls per round. Fixed in `3483a9c` with an explicit turn budget (25) and anti-flailing heuristics (halt at half-budget with no edits; halt after 8 consecutive Bash calls without an Edit).

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
| 1240 | humanize-boundary | FAILED — coder produced no diff |
| 607  | parsing-edge-case | *(not in Phase A)* |

Ran in parallel via separate git worktrees (`./arrow` + `./arrow-ablate`), separate `TRACES_DIR`, separate `MEMORY_DIR`. Same model (Sonnet), same seed, same max-rounds (3).

### Headline

*Table filled in after runs complete.*

### Per-issue breakdown

*Filled in after runs complete.*

### What the comparison tells us

*Filled in after runs complete.*

---

## Metrics I chose and why

Metric choice follows DESIGN.md § 8 and the empty-diff-exclusion fix from Phase A.

**Primary (oracle-grounded, cannot be gamed by the agents):**
- `test_pass_rate`: final-round diff must be non-empty AND pass oracle (targeted + broader slice).

**Coder-specific:**
- `first_pass_test_pass_rate`: did the coder fix it without any reviewer help?
- `rounds_to_oracle_pass`: loop efficiency, only counts rounds where a non-empty diff passed.

**Reviewer-specific:**
- `reviewer_precision` = P(tests pass | approved). Reviewer's approvals must be meaningful.
- `reviewer_recall` = P(approved | tests pass). Reviewer shouldn't block good fixes.
- `reviewer_fpr` = P(approved | tests fail). Reward-hacking / reviewer-collapse signal.

**Balance / pathology detectors:**
- `balance_gap = |approval_rate − test_pass_rate|`. Gap > 0.30 trips `reward_hacking_warning`.
- Approval saturation (≥ 0.95 or ≤ 0.05) trips collapse alerts.

Metrics deliberately **not** chosen as primary:
- Raw approval rate — gameable by the reviewer; moved to balance-monitor role only.
- Diff size — measures volume, not correctness.
- Reviewer comment addressal rate (round-over-round region overlap) — implemented but heuristic, secondary signal only.

---

## Failure modes observed (and what the guardrails caught)

*Filled in after runs complete. Placeholder categories:*
- Reward-hacking warning (balance gap)
- Reviewer freeze events
- Empty-diff rounds
- HEAD drift (caught by the post-commit loop audit)

---

## What I'd do differently

### With 2 more weeks

1. **Run more seeds.** N=5 in one arm is statistically weak. 2-3 seeds × 5 issues × 2 arms is ~6hrs but would give real error bars.
2. **Value function head.** A second prompt that estimates P(tests pass | diff) without being allowed to approve/reject. Trained from oracle labels. Useful as a tie-breaker when the primary reviewer is frozen or uncertain.
3. **Best-of-N at inference.** Coder produces K=3 candidate diffs per round; rank by the value function or by broader-slice pass rate; submit top-1.
4. **Curriculum ordering.** Sort training-stream issues by a difficulty heuristic (single-file → multi-file; humanize → DST).
5. **Extend history retrieval to the reviewer.** Give it `git blame` context on the lines the coder modified. Currently the coder gets historical context; the reviewer doesn't.
6. **Learn the categorizer.** Today `harness/memory.categorize` is keyword-based. Could be an LLM call or a small classifier trained on our labeled 25 issues.
7. **Per-category metrics.** Report `test_pass_rate` broken out by bug category. Would tell us whether certain categories benefit more from distillation.

### With 2 more months

1. **Cross-repo generalization.** Swap the target repo. See how much of the memory transfers. Likely answer: very little of the lessons (they're arrow-specific), but the *structure* of what gets learned generalizes. Could tell us which parts of our architecture are repo-generic.
2. **Preference learning from (accepted, rejected) diff pairs.** Every round gives us such a pair. Fit a lightweight preference model; use as a soft signal alongside (or eventually instead of) the prompt-level reviewer.
3. **Mutation testing as a second oracle.** Targeted tests passing isn't proof the fix is correct; mutants surviving the tests tells us test coverage is weak. Adds another signal orthogonal to pass/fail.
4. **Human-in-the-loop slice.** Every Nth approved diff gets a 30-second human read. Catches subtle correctness issues the oracle misses. Incorporates a small-but-real ground-truth stream alongside the synthetic signals.
5. **Richer base-prompt optimization.** The current "memory" is a bullet list; a better slice is a periodic LLM-driven rewrite of the base system prompt itself (OPRO / APE / DSPy style), informed by aggregate trace patterns. The Phase A flailing on 1240 is exactly the kind of failure that prompt optimization should fix, complementary to per-issue memory.
6. **Real ablation matrix.** Right now I measure full vs ablate. A proper 2×2 (memory × history) would tell us which of the two context layers is pulling weight.

---

## Limitations and honest caveats

- **N is small.** 5 issues per arm in Phase B is enough to see qualitative differences but not enough for statistical significance. Larger N is an API-cost question, not a design question.
- **Tests ≠ correctness.** Even with the broader-slice check, "passes arrow's own tests" is not the same as "the bug is really fixed". Mutation testing (2-month roadmap) would harden this.
- **`reviewer_recall` can't distinguish over-asking from useful-asking.** When the reviewer rejects a fix that passed tests but lacked test coverage, our metrics call that a "false rejection" — but in practice it may be correct reviewer behavior. See the Phase A issue 815 round-1 case.
- **One repo.** All learning is arrow-specific. Generalization is untested (and is the 2-month direction).
- **Prompt-level not weight-level.** Distillation writes to JSON files the agent reads from its prompt. True RLHF would require training infrastructure we don't have.
