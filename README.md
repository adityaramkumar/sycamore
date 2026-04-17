# Co-Optimizing AI Coding and Review Agents

A worktrial repo for designing and prototyping a system that co-optimizes AI coding and review agents through self-play on real historical bugs.

**Problem statement:** [PROBLEM.md](PROBLEM.md)

## What's Provided

### Target Repo

[arrow-py/arrow](https://github.com/arrow-py/arrow) -- a small Python date/time library (~2200 LOC). We've curated 25 confirmed historical bugs (humanize, locale, and parser issues) as the issue backlog. All issues are pinned to a single baseline commit (`c9cecaf`) that predates every fix.

### Starter Harness

A minimal but functional coder-reviewer loop in `harness/`:

| File | Purpose |
|------|---------|
| `coder.py` | Agentic coder (Claude + Read/Write/Edit/Bash tools) |
| `reviewer.py` | Reviewer agent (approves or requests changes with structured comments) |
| `loop.py` | Orchestration: reset repo -> code -> review -> repeat until approved or max rounds |
| `eval.py` | Compute metrics from trace files |

The harness is intentionally minimal. You can (and should) modify everything -- prompts, tools, loop logic, trace format, eval metrics, architecture.

### Data

`data/issues.json` contains the 25 issues with metadata:
- `number`, `title`, `body_summary` -- issue context for the coder
- `fix_commit` -- ground truth SHA (for evaluation only, do NOT pass to the coder)
- `files_changed` -- which files were modified in the real fix

Bug categories:
- `humanize()` boundary/rounding bugs (6)
- Missing locale timeframes -- week/quarter (7)
- Locale pluralization errors (3)
- Date parsing edge cases (6)
- DST, range, escaping bugs (3)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Clone the target repo
git clone https://github.com/arrow-py/arrow ./arrow

# Run a single issue to see the loop in action
python -m harness.loop --issue 1015

# Run all 25 issues
python -m harness.loop --all

# View metrics from traces
python harness/eval.py ./traces
```

## Auth

The harness uses the system `claude` CLI, authenticated via Claude Max. No API key needed. Set `CLI_PATH` env var to override the binary path.

## Structure

```
worktrial-base/
├── harness/
│   ├── coder.py       # Agentic coder
│   ├── reviewer.py    # Reviewer agent
│   ├── loop.py        # Orchestration loop
│   └── eval.py        # Metrics computation
├── data/
│   └── issues.json    # 25 curated bugs with ground truth
├── traces/            # Per-issue JSON traces (created on first run)
├── PROBLEM.md         # Full problem statement
├── .env.example       # Environment variable template
└── requirements.txt
```

## How It Works

For each issue:

1. **Reset** `arrow-py/arrow` to the pinned baseline commit
2. **Coder** reads the issue, explores code, writes a fix, runs tests, submits
3. **Reviewer** reads the diff, either approves or returns structured comments
4. If not approved, reviewer comments are passed back to the coder for another round
5. **Trace** saved to `traces/issue_<N>.json`

This produces the interaction traces described in the problem statement:

```
Trace = {
  issue,
  [(pr_attempt_1, review_1), (pr_attempt_2, review_2), ...],
}
```

## Starter Metrics

`harness/eval.py` computes these from trace files:

| Metric | Description |
|--------|-------------|
| `avg_rounds` | Average coder-reviewer rounds per issue (lower = better) |
| `approval_rate` | Fraction of issues eventually approved |
| `first_pass_rate` | Fraction approved on the first attempt |
| `comment_addressal_rate` | In multi-round issues, did the coder change its approach? |

These are a starting point. You should define additional metrics relevant to co-optimization.

---

## Admin: Candidate Setup

This repo is a [template repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-template-repository). To set up a candidate for the worktrial:

```bash
# Create a private repo for the candidate from the template
gh api repos/sycamore-labs/worktrial-agents-base/generate \
  -f owner=sycamore-labs \
  -f name=worktrial-CANDIDATE_NAME \
  -f private=true

# Invite the candidate as a collaborator
gh api repos/sycamore-labs/worktrial-CANDIDATE_NAME/collaborators/GITHUB_USERNAME \
  -X PUT -f permission=push

# After the worktrial, review their repo then clean up
gh repo delete sycamore-labs/worktrial-CANDIDATE_NAME --yes
```
