# Co-Optimizing AI Coding and Review Agents

A prototype that uses the interaction traces between an AI coding agent
and an AI review agent to improve both over time, with guardrails
against the usual self-play pathologies. Built against [arrow-py/arrow]'s
real historical bug backlog (about 800 pre-baseline commits, 25 curated
post-baseline fixes pinned to commit `c9cecaf`).

The three-part worktrial prompt is in [docs/PROBLEM.md](docs/PROBLEM.md).
A direct point-by-point response lives in [docs/RESPONSE.md](docs/RESPONSE.md).
Design writeup in [docs/DESIGN.md](docs/DESIGN.md), results in
[docs/RESULTS.md](docs/RESULTS.md).

[arrow-py/arrow]: https://github.com/arrow-py/arrow

## What this system does

Per issue, it runs a coder, oracle, reviewer loop:

1. **Coder** reads the issue, optionally gets a block of similar past
   commits from git history and distilled lessons from memory, then
   writes a fix.
2. **Oracle** runs a targeted slice of arrow's pytest suite plus a
   broader regression check against the patched tree. Its verdict is
   the only ground-truth signal and is **hidden from the reviewer**.
3. **Reviewer** reads the diff and either approves or returns
   structured comments. It sees only the diff and its own rubric memory.
4. If rejected, reviewer comments go back to the coder for another
   round (up to `--max-rounds`).
5. After the issue finishes, **distillation** updates the coder's
   lesson memory (if the oracle passed) and the reviewer's calibration
   memory (based on 2x2 agreement with the oracle).

On top of that, four stability guardrails: a held-out split that
never feeds distillation, alternating coder and reviewer updates, a
reviewer audit that freezes updates if precision collapses, and
category-balanced retrieval to prevent topic collapse.

## Quick start

```bash
pip install -r requirements.txt
git clone https://github.com/arrow-py/arrow ./arrow

# One issue, default settings
python -m harness.loop --issue 1015

# A specific subset
python -m harness.loop --issues 1056,815,1224 --max-rounds 3

# All 25 issues with the held-out split
python -m harness.loop --all

# Metrics
python harness/eval.py ./traces

# Ablation baseline (no memory, no history retrieval)
python -m harness.loop --all --ablate --no-history
```

Environment variables are documented in [.env.example](.env.example).
Parallel ablation runs are wrapped in
[`scripts/run_parallel_ablation.sh`](scripts/run_parallel_ablation.sh).

## Layout

```
sycamore/
├── README.md                 you are here
├── requirements.txt
├── .env.example              env-var reference
├── data/
│   └── issues.json           25 curated bugs, fix ground truth, baseline SHA
├── docs/
│   ├── PROBLEM.md            the worktrial prompt
│   ├── RESPONSE.md           direct point-by-point response to PROBLEM.md
│   ├── DESIGN.md             system design (high-schooler readable)
│   └── RESULTS.md            Phase B results and failure-mode analysis
├── harness/                  the code
│   ├── loop.py               orchestrator and CLI
│   ├── coder.py              coder agent (Claude with Read/Edit/Bash/submit_fix)
│   ├── reviewer.py           reviewer agent (Claude with submit_review)
│   ├── oracle.py             targeted pytest runner plus broader regression check
│   ├── memory.py             JSON-backed capped bullet stores for both agents
│   ├── history.py            baseline-scoped git-log retrieval for the coder
│   ├── distill.py            trace mining into memory updates (2x2 win/loss)
│   ├── scheduler.py          held-out split, alternating updates, audit/freeze
│   ├── metrics.py            oracle-grounded scoreboard and balance monitor
│   └── eval.py               thin CLI wrapper over metrics
└── scripts/
    └── run_parallel_ablation.sh
```

## Auth

Two options:

- **Claude Max via the `claude` CLI** (default). No extra config if you're
  already logged in. Set `CLI_PATH` if the binary isn't on `$PATH`.
- **Anthropic API key**. Export `ANTHROPIC_API_KEY` and the SDK picks
  it up. Required for higher concurrency or explicit model selection.

## Results one-liner

Phase B, 5 issues across 2 arms (full context vs ablated), Sonnet, 10
parallel workers, about 20 minutes wall time. Both arms hit 100%
oracle-grounded `test_pass_rate`. Git-history retrieval edged
`avg_rounds_to_oracle_pass` down from 1.4 to 1.2. The dominant signal
was that **the reviewer is chronically over-asking**. 7 of 15 non-empty
reviewer rounds rejected a diff the oracle had already passed. Full
breakdown in [docs/RESULTS.md](docs/RESULTS.md).
