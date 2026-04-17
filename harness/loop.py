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
except ImportError:
    from coder import run_coder  # type: ignore
    from reviewer import run_reviewer  # type: ignore

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


def run_issue(issue: dict, baseline: str, max_rounds: int = 5) -> dict:
    """
    Run the coder→reviewer loop for a single issue against the pinned baseline.
    Returns trace dict.
    """
    repo_abs = os.path.abspath(REPO)

    trace = {
        "issue_number": issue["number"],
        "issue_title": issue["title"],
        "baseline": baseline,
        "rounds": 0,
        "approved": False,
        "comments_per_round": [],
    }

    git_checkout_baseline(repo_abs, baseline)

    extra_context = ""
    prev_diff = ""

    issue_start = time.time()

    for round_num in range(1, max_rounds + 1):
        round_start = time.time()
        print(f"  [Round {round_num}] Running coder...", flush=True)

        # Only reset before round 1. On subsequent rounds, keep the working
        # tree so the coder can iterate on its previous changes.
        if round_num == 1:
            git_reset_to_baseline(repo_abs)

        diff = run_coder(issue, extra_context=extra_context)

        print(f"  [Round {round_num}] Running reviewer... ({len(diff)} chars in diff)", flush=True)
        review = run_reviewer(issue, diff)

        trace["rounds"] = round_num
        trace["comments_per_round"].append({
            "round": round_num,
            "diff_length": len(diff),
            "diff": diff[:4000],  # truncated for trace storage
            "approved": review["approved"],
            "comments": review["comments"],
        })

        elapsed = time.time() - round_start
        print(f"  [Round {round_num}] Approved: {review['approved']} ({elapsed:.0f}s)", flush=True)
        for c in review["comments"]:
            print(f"    - {c}", flush=True)

        if review["approved"]:
            trace["approved"] = True
            break

        # Build context for next round: reviewer comments + what the coder
        # already changed, so it can iterate rather than start over.
        feedback_lines = "\n".join(f"- {c}" for c in review["comments"])
        extra_context = f"Your current changes (DO NOT start over, iterate on these):\n```diff\n{diff[:3000]}\n```\n\nReviewer feedback to address:\n{feedback_lines}"
        prev_diff = diff

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

    for issue in issues:
        print(f"\n=== Issue #{issue['number']}: {issue['title']} ===")
        trace = run_issue(issue, baseline=baseline, max_rounds=args.max_rounds)
        save_trace(trace)
        print(f"  Done: {trace['rounds']} rounds, approved={trace['approved']}")


if __name__ == "__main__":
    main()
