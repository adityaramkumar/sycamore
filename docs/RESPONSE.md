# Response to the Worktrial Prompt

Direct point-by-point response to [PROBLEM.md](PROBLEM.md). This doc is
deliberately short — the substantive content lives in
[DESIGN.md](DESIGN.md) and [RESULTS.md](RESULTS.md) — but every
question PROBLEM.md asks is answered here with a pointer to where the
detail lives.

---

## Part 1 — System Design

### 1.1 Data extraction

Signals we extract from traces, ranked by reliability:

| Signal | Reliable? | What we use it for |
|---|---|---|
| Did the oracle (pytest) pass on a non-empty diff? | **Yes** — objective and local | Headline metric; gates coder lesson distillation |
| First-round oracle outcome (coder alone) | **Yes** | Isolates coder quality from reviewer help |
| Reviewer verdict agreed with oracle? (per round) | **Yes** — derived from oracle | Drives the reviewer's 2×2 win/loss table in memory |
| `approval_rate − test_pass_rate` over a window | **Yes** | Reward-hacking detector |
| Comment addressal (did the round N+1 diff touch the region reviewer flagged?) | Heuristic | Secondary signal only |
| Raw reviewer approval | **No**, on its own | Never a training signal by itself |

Empty-diff rounds (coder produced no changes) are excluded from every
reviewer-vs-oracle metric because the oracle trivially passes on a
clean tree and the reviewer trivially rejects "no changes" — counting
this would falsely penalize the reviewer.

See [DESIGN.md § "What signals we actually trust"](DESIGN.md) and
`harness/metrics.py::_reviewer_confusion` / `compute`.

### 1.2 Improvement mechanism

Both agents get a **small, capped, JSON-backed bullet memory** that is
prepended to their system prompt. We do not fine-tune.

- **Coder** (`harness/memory.py::CoderMemory` + `harness/distill.py`):
  LLM-distilled "lessons learned" from oracle-passing diffs, tagged by
  bug category. Capped at 8 per category, evicted by lowest
  `hits − uses`. Retrieval per issue is top-2 in the current category
  plus 1 bullet from a *different* category (diversity anchor to
  prevent topic collapse).
- **Reviewer** (`harness/memory.py::ReviewerMemory` + `harness/distill.py`):
  calibration cases tagged by the 2×2 outcome of (verdict, oracle) —
  `true_approval`, `true_rejection`, `false_approval`, `false_rejection`.
  Retrieval is up to one example of each outcome, in win/loss-table
  order so the prompt mirrors the mental model.

On top of this, the coder also gets a **git-history block**: up to 3
pre-baseline commits that touched the same files, retrieved at issue
start via `git log c9cecaf --grep=<keywords>` (`harness/history.py`).
This addresses the cold-start problem — memory is inductive and
accumulates slowly, but the arrow repo has ~800 pre-baseline fixes
that are a rich source of concrete examples. Baseline-scoping makes
eval-issue leakage impossible by construction (verified 0/25).

Distillation schedule: alternating. Odd-numbered training issues
update the coder; even-numbered update the reviewer. This is a GAN
training trick — the two agents never update simultaneously on the
same trace.

### 1.3 Co-optimization stability

| Pathology from PROBLEM.md | Defense | Detection |
|---|---|---|
| **Reward hacking** | Coder memory only updates on traces where **tests** passed. Reviewer approval alone never qualifies. Oracle output is **never** in the reviewer prompt (asymmetric information). | `balance_gap = \|approval_rate − test_pass_rate\|`, alert > 0.30 |
| **Reviewer collapse** | Reviewer memory updates from 2×2 agreement with oracle, so both over-lenience and over-strictness register. | `reviewer_audit` over a sliding window: freeze memory updates if precision < 0.6 OR approval saturates ≥ 0.95 / ≤ 0.05 OR balance gap > 0.30 |
| **Mode collapse** | Category-balanced retrieval (at least one bullet from a *different* category). Per-tag memory caps so no single topic can swamp. Alternating updates so the two sides can't co-overfit. | Retrieval-entropy tracking (stretch) |
| **Distributional shift** | Reviewer is updated on even-numbered training issues from *fresh* coder traces, so calibration tracks the current coder distribution. | Sliding-window reviewer accuracy |

See [DESIGN.md § "Stopping the four ways this can go wrong"](DESIGN.md)
and `harness/scheduler.py`.

### 1.4 Ground-truth anchoring

The pytest oracle (`harness/oracle.py`) is the only ground-truth
signal. It runs a targeted slice of arrow's test suite against the
patched working tree, plus a broader regression check on the full
`tests/` directory when the targeted slice passes. Both must pass for
`result.passed = True`.

Anti-drift guarantees:

- **Held-out split** (`harness/scheduler.py::make_split`): 7 of 25
  issues are reserved, never feed distillation. All headline metrics
  are also reported on this split. If the agents are gaming the
  oracle on training, held-out reveals it.
- **Asymmetric information** is enforced structurally:
  `loop.run_issue` never passes oracle output to the reviewer's
  prompt, and the reviewer never sees the coder's memory.
- **Memory items are capped and append-only + LRU-evicted**. Stored
  as plain JSON under `memory/`; a human can `cat` them and see
  exactly what was "learned".
- **Post-coder HEAD audit** (added after a Phase-A contamination bug):
  verifies the target repo is still at baseline after each coder
  round, force-resets and voids the round if the coder's Bash
  commands moved HEAD.

For repos without a good test suite (beyond this prototype), see
[DESIGN.md § "What we don't borrow"](DESIGN.md) and the "2-month
roadmap" in [RESULTS.md](RESULTS.md).

---

## Part 2 — Prototype

Checklist from PROBLEM.md:

| Requirement | Status | Where it lives |
|---|---|---|
| Implement the interaction loop | ✅ | `harness/loop.py::run_issue` |
| Collect + structure traces | ✅ | `traces/issue_<N>.json`, schema matches `Trace = {issue, [(pr, review), ...]}` from PROBLEM.md, with oracle + schedule + memory-retrieval IDs added |
| At least one improvement mechanism **for the coder** | ✅ | Git-history block (`harness/history.py`) + lesson memory (`harness/memory.py::CoderMemory` + `harness/distill.py`) |
| At least one improvement mechanism **for the reviewer** | ✅ | Calibration-case memory with 2×2 win/loss tagging (`harness/memory.py::ReviewerMemory` + `harness/distill.py`) |
| Show measurable improvement on a metric I define | Partial — see caveat below | [RESULTS.md](RESULTS.md) |
| At least one anti-pathology mechanism | ✅ (four: held-out split, alternating updates, reviewer audit/freeze, category-balanced retrieval) | `harness/scheduler.py` + `harness/memory.py::CoderMemory.render_for` |

**Caveat on "measurable improvement"**: Phase B was 5 issues × 2 arms
on Sonnet. Both arms achieved 100% `test_pass_rate`; history retrieval
gave a small edge on `avg_rounds_to_oracle_pass` (1.2 vs 1.4). The
effect size is small and N=5 is below statistical power. I'd want a
sequential within-arm run on more issues to claim a headline win, but
the pipeline demonstrably works and the mechanisms fire correctly.
See [RESULTS.md § "Findings"](RESULTS.md).

---

## Part 3 — Evaluation & Analysis

### Metrics I chose and why

Primary: **`test_pass_rate`** on final-round non-empty diffs, oracle-grounded.

Decomposition:
- `first_pass_test_pass_rate` — coder quality without reviewer help
- `rounds_to_oracle_pass` — loop efficiency
- `reviewer_precision` / `recall` / `false_positive_rate` — reviewer calibration against oracle
- `balance_gap = |approval_rate − test_pass_rate|` — reward-hacking / over-asking detector

Deliberately **not** primary: raw approval rate (gameable), diff size
(volume, not correctness), comment addressal (heuristic). See
`harness/metrics.py` and [DESIGN.md § "How we measure success"](DESIGN.md).

### Did both agents improve?

Phase B measured history retrieval (not full memory accumulation,
since parallel mode isolated per-issue). Full arm edged the ablate
arm by 0.2 rounds on average. Within-arm memory accumulation is not
yet measured — that's the gap for the next run.

**Neither arm improved at the other's expense** — reviewer precision
was 100% in both arms, and both saw the same "reviewer over-asks"
pattern, suggesting that behavior is prompt-level and unaffected by
the context layers we varied.

### Failure modes observed (and prevented)

Observed during the build, before fixes:
1. **Coder moving HEAD off baseline** via its Bash tool — caught,
   post-coder HEAD audit added (`harness/loop.py::assert_at_baseline`).
2. **Empty-diff rounds spoofing the reviewer audit** (oracle trivially
   passed, reviewer trivially rejected, counted as false_rejection) —
   caught, empty-diff exclusion added across metrics and distillation.
3. **Coder flailing** with Haiku (60+ Bash turns, no edit) — caught,
   turn-budget + Bash-streak heuristics added.
4. **Over-aggressive anti-flailing** killing Sonnet's thorough
   exploration at turn 12 — caught, heuristic relaxed.

Observed in Phase B results:
5. **Reviewer chronically over-asking** — 7 of 15 non-empty rounds
   got a false-rejection. Precision is 100%, recall is 50-57%. This
   is the big open defect. Fix direction: reviewer prompt tuning
   (downweight "missing tests" when the fix is already correct) +
   calibration from accumulated `false_rejection` cases.

### 2 weeks / 2 months roadmap

**2 weeks:**
- Sequential within-arm runs to actually measure memory accumulation.
- Reviewer prompt tuning + split `balance_gap` alert into `reward_hacking` vs `reviewer_over_asking`.
- Value-function reviewer (AlphaGo-style P(tests pass) head) as a sanity check.
- Best-of-N candidate diffs ranked by value + reviewer.
- Curriculum ordering (easy → hard).
- Extend history retrieval to the reviewer (git-blame context).

**2 months:**
- Cross-repo generalization (does an arrow memory help on a different library?).
- Preference learning from (accepted, rejected) diff pairs at token level.
- Mutation testing as a second oracle, orthogonal to pass/fail.
- Human-in-the-loop sample checks (30s per Nth diff).
- Base-prompt rewrite (OPRO/APE/DSPy style) complementing per-issue memory.

Full reasoning in [RESULTS.md § "What I'd do differently"](RESULTS.md).

---

## Addressing the "Interesting Initial Directions"

> Notice they only have to co-optimize for only one repo

The system is deliberately per-repo. All retrieval, categorization,
and memory state are scoped to the target repo. Cross-repo transfer
is documented as the 2-month direction.

> Both the coder and reviewer can have access to:
> - Git history
> - Traces of each other

The **coder** has git-history access via
`harness/history.py::retrieve_similar_fixes`, hooked into its system
prompt via `history_block`. Baseline-scoped, leakage-proof.

The **reviewer** does **not** yet have git-history access — that's
the "extend history retrieval to the reviewer" item in the 2-week
roadmap. Low-effort, high-signal, just didn't land in scope.

Both agents have indirect access to each other's traces via
distilled memory items: the reviewer's memory includes diff snippets
from the coder's past submissions; the coder's memory includes
lessons extracted from rounds where the reviewer's comments preceded
oracle-passing diffs. Direct trace access (rather than distilled) is
an explicit design choice *against* — raw traces in the prompt are
expensive and noisy; distillation is the whole point.
