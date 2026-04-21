# Sycamore — Codebase Guide

Sycamore is a research prototype that co-optimizes an AI coding agent and an AI
review agent through interaction traces and self-play, anchored to a real pytest
oracle. This document is the starting point for anyone working on the codebase.

## Project layout

```
sycamore/
├── harness/            the implementation
│   ├── loop.py         orchestrator + CLI entry-point
│   ├── coder.py        coding agent (Read/Write/Edit/Bash + submit_fix tool)
│   ├── reviewer.py     review agent (submit_review tool)
│   ├── oracle.py       pytest runner; the only ground-truth signal
│   ├── memory.py       JSON-backed capped stores for both agents
│   ├── distill.py      post-issue trace mining → memory updates
│   ├── history.py      baseline-scoped git-log RAG for the coder
│   ├── scheduler.py    held-out split, alternating updates, reviewer audit
│   ├── metrics.py      15+ oracle-grounded metrics + pathology detectors
│   └── eval.py         CLI wrapper over metrics
├── data/
│   └── issues.json     25 curated bugs, baseline SHA, fix commits
├── tests/              unit tests (pytest)
├── docs/               design docs, results, overview
├── scripts/
│   └── run_parallel_ablation.sh   parallel FULL vs ABLATE comparison
├── .env.example        environment-variable reference
├── requirements.txt
└── CLAUDE.md           this file
```

## Target repository

The system operates on [arrow-py/arrow](https://github.com/arrow-py/arrow), a
Python date/time library. Clone it to `./arrow` before running:

```bash
git clone https://github.com/arrow-py/arrow ./arrow
```

All agents run against a **single pinned baseline commit** (`c9cecaf`). Every
eval issue's fix commit is blacklisted from history retrieval to prevent data
leakage.

## Common commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run on one issue
python -m harness.loop --issue 1015

# Run a subset (order matters for parity scheduling)
python -m harness.loop --issues 1056,815,1224 --max-rounds 3

# Run all 25 issues with the held-out split
python -m harness.loop --all

# Ablation (no memory, no history)
python -m harness.loop --all --ablate --no-history

# Evaluate a traces directory
python harness/eval.py ./traces
python harness/eval.py ./traces --json --held-out 1015,541,686

# Smoke-test the oracle against current repo state (no baseline checkout)
python -m harness.oracle 1015

# Inspect history retrieval for an issue
python -m harness.history 1015

# Run unit tests
pytest tests/

# Parallel ablation (requires ANTHROPIC_API_KEY)
ANTHROPIC_API_KEY=sk-ant-... AGENT_MODEL=sonnet \
  scripts/run_parallel_ablation.sh 1056 815 1224 1240 607
```

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `TARGET_REPO_PATH` | `./arrow` | Path to the arrow clone |
| `TRACES_DIR` | `./traces` | Where per-issue JSON traces are written |
| `MEMORY_DIR` | `./memory` | Where memory JSON files are persisted |
| `AGENT_MODEL` | `haiku` | Model alias for coder + reviewer agents |
| `DISTILL_MODEL` | `$AGENT_MODEL` | Model for coder lesson distillation |
| `MAX_TURNS` | `40` | Coder turn budget |
| `ORACLE_TIMEOUT_S` | `120` | Pytest timeout per oracle invocation |
| `ORACLE_BROADER_CHECK` | `1` | Set to `0` to disable full-suite regression |
| `CLI_PATH` | auto | Override path to the `claude` CLI binary |
| `ANTHROPIC_API_KEY` | — | Required for direct API auth (skips CLI) |

See `.env.example` for a copy-pasteable template.

## Architecture

### The core loop (per issue)

```
for round in 1..max_rounds:
  1. Coder reads issue + optional memory + history block → produces a diff
  2. Oracle runs targeted pytest slice (+ broader regression check) → hidden from reviewer
  3. Reviewer reads diff + optional calibration memory → approve or reject
  4. If approved → break; else feedback goes to coder for next round

Post-issue:
  5. Distillation: extract coder lesson (LLM) from oracle-passed diffs
  6. Distillation: structural 2x2 reviewer calibration from every round
  7. Scheduler: alternating updates (odd issues → coder; even → reviewer)
  8. Reviewer audit: freeze if precision < 60% or approval saturates
```

### Key information-flow contract

The oracle result is **never** passed to the reviewer prompt. This is the
central structural invariant that prevents reward hacking: the reviewer cannot
"cheat" by reading the oracle's verdict.

### Four pathological dynamics and defenses

| Pathology | Defense |
|---|---|
| Reward hacking (coder games reviewer) | Coder memory gated on oracle pass; oracle hidden from reviewer |
| Reviewer collapse (too lenient/strict) | 2x2 calibration from oracle; audit + freeze |
| Mode collapse (both narrow to one pattern) | Category-balanced retrieval; per-tag caps; alternating updates |
| Distributional shift (reviewer lags coder) | Reviewer updated from fresh coder traces on even issues |

## Module reference

### `harness/loop.py`
Orchestrator. The `main()` function is the CLI entry-point. Key functions:
- `run_issue()` — runs the coder-reviewer loop for one issue
- `git_checkout_baseline()` — pins the repo to the baseline commit
- `assert_at_baseline()` — detects if the coder moved HEAD (voids the round)

### `harness/coder.py`
`run_coder(issue, extra_context, max_turns, memory_block, history_block) → str`

Runs a claude-agent-sdk agent in the target repo with Read/Write/Edit/Bash tools
and a custom `submit_fix` MCP tool. Returns the `git diff` string.

Safety: turn budget (default 40), bash-streak limit (12 consecutive Bash calls
without an Edit halts the agent).

### `harness/reviewer.py`
`run_reviewer(issue, diff, memory_block, history_block) → {"approved": bool, "comments": [str]}`

Runs the reviewer agent with a `submit_review` MCP tool. No filesystem access;
reads only the diff and its memory block.

### `harness/oracle.py`
`run(issue, repo, timeout_s) → OracleResult`

Two-stage verification:
1. Targeted slice: pytest on test files inferred from `issue.files_changed`
2. Broader regression: full `tests/` (if targeted passed and `ORACLE_BROADER_CHECK=1`)

`OracleResult.passed` is True only if both stages pass.

### `harness/memory.py`
`CoderMemory` and `ReviewerMemory` — JSON-backed stores under `MEMORY_DIR`.
- Per-tag capacity: 8 items; eviction by lowest `hits - uses` (LRU-ish)
- Atomic write via temp-file rename
- `categorize(issue)` — keyword heuristic returning one of 5 category strings

### `harness/distill.py`
`update_from_trace(trace, issue, coder_memory, reviewer_memory, schedule)`

Post-issue memory updates:
- **Reviewer**: structural — every round produces one calibration item (2x2 outcome)
- **Coder**: LLM call — asks `DISTILL_MODEL` to extract 1-2 generalizable lessons
  from the best oracle-passed diff in the trace

### `harness/history.py`
`retrieve_similar_fixes(issue, repo_abs, baseline, k, forbidden_shas) → list[HistoricalCommit]`

RAG over git log: keyword-scores commits touching the same files, returns top-k
with a diff excerpt on the top result. Bounded to commits at or before baseline;
forbidden_shas blacklists eval fix commits.

### `harness/scheduler.py`
Pure-function policy layer. Three responsibilities:
- `make_split()` — deterministic train/held-out split
- `schedule_for()` — alternating update parity per training-stream index
- `reviewer_audit()` — compute `ReviewerHealth`; freeze if thresholds exceeded

### `harness/metrics.py`
`compute(traces) → dict` and `compute_split(traces, held_out) → dict`

Computes `test_pass_rate`, `first_pass_test_pass_rate`, reviewer precision/recall/FPR,
balance gap, and approval-saturation alerts. All metrics are oracle-grounded (not
reviewer-self-reported).

### `harness/eval.py`
Thin CLI wrapper over `metrics.py`. Accepts a traces directory, optional
`--held-out` split, and optional `--json` flag.

## Testing

Unit tests live in `tests/`. Run with:

```bash
pytest tests/ -v
```

Tests cover: oracle output parsing, memory eviction logic, metrics computation,
scheduler split/audit, history keyword extraction, and distillation helpers.
No live LLM or git calls are made in unit tests — all external dependencies are
mocked or tested with synthetic fixtures.

## Adding a new issue

1. Add an entry to `data/issues.json` with `number`, `title`, `body_summary`,
   `fix_commit`, `fix_pr`, `files_changed`, and `url`.
2. Ensure the `baseline_commit` field still predates your new fix commit.
3. Run `python -m harness.oracle <new_issue_number>` with the baseline checked
   out to confirm the targeted test slice works.

## Code conventions

- All modules import from `harness.*` when available; fall back to bare imports
  for script invocation (`if ImportError`).
- Async agent calls use `anyio.run()` at the public API boundary; internals are
  `async def *_async(...)`.
- Print-based logging uses a `[module]` prefix (e.g. `[coder]`, `[distill]`).
- Oracle output is **never** passed to reviewer prompts — enforce this in code
  review.
- Memory writes are atomic (temp-file + `os.replace`).
