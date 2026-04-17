"""
loop.py — Orchestrator: runs coder→reviewer loop per issue.

All issues are evaluated against a single pinned baseline commit
(data/issues.json → baseline_commit) that predates ALL 25 fixes.
This ensures a consistent, reproducible eval environment.

Usage:
    python -m harness.loop --issue 1015
    python -m harness.loop --all
    python -m harness.loop --all --max-rounds 3
"""
import argparse
import json
import os
import subprocess
import sys
import time

try:
    from harness.coder import run_coder
    from harness.reviewer import run_reviewer
    from harness.oracle import run as run_oracle
    from harness.memory import CoderMemory, ReviewerMemory, categorize
    from harness.distill import update_from_trace
except ImportError:
    from coder import run_coder  # type: ignore
    from reviewer import run_reviewer  # type: ignore
    from oracle import run as run_oracle  # type: ignore
    from memory import CoderMemory, ReviewerMemory, categorize  # type: ignore
    from distill import update_from_trace  # type: ignore

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "issues.json")
TRACES_DIR = os.path.join(os.path.dirname(__file__), "..", "traces")
REPO = os.environ.get("TARGET_REPO_PATH", "./arrow")


def load_data() -> dict:
    with open(DATA_FILE) as f:
        return json.load(f)


def git_checkout_baseline(repo: str, baseline: str):
    """Detach HEAD at the pinned baseline commit."""
    result = subprocess.run(
        ["git", "checkout", baseline],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not checkout baseline {baseline}: {result.stderr.strip()}")
    print(f"  Pinned to baseline {baseline[:8]}")


def git_reset_to_baseline(repo: str):
    """Discard any uncommitted changes (keeps the checked-out commit)."""
    subprocess.run(["git", "checkout", "."], cwd=repo, check=True, capture_output=True)


def run_issue(
    issue: dict,
    baseline: str,
    max_rounds: int = 5,
    coder_memory: "CoderMemory | None" = None,
    reviewer_memory: "ReviewerMemory | None" = None,
) -> dict:
    """
    Run the coder→reviewer loop for a single issue against the pinned baseline.
    Returns trace dict.

    If coder_memory / reviewer_memory are provided, render their current
    contents into the agents' system prompts. The same memory blocks are
    used for every round of this issue (we do not refresh mid-issue);
    distillation happens only after the loop completes.
    """
    repo_abs = os.path.abspath(REPO)

    trace = {
        "issue_number": issue["number"],
        "issue_title": issue["title"],
        "baseline": baseline,
        "rounds": 0,
        "approved": False,
        "oracle_passed_final": False,
        "first_pass_oracle": False,
        "comments_per_round": [],
    }

    git_checkout_baseline(repo_abs, baseline)

    extra_context = ""

    # Render memory blocks once per issue. Same blocks used in every round.
    coder_block = ""
    coder_lesson_ids: list[str] = []
    if coder_memory is not None:
        category = categorize(issue)
        coder_block, coder_lesson_ids = coder_memory.render_for(category)
        if coder_block:
            print(
                f"  Coder memory: injecting {len(coder_lesson_ids)} lesson(s) "
                f"(category={category})",
                flush=True,
            )

    reviewer_block = ""
    reviewer_case_ids: list[str] = []
    if reviewer_memory is not None:
        reviewer_block, reviewer_case_ids = reviewer_memory.render()
        if reviewer_block:
            print(
                f"  Reviewer memory: injecting {len(reviewer_case_ids)} calibration case(s)",
                flush=True,
            )

    trace["coder_lesson_ids"] = coder_lesson_ids
    trace["reviewer_case_ids"] = reviewer_case_ids

    issue_start = time.time()

    for round_num in range(1, max_rounds + 1):
        round_start = time.time()
        print(f"  [Round {round_num}] Running coder...", flush=True)

        # Only reset before round 1. On subsequent rounds, keep the working
        # tree so the coder can iterate on its previous changes.
        if round_num == 1:
            git_reset_to_baseline(repo_abs)

        diff = run_coder(issue, extra_context=extra_context, memory_block=coder_block)

        # Oracle runs against the patched working tree BEFORE the reviewer.
        # Its result is recorded in the trace but never passed to the
        # reviewer prompt (DESIGN.md sec 5.1 / 7.2 information-flow contract).
        print(f"  [Round {round_num}] Running oracle...", flush=True)
        oracle_result = run_oracle(issue, repo=repo_abs)
        oracle_dict = oracle_result.to_dict()
        print(
            f"  [Round {round_num}] Oracle: passed={oracle_result.passed} "
            f"({oracle_result.n_passed}/{oracle_result.n_tests}, "
            f"{oracle_result.elapsed_s:.2f}s)",
            flush=True,
        )

        print(f"  [Round {round_num}] Running reviewer... ({len(diff)} chars in diff)", flush=True)
        review = run_reviewer(issue, diff, memory_block=reviewer_block)

        trace["rounds"] = round_num
        trace["comments_per_round"].append({
            "round": round_num,
            "diff_length": len(diff),
            "diff": diff[:4000],  # truncated for trace storage
            "approved": review["approved"],
            "comments": review["comments"],
            "oracle": oracle_dict,
        })

        # Track first-round oracle outcome separately - this is the cleanest
        # measure of coder quality without reviewer contamination.
        if round_num == 1:
            trace["first_pass_oracle"] = oracle_result.passed
        # The oracle status of the *final* round we ran is what counts
        # for the headline test_pass_rate metric.
        trace["oracle_passed_final"] = oracle_result.passed

        elapsed = time.time() - round_start
        print(f"  [Round {round_num}] Approved: {review['approved']} ({elapsed:.0f}s)", flush=True)
        for c in review["comments"]:
            print(f"    - {c}", flush=True)

        # Loop continuation is decided ONLY by the reviewer, not the oracle.
        # Reviewer-approved + oracle-failed and reviewer-rejected + oracle-passed
        # are valuable training signals we want to surface, not short-circuit.
        if review["approved"]:
            trace["approved"] = True
            break

        # Build context for next round: reviewer comments + what the coder
        # already changed, so it can iterate rather than start over.
        # Oracle output is NOT included here.
        feedback_lines = "\n".join(f"- {c}" for c in review["comments"])
        extra_context = (
            "Your current changes (DO NOT start over, iterate on these):\n"
            f"```diff\n{diff[:3000]}\n```\n\n"
            f"Reviewer feedback to address:\n{feedback_lines}"
        )

    # Restore to master when done with this issue
    subprocess.run(["git", "checkout", "master"], cwd=repo_abs, capture_output=True)

    total_elapsed = time.time() - issue_start
    print(f"  Total time for issue #{issue['number']}: {total_elapsed:.0f}s", flush=True)

    return trace


def save_trace(trace: dict):
    os.makedirs(TRACES_DIR, exist_ok=True)
    path = os.path.join(TRACES_DIR, f"issue_{trace['issue_number']}.json")
    with open(path, "w") as f:
        json.dump(trace, f, indent=2)
    print(f"  Trace saved: {path}")


def main():
    parser = argparse.ArgumentParser(description="Run worktrial loop")
    parser.add_argument("--issue", type=int, help="Run a single issue by number")
    parser.add_argument("--all", action="store_true", help="Run all issues")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="Disable memory injection AND distillation. Use for the no-distillation baseline arm.",
    )
    args = parser.parse_args()

    data = load_data()
    issues = data["issues"]
    baseline = data.get("baseline_commit")

    if not baseline:
        print("ERROR: data/issues.json missing 'baseline_commit'. Add it first.")
        sys.exit(1)

    print(f"Baseline: {baseline} — {data.get('baseline_note', '')}")

    if args.issue:
        matching = [i for i in issues if i["number"] == args.issue]
        if not matching:
            print(f"Issue {args.issue} not found in data/issues.json")
            sys.exit(1)
        issues = matching
    elif not args.all:
        parser.print_help()
        sys.exit(1)

    coder_memory: "CoderMemory | None" = None
    reviewer_memory: "ReviewerMemory | None" = None
    if args.ablate:
        print("ABLATION MODE: memory injection and distillation disabled.")
    else:
        coder_memory = CoderMemory()
        reviewer_memory = ReviewerMemory()
        print(
            f"Memory active: coder={len(coder_memory.all())} lessons, "
            f"reviewer={len(reviewer_memory.all())} cases"
        )

    for stream_index, issue in enumerate(issues, start=1):
        print(f"\n=== Issue #{issue['number']}: {issue['title']} ===")
        trace = run_issue(
            issue,
            baseline=baseline,
            max_rounds=args.max_rounds,
            coder_memory=coder_memory,
            reviewer_memory=reviewer_memory,
        )
        save_trace(trace)
        print(f"  Done: {trace['rounds']} rounds, approved={trace['approved']}, "
              f"oracle_passed_final={trace['oracle_passed_final']}")

        if not args.ablate:
            # Guardrails (alternating schedule, held-out exclusion) are
            # added in the next commit; for now we update both memories
            # every issue. Distillation is best-effort - failures don't
            # abort the run.
            try:
                update_from_trace(
                    trace,
                    issue,
                    coder_memory=coder_memory,
                    reviewer_memory=reviewer_memory,
                    schedule={"update_coder": True, "update_reviewer": True},
                )
            except Exception as e:  # noqa: BLE001
                print(f"  WARNING: distillation failed: {e!r}")


if __name__ == "__main__":
    main()
