"""
metrics.py: Compute eval metrics from a corpus of trace files.

The headline metric is `test_pass_rate` (oracle-grounded). All others
either decompose it per-agent (first-pass, rounds-to-pass, reviewer
precision/recall) or detect pathological dynamics (reward-hacking
gap, approval saturation alerts) per DESIGN.md section 8.

Backward compatibility: traces written before the oracle was wired
in (no `oracle` key per round) are treated as oracle-failed and
flagged via per_round_oracle being uniformly False. This is a
deliberate choice; pre-oracle approvals are not ground-truth-
verified and should not inflate test_pass_rate.

Reviewer 2x2 confusion table is computed per-round, not per-issue,
so a multi-round trace contributes multiple samples. This matches
the way distillation will sample reviewer calibration cases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Iterable


@dataclass
class IssueOutcome:
    """A flattened, metrics-friendly view of one trace file."""
    issue_number: int
    title: str
    rounds: int
    approved: bool                       # reviewer's terminal verdict
    oracle_passed_final: bool            # ground truth on the last round we ran
    first_pass_oracle: bool              # ground truth on round 1 alone
    first_pass_approved: bool
    rounds_to_oracle_pass: int | None    # smallest round where oracle passed
    per_round_oracle: list[bool] = field(default_factory=list)
    per_round_approved: list[bool] = field(default_factory=list)
    diff_lengths: list[int] = field(default_factory=list)
    n_comments: list[int] = field(default_factory=list)
    held_out: bool = False               # set externally if a split is provided


def _flatten(t: dict) -> IssueOutcome:
    rounds = t.get("comments_per_round", []) or []
    per_round_oracle: list[bool] = []
    per_round_approved: list[bool] = []
    diff_lengths: list[int] = []
    n_comments: list[int] = []
    rounds_to_oracle_pass: int | None = None

    for r in rounds:
        oracle = r.get("oracle") or {}
        passed = bool(oracle.get("passed", False))
        per_round_oracle.append(passed)
        per_round_approved.append(bool(r.get("approved", False)))
        diff_lengths.append(int(r.get("diff_length", 0)))
        n_comments.append(len(r.get("comments", []) or []))
        if rounds_to_oracle_pass is None and passed:
            rounds_to_oracle_pass = int(r.get("round", len(per_round_oracle)))

    last_oracle = per_round_oracle[-1] if per_round_oracle else False
    first_oracle = per_round_oracle[0] if per_round_oracle else False
    first_approved = per_round_approved[0] if per_round_approved else False

    return IssueOutcome(
        issue_number=int(t.get("issue_number", -1)),
        title=t.get("issue_title", ""),
        rounds=int(t.get("rounds", len(rounds))),
        approved=bool(t.get("approved", per_round_approved[-1] if per_round_approved else False)),
        oracle_passed_final=bool(t.get("oracle_passed_final", last_oracle)),
        first_pass_oracle=bool(t.get("first_pass_oracle", first_oracle)),
        first_pass_approved=first_approved,
        rounds_to_oracle_pass=rounds_to_oracle_pass,
        per_round_oracle=per_round_oracle,
        per_round_approved=per_round_approved,
        diff_lengths=diff_lengths,
        n_comments=n_comments,
    )


def load_traces(traces_dir: str) -> list[IssueOutcome]:
    """Read every issue_*.json in `traces_dir`, return as IssueOutcomes."""
    out: list[IssueOutcome] = []
    if not os.path.isdir(traces_dir):
        return out
    for fname in sorted(os.listdir(traces_dir)):
        if not fname.startswith("issue_") or not fname.endswith(".json"):
            continue
        path = os.path.join(traces_dir, fname)
        try:
            with open(path) as f:
                t = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        out.append(_flatten(t))
    return out


def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def _reviewer_confusion(traces: Iterable[IssueOutcome]) -> dict[str, int]:
    """Per-round 2x2 reviewer-vs-oracle table.

    Mapping (DESIGN.md sec 4.2 win/loss table):
      true_approval   reviewer approved   AND oracle passed   (WIN)
      true_rejection  reviewer rejected   AND oracle failed   (WIN)
      false_approval  reviewer approved   AND oracle failed   (LOSS - missed bug)
      false_rejection reviewer rejected   AND oracle passed   (LOSS - over-asked)

    Empty-diff rounds (coder produced no changes) are EXCLUDED. The
    oracle trivially passes on a clean baseline tree, and the
    reviewer trivially rejects "no changes were made". Counting this
    as a false rejection would falsely paint the reviewer as
    over-asking when in fact it correctly rejected an empty submission.
    """
    cm = {"true_approval": 0, "true_rejection": 0, "false_approval": 0, "false_rejection": 0}
    for t in traces:
        for approved, oracle_passed, diff_len in zip(
            t.per_round_approved, t.per_round_oracle, t.diff_lengths
        ):
            if diff_len <= 0:
                continue
            if approved and oracle_passed:
                cm["true_approval"] += 1
            elif approved and not oracle_passed:
                cm["false_approval"] += 1
            elif not approved and not oracle_passed:
                cm["true_rejection"] += 1
            else:
                cm["false_rejection"] += 1
    return cm


def _balance_alerts(approval_rate: float, gap: float) -> list[str]:
    alerts: list[str] = []
    if approval_rate >= 0.95:
        alerts.append("approval_saturation_high")
    if approval_rate <= 0.05:
        alerts.append("approval_saturation_low")
    if gap > 0.30:
        alerts.append("reward_hacking_warning")
    return alerts


def compute(traces: list[IssueOutcome]) -> dict:
    """Aggregate metrics. Returns primary / reviewer / balance / per_issue."""
    if not traces:
        return {"error": "No traces provided", "n_issues": 0}

    n = len(traces)
    # Require a non-empty final/first diff to count as a "fix" - a clean
    # baseline trivially passes the oracle, and we do not want
    # "coder did nothing" rounds to inflate the headline number.
    def _final_diff_nonempty(t: IssueOutcome) -> bool:
        return bool(t.diff_lengths and t.diff_lengths[-1] > 0)

    def _first_diff_nonempty(t: IssueOutcome) -> bool:
        return bool(t.diff_lengths and t.diff_lengths[0] > 0)

    test_pass = sum(1 for t in traces if t.oracle_passed_final and _final_diff_nonempty(t))
    first_pass = sum(1 for t in traces if t.first_pass_oracle and _first_diff_nonempty(t))
    approved = sum(1 for t in traces if t.approved)
    avg_rounds = sum(t.rounds for t in traces) / n

    rtp = []
    for t in traces:
        # Walk rounds to find the FIRST non-empty diff that passed oracle.
        for r_idx, (oracle_passed, diff_len) in enumerate(
            zip(t.per_round_oracle, t.diff_lengths), start=1
        ):
            if oracle_passed and diff_len > 0:
                rtp.append(r_idx)
                break
    avg_rounds_to_pass = (sum(rtp) / len(rtp)) if rtp else None

    cm = _reviewer_confusion(traces)
    n_approved_rounds = cm["true_approval"] + cm["false_approval"]
    n_oracle_pass_rounds = cm["true_approval"] + cm["false_rejection"]
    n_total_rounds = sum(cm.values())

    reviewer_precision = _safe_div(cm["true_approval"], n_approved_rounds)
    reviewer_recall = _safe_div(cm["true_approval"], n_oracle_pass_rounds)
    reviewer_fpr = _safe_div(
        cm["false_approval"], cm["false_approval"] + cm["true_rejection"]
    )

    approval_rate_round = _safe_div(n_approved_rounds, n_total_rounds)
    test_pass_rate_round = _safe_div(n_oracle_pass_rounds, n_total_rounds)
    balance_gap = abs(approval_rate_round - test_pass_rate_round)

    return {
        "n_issues": n,
        "primary": {
            "test_pass_rate": round(test_pass / n, 3),
            "approval_rate_issue": round(approved / n, 3),
            "first_pass_test_pass_rate": round(first_pass / n, 3),
            "avg_rounds": round(avg_rounds, 2),
            "avg_rounds_to_oracle_pass": round(avg_rounds_to_pass, 2) if avg_rounds_to_pass is not None else None,
        },
        "reviewer": {
            "confusion_matrix": cm,
            "precision": round(reviewer_precision, 3),
            "recall": round(reviewer_recall, 3),
            "false_positive_rate": round(reviewer_fpr, 3),
        },
        "balance": {
            "approval_rate_per_round": round(approval_rate_round, 3),
            "test_pass_rate_per_round": round(test_pass_rate_round, 3),
            "balance_gap": round(balance_gap, 3),
            "alerts": _balance_alerts(approval_rate_round, balance_gap),
        },
        "per_issue": [
            {
                "number": t.issue_number,
                "title": t.title,
                "rounds": t.rounds,
                "approved": t.approved,
                "oracle_passed_final": t.oracle_passed_final,
                "first_pass_oracle": t.first_pass_oracle,
                "rounds_to_oracle_pass": t.rounds_to_oracle_pass,
                "held_out": t.held_out,
            }
            for t in traces
        ],
    }


def compute_split(traces: list[IssueOutcome], held_out: set[int] | None = None) -> dict:
    """Compute metrics with optional held-out / training-stream split.

    `held_out` is a set of issue numbers; matching traces are tagged
    and reported as a separate sub-block. Returns a dict with three
    keys: `all`, `held_out`, `training_stream`.
    """
    if not held_out:
        return {"all": compute(traces), "held_out": None, "training_stream": None}
    for t in traces:
        t.held_out = t.issue_number in held_out
    held = [t for t in traces if t.held_out]
    train = [t for t in traces if not t.held_out]
    return {
        "all": compute(traces),
        "held_out": compute(held),
        "training_stream": compute(train),
    }


def _print_section(metrics: dict) -> None:
    if "error" in metrics or metrics.get("n_issues", 0) == 0:
        print("  (no issues)")
        return
    p, r, b = metrics["primary"], metrics["reviewer"], metrics["balance"]
    print(f"  issues:                       {metrics['n_issues']}")
    print(f"  test_pass_rate:               {p['test_pass_rate'] * 100:5.1f}%   <-- headline")
    print(f"  first_pass_test_pass_rate:    {p['first_pass_test_pass_rate'] * 100:5.1f}%")
    print(f"  approval_rate (issue-level):  {p['approval_rate_issue'] * 100:5.1f}%")
    print(f"  avg rounds:                   {p['avg_rounds']}")
    if p["avg_rounds_to_oracle_pass"] is not None:
        print(f"  avg rounds_to_oracle_pass:    {p['avg_rounds_to_oracle_pass']}")
    print(f"  reviewer precision:           {r['precision'] * 100:5.1f}%")
    print(f"  reviewer recall:              {r['recall'] * 100:5.1f}%")
    print(f"  reviewer FPR:                 {r['false_positive_rate'] * 100:5.1f}%")
    print(
        f"  per-round approval / pass:    {b['approval_rate_per_round'] * 100:5.1f}%"
        f" / {b['test_pass_rate_per_round'] * 100:5.1f}%"
    )
    print(f"  balance_gap:                  {b['balance_gap'] * 100:5.1f}%")
    if b["alerts"]:
        print(f"  ALERTS: {', '.join(b['alerts'])}")


def print_summary(metrics: dict) -> None:
    """Pretty-print headline numbers. Handles both flat and split layouts."""
    if "all" in metrics and metrics.get("held_out") is not None:
        print("\n=== Held-out (eval) ===")
        _print_section(metrics["held_out"])
        print("\n=== Training stream ===")
        _print_section(metrics["training_stream"])
        print("\n=== All issues ===")
        _print_section(metrics["all"])
    elif "all" in metrics:
        _print_section(metrics["all"])
    else:
        _print_section(metrics)
