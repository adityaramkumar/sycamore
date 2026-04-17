# Response to the Worktrial Prompt

Direct point-by-point response to [PROBLEM.md](PROBLEM.md). This doc is
deliberately short. The substantive content lives in
[DESIGN.md](DESIGN.md) and [RESULTS.md](RESULTS.md). What you get here
is every question PROBLEM.md asks, with a concise answer and a pointer
to where the detail lives.

---

## Part 1. System Design

### 1.1 Data extraction

Signals we extract from traces, ranked by reliability:

| Signal | Reliable? | What we use it for |
|---|---|---|
| Did the oracle (pytest) pass on a non-empty diff? | **Yes**, objective and local | Headline metric. Gates coder lesson distillation. |
| First-round oracle outcome (coder alone) | **Yes** | Isolates coder quality from reviewer help |
| Reviewer verdict agreed with oracle? (per round) | **Yes**, derived from oracle | Drives the reviewer's 2x2 win/loss table in memory |
| `approval_rate - test_pass_rate` over a window | **Yes** | Reward-hacking detector |
| Comment addressal (did the round N+1 diff touch the region the reviewer flagged?) | Heuristic | Secondary signal only |
| Raw reviewer approval | **No** by itself | Never a training signal by itself |

Empty-diff rounds (coder produced no changes) are excluded from every
reviewer-vs-oracle metric. The oracle trivially passes on a clean tree,
the reviewer trivially rejects "no changes", and counting these would
falsely penalize the reviewer.

See [DESIGN.md section "What signals we actually trust"](DESIGN.md) and
`harness/metrics.py::_reviewer_confusion` and `compute`.

### 1.2 Improvement mechanism

Both agents get a small, capped, JSON-backed bullet memory that is
prepended to their system prompt. We do not fine-tune.

- **Coder** (`harness/memory.py::CoderMemory` plus `harness/distill.py`).
  LLM-distilled "lessons learned" from oracle-passing diffs, tagged by
  bug category. Capped at 8 per category, evicted by lowest
  `hits - uses`. Retrieval per issue is the top 2 in the current
  category plus 1 bullet from a different category (diversity anchor
  to prevent topic collapse).
- **Reviewer** (`harness/memory.py::ReviewerMemory` plus `harness/distill.py`).
  Calibration cases tagged by the 2x2 outcome of (verdict, oracle):
  `true_approval`, `true_rejection`, `false_approval`, `false_rejection`.
  Retrieval is up to one example of each outcome, in win/loss-table
  order so the prompt mirrors the mental model.

On top of this, the coder also gets a git-history block: up to 3
pre-baseline commits that touched the same files, retrieved at issue
start via `git log c9cecaf --grep=<keywords>` (`harness/history.py`).
This addresses the cold-start problem. Memory is inductive and
accumulates slowly, but the arrow repo has about 800 pre-baseline
fixes that are a rich source of concrete examples. Baseline-scoping
makes eval-issue leakage impossible by construction (verified 0 of 25
leaks).

Distillation schedule is alternating. Odd-numbered training issues
update the coder. Even-numbered update the reviewer. This is a GAN
training trick: the two agents never update simultaneously on the
same trace.

### 1.3 Co-optimization stability

| Pathology from PROBLEM.md | Defense | Detection |
|---|---|---|
| **Reward hacking** | Coder memory only updates on traces where the tests passed. Reviewer approval alone never qualifies. Oracle output is never in the reviewer prompt (asymmetric information). | `balance_gap = \|approval_rate - test_pass_rate\|`, alert when > 0.30 |
| **Reviewer collapse** | Reviewer memory updates from 2x2 agreement with oracle, so both over-lenience and over-strictness register. | `reviewer_audit` over a sliding window: freeze memory updates if precision < 0.6 OR approval saturates above 0.95 or below 0.05 OR balance gap > 0.30 |
| **Mode collapse** | Category-balanced retrieval (at least one bullet from a different category). Per-tag memory caps so no single topic can swamp. Alternating updates so the two sides can't co-overfit. | Retrieval-entropy tracking (stretch) |
| **Distributional shift** | Reviewer is updated on even-numbered training issues from fresh coder traces, so calibration tracks the current coder distribution. | Sliding-window reviewer accuracy |

See [DESIGN.md section "Stopping the four ways this can go wrong"](DESIGN.md)
and `harness/scheduler.py`.

### 1.4 Ground-truth anchoring

The pytest oracle (`harness/oracle.py`) is the only ground-truth
signal. It runs a targeted slice of arrow's test suite against the
patched working tree, plus a broader regression check on the full
`tests/` directory when the targeted slice passes. Both must pass for
`result.passed = True`.

Anti-drift guarantees:

- **Held-out split** (`harness/scheduler.py::make_split`). 7 of 25
  issues are reserved and never feed distillation. All headline
  metrics are also reported on this split. If the agents are gaming
  the oracle on training, held-out reveals it.
- **Asymmetric information** is enforced structurally. `loop.run_issue`
  never passes oracle output to the reviewer's prompt, and the
  reviewer never sees the coder's memory.
- **Memory items are capped, append-only, and LRU-evicted**. Stored as
  plain JSON under `memory/`. A human can `cat` the file and see
  exactly what was "learned".
- **Post-coder HEAD audit** (added after a Phase-A contamination bug).
  Verifies the target repo is still at baseline after each coder
  round. Force-resets and voids the round if the coder's Bash
  commands moved HEAD.

For repos without a good test suite (beyond this prototype), see
[DESIGN.md section "What we don't borrow"](DESIGN.md) and the
"2-month roadmap" in [RESULTS.md](RESULTS.md).

---

## Part 2. Prototype

Checklist from PROBLEM.md:

| Requirement | Status | Where it lives |
|---|---|---|
| Implement the interaction loop | Done | `harness/loop.py::run_issue` |
| Collect and structure traces | Done | `traces/issue_<N>.json`. Schema matches `Trace = {issue, [(pr, review), ...]}` from PROBLEM.md, with oracle, schedule, and memory-retrieval IDs added. |
| At least one improvement mechanism **for the coder** | Done | Git-history block (`harness/history.py`) plus lesson memory (`harness/memory.py::CoderMemory` plus `harness/distill.py`) |
| At least one improvement mechanism **for the reviewer** | Done | Calibration-case memory with 2x2 win/loss tagging (`harness/memory.py::ReviewerMemory` plus `harness/distill.py`) |
| Show measurable improvement on a metric I define | Partial. See caveat below. | [RESULTS.md](RESULTS.md) |
| At least one anti-pathology mechanism | Done. Four of them: held-out split, alternating updates, reviewer audit and freeze, category-balanced retrieval. | `harness/scheduler.py` plus `harness/memory.py::CoderMemory.render_for` |

**Caveat on "measurable improvement"**: we ran four phases.

- Phase A (Haiku, 3 issues): pipeline integration test. `test_pass_rate` 66.7%, mostly caught bugs in our own system.
- Phase B (Sonnet, 5 issues, parallel): headline 100% in both arms. `avg_rounds_to_oracle_pass` 1.2 vs 1.4 favoring FULL. Surfaced the reviewer-over-asking defect.
- Phase C (Sonnet, 2 issues, post-fix parallel): confirmed the over-asking fix. `reviewer_recall` 50% to 66.7%.
- Phase D (Sonnet, 4 issues, sequential): first run that exercised cross-issue memory accumulation. Showed memory helps the reviewer recalibrate (1224 got approved after 815's false-rejections loaded the reviewer's calibration cases). Also exposed a new defect: the coder sometimes writes regression tests that fail on its own fix, and the reviewer can't catch that because it doesn't run tests.

Net: the mechanisms all work individually and we have clean metrics showing each one firing. The headline `test_pass_rate` moves both ways depending on which defects are active. Real measurable improvement within a single phase requires closing the broken-test loophole first. See [RESULTS.md](RESULTS.md) for the full per-phase breakdown.

---

## Part 3. Evaluation and Analysis

### Metrics I chose and why

Primary: **`test_pass_rate`** on final-round non-empty diffs,
oracle-grounded.

Decomposition:

- `first_pass_test_pass_rate`: coder quality without reviewer help.
- `rounds_to_oracle_pass`: loop efficiency.
- `reviewer_precision`, `recall`, `false_positive_rate`: reviewer
  calibration against oracle.
- `balance_gap = |approval_rate - test_pass_rate|`: reward-hacking or
  over-asking detector.

Deliberately not primary: raw approval rate (gameable), diff size
(volume, not correctness), comment addressal (heuristic). See
`harness/metrics.py` and [DESIGN.md section "How we measure success"](DESIGN.md).

### Did both agents improve?

Phase B measured history retrieval (not full memory accumulation,
since parallel mode isolated per-issue state). The full arm edged the
ablate arm by 0.2 rounds on average. Within-arm memory accumulation is
not yet measured. That's the gap for the next run.

Neither arm improved at the other's expense. Reviewer precision was
100% in both arms, and both saw the same "reviewer over-asks" pattern,
suggesting that behavior is prompt-level and unaffected by the context
layers we varied.

### Failure modes observed (and prevented)

Observed during the build, before fixes:

1. **Coder moving HEAD off baseline** via its Bash tool. Caught.
   Post-coder HEAD audit added (`harness/loop.py::assert_at_baseline`).
2. **Empty-diff rounds spoofing the reviewer audit** (oracle trivially
   passed, reviewer trivially rejected, counted as false_rejection).
   Caught. Empty-diff exclusion added across metrics and distillation.
3. **Coder flailing** with Haiku (60+ Bash turns, no edit). Caught.
   Turn-budget and Bash-streak heuristics added.
4. **Over-aggressive anti-flailing** killing Sonnet's thorough
   exploration at turn 12. Caught. Heuristic relaxed.

Observed in Phase B results:

5. **Reviewer chronically over-asking.** 7 of 15 non-empty rounds got
   a false-rejection. Precision is 100%, recall is 50% to 57%. This is
   the big open defect. Fix direction: reviewer prompt tuning
   (downweight "missing tests" when the fix is already correct), plus
   calibration from accumulated `false_rejection` cases.

### 2 weeks and 2 months roadmap

**2 weeks:**

- **Oracle-side verification that the coder's new tests actually fail on the pre-fix code.** Closes the "coder tests its own homework" loophole Phase D uncovered, where a coder that writes a broken regression test gets a false-approval because the reviewer reads statically and can't catch it. Roughly 30 lines in `harness/oracle.py`.
- Value-function reviewer (AlphaGo-style P(tests pass) head) as a sanity check.
- Best-of-N candidate diffs ranked by value and reviewer.
- Curriculum ordering (easy to hard).
- Reviewer git-blame context (`git blame` on the specific lines the coder modified, finer than the existing commit-level history block).
- Dedupe calibration cases per issue so one hard issue doesn't dominate the reviewer's memory and trigger a premature freeze.

**2 months:**

- Cross-repo generalization. Does an arrow memory help on a different
  library?
- Preference learning from (accepted, rejected) diff pairs at token
  level.
- Mutation testing as a second oracle, orthogonal to pass/fail.
- Human-in-the-loop sample checks (30 seconds per Nth diff).
- Base-prompt rewrite (OPRO, APE, DSPy style) complementing per-issue
  memory.

Full reasoning in [RESULTS.md section "What I'd do differently"](RESULTS.md).

---

## Addressing the "Interesting Initial Directions"

> Notice they only have to co-optimize for only one repo

The system is deliberately per-repo. All retrieval, categorization,
and memory state are scoped to the target repo. Cross-repo transfer
is documented as the 2-month direction.

> Both the coder and reviewer can have access to:
> - Git history
> - Traces of each other

Both the **coder** and the **reviewer** get git-history access via
`harness/history.py::retrieve_similar_fixes`. Same pre-baseline
commits are retrieved per issue and rendered into each agent's
system prompt via `history_block`. Baseline-scoped and
leakage-proof. The coder uses it to see how past fixes were shaped;
the reviewer uses it to calibrate against what this repo typically
ships.

Stretch direction: give the reviewer `git blame` on specifically
the modified lines, not just the file-level commit list. That's
still in the 2-week roadmap.

Both agents have indirect access to each other's traces via distilled
memory items. The reviewer's memory includes diff snippets from the
coder's past submissions. The coder's memory includes lessons
extracted from rounds where the reviewer's comments preceded
oracle-passing diffs. Direct raw-trace access (rather than distilled)
is an explicit design choice against. Raw traces in the prompt are
expensive and noisy. Distillation is the whole point.
