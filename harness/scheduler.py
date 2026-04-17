"""
scheduler.py — Policy-level guardrails on top of the trace loop.

This module owns the *decisions* that are not algorithmic but
political: who learns when, who gets evaluated against what, and when
to pull the emergency brake on a misbehaving reviewer. It does not
own any state of its own; it's pure functions over inputs.

Three responsibilities:

1. Held-out split (DESIGN.md sec 5 & 6.3).
   `make_split(numbers, heldout_size, seed)` returns deterministic
   (training, held_out) lists of issue numbers. Same seed -> same
   split, always. Held-out issues are RUN but distillation is
   suppressed so the headline metric stays uncontaminated.

2. Alternating updates (DESIGN.md sec 4.3 / 5.3).
   On each *training-stream* issue we update either the coder memory
   or the reviewer memory, never both, alternating by parity. Held-out
   issues update neither. This prevents the agents from co-overfitting
   on the same trace within the same step.

3. Reviewer audit & freeze (DESIGN.md sec 5.2).
   `reviewer_audit(traces)` rolls up reviewer precision, approval
   rate, and balance gap from a window of recent traces. If precision
   drops below the floor or approval rate saturates, we mark the
   reviewer FROZEN and stop updating its memory. Freezing is sticky
   in the prototype — recovery is future work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random

try:
    from harness.metrics import IssueOutcome, _flatten, _reviewer_confusion, _safe_div
except ImportError:
    from metrics import IssueOutcome, _flatten, _reviewer_confusion, _safe_div  # type: ignore

DEFAULT_HELDOUT_SIZE = 7
DEFAULT_SEED = 42

# Audit thresholds (DESIGN.md sec 5.2 + 8.3).
MIN_AUDIT_SAMPLES = 4          # need at least this many per-round samples before judging
PRECISION_FLOOR = 0.6
APPROVAL_SATURATION_HIGH = 0.95
APPROVAL_SATURATION_LOW = 0.05
BALANCE_GAP_MAX = 0.30


def make_split(
    issue_numbers: list[int],
    heldout_size: int = DEFAULT_HELDOUT_SIZE,
    seed: int = DEFAULT_SEED,
) -> tuple[list[int], list[int]]:
    """Deterministic train/held-out split.

    The held-out set is sampled from a sorted-then-shuffled copy so the
    split is independent of caller-supplied order. Returns (training,
    held_out) as sorted lists.
    """
    if heldout_size < 0 or heldout_size > len(issue_numbers):
        raise ValueError(f"heldout_size {heldout_size} out of range for {len(issue_numbers)} issues")
    sorted_nums = sorted(set(int(n) for n in issue_numbers))
    rng = random.Random(seed)
    shuffled = sorted_nums.copy()
    rng.shuffle(shuffled)
    held = sorted(shuffled[:heldout_size])
    train = sorted(shuffled[heldout_size:])
    return train, held


def schedule_for(
    held_out: bool,
    stream_index: int,
    reviewer_frozen: bool,
) -> dict:
    """What updates apply to *this* issue?

    Held-out issues update nothing.
    Training-stream issues alternate parity:
      stream_index odd  -> update_coder
      stream_index even -> update_reviewer (unless reviewer is frozen)
    """
    if held_out:
        return {"update_coder": False, "update_reviewer": False}
    update_coder = stream_index % 2 == 1
    update_reviewer = stream_index % 2 == 0 and not reviewer_frozen
    return {"update_coder": update_coder, "update_reviewer": update_reviewer}


@dataclass
class ReviewerHealth:
    samples: int
    precision: float
    approval_rate_per_round: float
    test_pass_rate_per_round: float
    balance_gap: float
    frozen: bool
    reasons: list[str] = field(default_factory=list)


def reviewer_audit(
    recent_traces: list[dict],
    *,
    min_samples: int = MIN_AUDIT_SAMPLES,
    precision_floor: float = PRECISION_FLOOR,
    approval_high: float = APPROVAL_SATURATION_HIGH,
    approval_low: float = APPROVAL_SATURATION_LOW,
    balance_gap_max: float = BALANCE_GAP_MAX,
) -> ReviewerHealth:
    """Compute reviewer health from a list of trace dicts.

    Per-round samples (not per-issue) drive the calculation, matching
    the metric layer in harness.metrics. If samples < min_samples,
    returns a non-frozen ReviewerHealth with no reasons (under-determined).
    """
    flat: list[IssueOutcome] = [_flatten(t) for t in recent_traces if t]
    cm = _reviewer_confusion(flat)
    n_approved = cm["true_approval"] + cm["false_approval"]
    n_oracle_pass = cm["true_approval"] + cm["false_rejection"]
    n_total = sum(cm.values())

    precision = _safe_div(cm["true_approval"], n_approved)
    approval_rate = _safe_div(n_approved, n_total)
    test_pass_rate = _safe_div(n_oracle_pass, n_total)
    gap = abs(approval_rate - test_pass_rate)

    reasons: list[str] = []
    if n_total >= min_samples:
        if n_approved > 0 and precision < precision_floor:
            reasons.append(f"precision_below_floor({precision:.2f}<{precision_floor})")
        if approval_rate >= approval_high:
            reasons.append(f"approval_saturation_high({approval_rate:.2f})")
        if approval_rate <= approval_low:
            reasons.append(f"approval_saturation_low({approval_rate:.2f})")
        if gap > balance_gap_max:
            reasons.append(f"balance_gap_exceeded({gap:.2f}>{balance_gap_max})")

    return ReviewerHealth(
        samples=n_total,
        precision=round(precision, 3),
        approval_rate_per_round=round(approval_rate, 3),
        test_pass_rate_per_round=round(test_pass_rate, 3),
        balance_gap=round(gap, 3),
        frozen=bool(reasons),
        reasons=reasons,
    )
