# Co-Optimizing AI Coding and Review Agents

Today, AI coding agents (e.g., Claude Code, Codex, Copilot Workspace) and AI code review agents (e.g., Bugbot, Greptile, Qodo) are built and improved independently. A coding agent generates PRs; a review agent critiques them. But neither learns from the other. This is a missed opportunity -- their interaction traces contain a rich signal for improving both.

## The Setup

You have access to the following (simulated or real, your choice):

- **Repository Backlog:** A repository with a backlog of GitHub issues (bugs, features, refactors).
- **Coding Agent:** An agent that takes an issue and produces a PR (a diff + description). Think of this as a black-box LLM you can prompt-engineer/add skills/change architecture or tools.
- **Review Agent:** An agent that takes a PR and produces structured review comments (approve, request changes, specific line-level feedback). Also a black-box LLM.
- **Feedback Loop:** The coding agent can revise the PR based on review comments. This back-and-forth continues until the reviewer approves or a maximum number of rounds is reached.

Over N issues, this process produces a dataset of interaction traces:

```
Trace = {
  issue,
  [(pr_attempt_1, review_1), (pr_attempt_2, review_2), ...],
}
```

## The Problem

Design and prototype a system that uses these interaction traces to improve both agents over time using some sort of self-play.

Concretely, address the following parts:

---

## Part 1: System Design (1-2 hrs)

### 1. Data Extraction

What signals can you extract from the interaction traces? Which are reliable enough to train on? Think about:

- Reviewer accuracy (did comments lead to better code?)
- Coder responsiveness (did it incorporate feedback?)
- Review-revision alignment
- Outcome correlation

### 2. Improvement Mechanism

How do you use these signals to update each agent? Options include (but are not limited to):

- Prompt engineering with curated examples
- Distilling patterns from successful traces into guidelines
- Something else entirely

### 3. Co-optimization Stability

How do you prevent pathological dynamics? Specifically:

- **Reward hacking:** The coder learns to satisfy the reviewer without improving actual code quality (gaming the critic).
- **Reviewer collapse:** The reviewer becomes trivially lenient or adversarially strict.
- **Mode collapse:** Both converge to a narrow pattern that looks good locally but is fragile.
- **Distributional shift:** As the coder improves, the reviewer sees a different distribution of code than it was calibrated for.

### 4. Ground Truth Anchoring

How do you keep the system grounded when the sparse ground truth (tests, human labels, prod bugs) is infrequent? How do you avoid both agents drifting away from reality while they co-optimize against each other?

---

## Part 2: Prototype (3-4 hrs)

Build a working prototype that demonstrates the core loop.

Your prototype should:

- Implement the interaction loop (coder generates, reviewer reviews, coder revises)
- Collect and structure the interaction traces
- Implement at least one concrete improvement mechanism for each agent
- Show measurable improvement over multiple rounds on a metric you define
- Include at least one mechanism to prevent the pathological dynamics from Part 1

---

## Part 3: Evaluation & Analysis (30 min)

- What metrics you chose and why
- Results from your prototype: did both agents improve? Did one improve at the expense of the other?
- What failure modes did you observe (or deliberately prevent)?
- What would you do differently with 2 more weeks? What about 2 more months?

---

## Interesting Initial Directions

- Notice they only have to co-optimize for only one repo
- Both the coder and reviewer can have access to:
  - Git history
  - Traces of each other

---

## Time Budget

This is a full-day worktrial (~8-9 hours). Suggested breakdown:

| Phase | Time | Description |
|-------|------|-------------|
| Part 1 | 1-2 hrs | System design document |
| Part 2 | 3-4 hrs | Working prototype |
| Part 3 | 30 min | Evaluation and analysis |
| Buffer | ~2 hrs | Iteration, cleanup, writeup |

---

## What's Provided

This repo includes a starter harness with a working coder-reviewer loop, 25 curated bugs from [arrow-py/arrow](https://github.com/arrow-py/arrow), and basic eval metrics. See the [README](README.md) for setup and usage.

**Everything here is guidance, not constraint.** The starter harness, target repo, issue set, agent architecture, and eval metrics are provided to save you setup time -- not to limit your approach. Feel free to change anything: swap the target repo, curate different issues, rewrite the agents from scratch, use a different framework, define your own metrics. Use whatever you're most comfortable with to demonstrate your thinking on the core problem.
