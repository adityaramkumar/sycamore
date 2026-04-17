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
    from harness.history import (
        render_block as render_history_block,
        retrieve_similar_fixes,
        retrieved_sha_list,
    )
    from harness.scheduler import (
        DEFAULT_HELDOUT_SIZE,
        DEFAULT_SEED,
        make_split,
        reviewer_audit,
        schedule_for,
    )
except ImportError:
    from coder import run_coder  # type: ignore
    from reviewer import run_reviewer  # type: ignore
    from oracle import run as run_oracle  # type: ignore
    from memory import CoderMemory, ReviewerMemory, categorize  # type: ignore
    from distill import update_from_trace  # type: ignore
    from history import (  # type: ignore
        render_block as render_history_block,
        retrieve_similar_fixes,
        retrieved_sha_list,
    )
    from scheduler import (  # type: ignore
        DEFAULT_HELDOUT_SIZE,
        DEFAULT_SEED,
        make_split,
        reviewer_audit,
        schedule_for,
    )

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "issues.json")
TRACES_DIR = os.path.join(os.path.dirname(__file__), "..", "traces")
REPO = os.environ.get("TARGET_REPO_PATH", "./arrow")


def load_data() -> dict:
    with open(DATA_FILE) as f:
        return json.load(f)


def git_checkout_baseline(repo: str, baseline: str):
    """Detach HEAD at the pinned baseline commit.

    Discards any working-tree changes first so a dirty tree from a
    previous issue (or a misbehaving coder) cannot block the checkout.
    """
    subprocess.run(["git", "checkout", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=repo, capture_output=True)
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
    subprocess.run(["git", "clean", "-fd"], cwd=repo, capture_output=True)


def git_head_sha(repo: str) -> str:
    """Return the current HEAD commit SHA (full)."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    return result.stdout.strip()


def assert_at_baseline(repo: str, baseline: str) -> tuple[bool, str]:
    """Check whether HEAD is at the baseline commit. Returns (ok, current_sha).

    The baseline may be a short SHA; we compare prefix-to-prefix.
    """
    current = git_head_sha(repo)
    return current.startswith(baseline) or baseline.startswith(current), current


def run_issue(
    issue: dict,
    baseline: str,
    max_rounds: int = 5,
    coder_memory: "CoderMemory | None" = None,
    reviewer_memory: "ReviewerMemory | None" = None,
    forbidden_shas: "set[str] | None" = None,
    use_history: bool = True,
) -> dict:
    """
    Run the coder→reviewer loop for a single issue against the pinned baseline.
    Returns trace dict.

    If coder_memory / reviewer_memory are provided, render their current
    contents into the agents' system prompts. The same memory blocks are
    used for every round of this issue (we do not refresh mid-issue);
    distillation happens only after the loop completes.

    When use_history is True, we also retrieve up to 3 similar pre-baseline
    commits from git log and inject them as a concrete-examples block in
    the coder prompt. forbidden_shas (typically the 25 eval fix_commits)
    are never returned as defense-in-depth against data leakage.
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

    # Git-history retrieval. Baseline-scoped with fix-SHA blacklist so
    # eval leakage is impossible by construction.
    history_block = ""
    history_sha_ids: list[str] = []
    if use_history:
        try:
            commits = retrieve_similar_fixes(
                issue, repo_abs, baseline, forbidden_shas=forbidden_shas
            )
            history_block = render_history_block(commits)
            history_sha_ids = retrieved_sha_list(commits)
            if history_block:
                print(
                    f"  History: injecting {len(history_sha_ids)} commit(s) "
                    f"from pre-baseline git log",
                    flush=True,
                )
        except Exception as e:  # noqa: BLE001 - history is best-effort
            print(f"  WARNING: history retrieval failed: {e!r}", flush=True)

    trace["coder_lesson_ids"] = coder_lesson_ids
    trace["reviewer_case_ids"] = reviewer_case_ids
    trace["history_sha_ids"] = history_sha_ids

    issue_start = time.time()

    for round_num in range(1, max_rounds + 1):
        round_start = time.time()
        print(f"  [Round {round_num}] Running coder...", flush=True)

        # Only reset before round 1. On subsequent rounds, keep the working
        # tree so the coder can iterate on its previous changes.
        if round_num == 1:
            git_reset_to_baseline(repo_abs)

        diff = run_coder(
            issue,
            extra_context=extra_context,
            memory_block=coder_block,
            history_block=history_block,
        )

        # The coder has Bash and could have moved HEAD off the baseline
        # (e.g. running `git checkout master`). If it did, the diff is
        # against the wrong base and the oracle would test a different
        # source tree. Detect, force-reset, and mark this round void.
        head_ok, current_sha = assert_at_baseline(repo_abs, baseline)
        if not head_ok:
            print(
                f"  [Round {round_num}] WARNING: HEAD drifted to {current_sha[:8]} "
                f"(expected {baseline[:8]}). Force-resetting and voiding this round.",
                flush=True,
            )
            git_checkout_baseline(repo_abs, baseline)
            void_oracle = {
                "passed": False,
                "targeted_files": [],
                "n_tests": 0,
                "n_passed": 0,
                "n_failed": 0,
                "failing_tests": [],
                "elapsed_s": 0.0,
                "timed_out": False,
                "error": f"void: HEAD drifted to {current_sha[:8]}",
            }
            trace["rounds"] = round_num
            trace["comments_per_round"].append({
                "round": round_num,
                "diff_length": 0,
                "diff": "",
                "approved": False,
                "comments": [f"VOID: coder moved HEAD off baseline ({current_sha[:8]})"],
                "oracle": void_oracle,
            })
            if round_num == 1:
                trace["first_pass_oracle"] = False
            trace["oracle_passed_final"] = False
            extra_context = "Your previous round moved HEAD off the baseline commit. Do NOT run git checkout, git reset, or any command that changes HEAD."
            continue

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
    parser.add_argument(
        "--issues",
        type=str,
        help="Comma-separated issue numbers to run (a subset of all). "
             "Useful for small ablations where alternating-update parity matters.",
    )
    parser.add_argument("--all", action="store_true", help="Run all issues")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="Disable memory injection AND distillation. Use for the no-distillation baseline arm.",
    )
    parser.add_argument(
        "--heldout-size",
        type=int,
        default=DEFAULT_HELDOUT_SIZE,
        help=f"Number of issues to reserve as held-out eval (default {DEFAULT_HELDOUT_SIZE}). "
             "Held-out issues are still RUN but contribute no distillation.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Seed for the deterministic train/held-out split (default {DEFAULT_SEED}).",
    )
    parser.add_argument(
        "--no-heldout",
        action="store_true",
        help="Treat every issue as training. Useful for --issue mode and small ablations.",
    )
    parser.add_argument(
        "--audit-window",
        type=int,
        default=6,
        help="How many recent training-stream traces to use for the reviewer audit (default 6).",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Disable git-history retrieval. Use for the no-history ablation arm.",
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
    elif args.issues:
        try:
            targets = {int(x.strip()) for x in args.issues.split(",") if x.strip()}
        except ValueError:
            print(f"--issues must be a comma-separated list of integers, got {args.issues!r}")
            sys.exit(1)
        matching = [i for i in issues if i["number"] in targets]
        if not matching:
            print(f"None of {sorted(targets)} found in data/issues.json")
            sys.exit(1)
        # Preserve the user-supplied order so parity scheduling is predictable.
        order = {n: idx for idx, n in enumerate(int(x.strip()) for x in args.issues.split(",") if x.strip())}
        matching.sort(key=lambda i: order.get(i["number"], 0))
        issues = matching
    elif not args.all:
        parser.print_help()
        sys.exit(1)

    # Held-out split (DESIGN.md sec 5 / 6.3). Subset and single-issue
    # runs disable the split entirely so debugging stays simple.
    held_out_set: set[int] = set()
    if args.all and not args.issues and not args.no_heldout:
        all_numbers = [i["number"] for i in data["issues"]]
        train_nums, held_nums = make_split(
            all_numbers, heldout_size=args.heldout_size, seed=args.seed
        )
        held_out_set = set(held_nums)
        print(
            f"Split (seed={args.seed}): training={len(train_nums)} issues, "
            f"held-out={len(held_nums)} issues -> {sorted(held_nums)}"
        )

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

    use_history = not args.no_history
    print(f"Git-history retrieval: {'ON' if use_history else 'OFF'}")

    # Defense-in-depth: every eval issue's fix_commit is blacklisted from
    # history retrieval, independent of baseline-scope filtering.
    forbidden_shas: set[str] = {i["fix_commit"] for i in data["issues"]}

    reviewer_frozen = False
    recent_training_traces: list[dict] = []
    training_index = 0  # 1-based index into the training stream for parity scheduling

    for issue in issues:
        held_out = issue["number"] in held_out_set
        if not held_out:
            training_index += 1
        print(f"\n=== Issue #{issue['number']}: {issue['title']} "
              f"({'HELD-OUT' if held_out else f'training #{training_index}'}) ===")

        trace = run_issue(
            issue,
            baseline=baseline,
            max_rounds=args.max_rounds,
            coder_memory=coder_memory,
            reviewer_memory=reviewer_memory,
            forbidden_shas=forbidden_shas,
            use_history=use_history,
        )
        trace["held_out"] = held_out
        trace["training_stream_index"] = None if held_out else training_index
        trace["reviewer_frozen_before"] = reviewer_frozen
        sched = schedule_for(
            held_out=held_out,
            stream_index=training_index,
            reviewer_frozen=reviewer_frozen,
        )
        trace["scheduled_updates"] = sched
        save_trace(trace)
        print(f"  Done: {trace['rounds']} rounds, approved={trace['approved']}, "
              f"oracle_passed_final={trace['oracle_passed_final']}, "
              f"updates={sched}")

        # Distillation is gated by the schedule. Held-out issues update
        # nothing; training issues alternate parity; reviewer freeze
        # disables reviewer updates only.
        if not args.ablate and (sched["update_coder"] or sched["update_reviewer"]):
            try:
                update_from_trace(
                    trace,
                    issue,
                    coder_memory=coder_memory,
                    reviewer_memory=reviewer_memory,
                    schedule=sched,
                )
            except Exception as e:  # noqa: BLE001
                print(f"  WARNING: distillation failed: {e!r}")

        # Reviewer audit (DESIGN.md sec 5.2). Runs only on training
        # traces, over a sliding window. Once frozen, stays frozen
        # (recovery is future work).
        if not args.ablate and not held_out:
            recent_training_traces.append(trace)
            window = recent_training_traces[-args.audit_window:]
            health = reviewer_audit(window)
            if health.frozen and not reviewer_frozen:
                print(
                    f"  REVIEWER FROZEN after issue #{issue['number']} "
                    f"(samples={health.samples}, precision={health.precision}, "
                    f"approval={health.approval_rate_per_round}, "
                    f"gap={health.balance_gap}); reasons={health.reasons}"
                )
                reviewer_frozen = True


if __name__ == "__main__":
    main()
