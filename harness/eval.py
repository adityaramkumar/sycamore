"""
eval.py — Compute metrics from trace files.

Usage:
    python harness/eval.py ./traces
"""
import json
import os
import sys


def compute_metrics(traces_dir: str) -> dict:
    """
    Read all trace JSONs and compute aggregate metrics.
    Returns:
        {
            avg_rounds: float,
            approval_rate: float,        # % approved at all
            first_pass_rate: float,      # % approved on round 1
            comment_addressal_rate: float,  # % of multi-round issues that improved
            per_issue: [...]
        }
    """
    traces = []
    for fname in sorted(os.listdir(traces_dir)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(traces_dir, fname)) as f:
            traces.append(json.load(f))

    if not traces:
        return {"error": "No traces found"}

    total = len(traces)
    approved = sum(1 for t in traces if t.get("approved"))
    first_pass = sum(1 for t in traces if t.get("approved") and t.get("rounds") == 1)
    total_rounds = sum(t.get("rounds", 0) for t in traces)

    # Comment addressal: in multi-round issues, did the coder produce a longer/different diff?
    multi_round = [t for t in traces if t.get("rounds", 1) > 1]
    addressal_count = 0
    for t in multi_round:
        rounds_data = t.get("comments_per_round", [])
        if len(rounds_data) >= 2:
            r1_len = rounds_data[0].get("diff_length", 0)
            r2_len = rounds_data[1].get("diff_length", 0)
            if r2_len != r1_len:
                addressal_count += 1

    comment_addressal_rate = (addressal_count / len(multi_round)) if multi_round else 1.0

    return {
        "total_issues": total,
        "avg_rounds": round(total_rounds / total, 2),
        "approval_rate": round(approved / total, 3),
        "first_pass_rate": round(first_pass / total, 3),
        "comment_addressal_rate": round(comment_addressal_rate, 3),
        "per_issue": [
            {
                "number": t.get("issue_number"),
                "title": t.get("issue_title", ""),
                "rounds": t.get("rounds"),
                "approved": t.get("approved"),
            }
            for t in traces
        ],
    }


def print_table(metrics: dict):
    print("\n=== Eval Metrics ===")
    print(f"Total issues:           {metrics.get('total_issues')}")
    print(f"Avg rounds:             {metrics.get('avg_rounds')}")
    print(f"Approval rate:          {metrics.get('approval_rate', 0) * 100:.1f}%")
    print(f"First-pass rate:        {metrics.get('first_pass_rate', 0) * 100:.1f}%")
    print(f"Comment addressal rate: {metrics.get('comment_addressal_rate', 0) * 100:.1f}%")

    print("\n--- Per-issue ---")
    print(f"{'#':<8} {'Rounds':<8} {'Approved':<10} Title")
    print("-" * 60)
    for issue in metrics.get("per_issue", []):
        approved_str = "✓" if issue["approved"] else "✗"
        title = issue["title"][:40] if issue.get("title") else ""
        print(f"{issue['number']:<8} {issue['rounds']:<8} {approved_str:<10} {title}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python harness/eval.py <traces_dir>")
        sys.exit(1)

    traces_dir = sys.argv[1]
    if not os.path.exists(traces_dir):
        print(f"Traces directory not found: {traces_dir}")
        print("Run 'python harness/loop.py --all' first to generate traces.")
        sys.exit(0)
    if not os.path.isdir(traces_dir):
        print(f"Not a directory: {traces_dir}")
        sys.exit(1)

    metrics = compute_metrics(traces_dir)

    if "error" in metrics:
        print(f"Error: {metrics['error']}")
        sys.exit(1)

    print_table(metrics)

    # Also dump full JSON
    print("\n--- Full JSON ---")
    print(json.dumps(metrics, indent=2))
