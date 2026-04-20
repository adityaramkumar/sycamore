"""Tests for harness/metrics.py — flatten, compute, reviewer confusion, alerts."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.metrics import (
    IssueOutcome,
    _balance_alerts,
    _flatten,
    _reviewer_confusion,
    _safe_div,
    compute,
    compute_split,
    load_traces,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _round(approved: bool, oracle_passed: bool, diff_length: int = 100, round_num: int = 1) -> dict:
    return {
        "round": round_num,
        "approved": approved,
        "diff_length": diff_length,
        "diff": "+ some change",
        "comments": [] if approved else ["needs more tests"],
        "oracle": {"passed": oracle_passed, "n_tests": 5, "n_passed": 5 if oracle_passed else 3},
    }


def _trace(
    issue_number: int,
    rounds: list[dict],
    approved: bool | None = None,
    oracle_final: bool | None = None,
) -> dict:
    last_round = rounds[-1] if rounds else {}
    return {
        "issue_number": issue_number,
        "issue_title": f"Issue {issue_number}",
        "rounds": len(rounds),
        "approved": approved if approved is not None else last_round.get("approved", False),
        "oracle_passed_final": oracle_final if oracle_final is not None else (
            last_round.get("oracle", {}).get("passed", False)
        ),
        "first_pass_oracle": rounds[0].get("oracle", {}).get("passed", False) if rounds else False,
        "comments_per_round": rounds,
    }


# ── _safe_div ─────────────────────────────────────────────────────────────────

def test_safe_div_normal():
    assert _safe_div(3, 4) == pytest.approx(0.75)


def test_safe_div_zero_denominator():
    assert _safe_div(5, 0) == 0.0


# ── _flatten ─────────────────────────────────────────────────────────────────

class TestFlatten:
    def test_single_round_pass(self):
        t = _trace(1, [_round(True, True)])
        o = _flatten(t)
        assert o.oracle_passed_final is True
        assert o.approved is True
        assert o.first_pass_oracle is True
        assert o.rounds == 1
        assert o.rounds_to_oracle_pass == 1

    def test_two_rounds_pass_on_second(self):
        t = _trace(2, [_round(False, False, round_num=1), _round(True, True, round_num=2)])
        o = _flatten(t)
        assert o.first_pass_oracle is False
        assert o.oracle_passed_final is True
        assert o.rounds_to_oracle_pass == 2

    def test_never_passes(self):
        t = _trace(3, [_round(False, False), _round(False, False)])
        o = _flatten(t)
        assert o.oracle_passed_final is False
        assert o.rounds_to_oracle_pass is None

    def test_diff_lengths_captured(self):
        t = _trace(4, [
            _round(False, False, diff_length=50),
            _round(True, True, diff_length=200),
        ])
        o = _flatten(t)
        assert o.diff_lengths == [50, 200]


# ── _reviewer_confusion ───────────────────────────────────────────────────────

class TestReviewerConfusion:
    def _outcomes(self, outcomes: list[IssueOutcome]) -> dict:
        return _reviewer_confusion(outcomes)

    def test_true_approval(self):
        t = _trace(1, [_round(True, True)])
        cm = self._outcomes([_flatten(t)])
        assert cm["true_approval"] == 1
        assert cm["false_approval"] == 0

    def test_false_approval(self):
        t = _trace(1, [_round(True, False)])
        cm = self._outcomes([_flatten(t)])
        assert cm["false_approval"] == 1
        assert cm["true_approval"] == 0

    def test_true_rejection(self):
        t = _trace(1, [_round(False, False)])
        cm = self._outcomes([_flatten(t)])
        assert cm["true_rejection"] == 1

    def test_false_rejection(self):
        t = _trace(1, [_round(False, True)])
        cm = self._outcomes([_flatten(t)])
        assert cm["false_rejection"] == 1

    def test_empty_diff_rounds_excluded(self):
        t = _trace(1, [_round(False, True, diff_length=0)])
        cm = self._outcomes([_flatten(t)])
        assert sum(cm.values()) == 0

    def test_multiple_rounds_aggregated(self):
        t = _trace(1, [
            _round(False, False),  # true_rejection
            _round(True, True),    # true_approval
        ])
        cm = self._outcomes([_flatten(t)])
        assert cm["true_rejection"] == 1
        assert cm["true_approval"] == 1


# ── _balance_alerts ───────────────────────────────────────────────────────────

class TestBalanceAlerts:
    def test_no_alerts(self):
        assert _balance_alerts(0.5, 0.5) == []

    def test_approval_saturation_high(self):
        alerts = _balance_alerts(0.97, 0.5)
        assert "approval_saturation_high" in alerts

    def test_approval_saturation_low(self):
        alerts = _balance_alerts(0.02, 0.5)
        assert "approval_saturation_low" in alerts

    def test_reward_hacking(self):
        alerts = _balance_alerts(0.9, 0.5)
        assert "reward_hacking_warning" in alerts

    def test_reviewer_over_asking(self):
        alerts = _balance_alerts(0.3, 0.8)
        assert "reviewer_over_asking_warning" in alerts

    def test_boundary_not_triggered(self):
        # A gap clearly below 0.30 should not trigger the alert
        alerts = _balance_alerts(0.70, 0.50)
        assert "reward_hacking_warning" not in alerts


# ── compute ───────────────────────────────────────────────────────────────────

class TestCompute:
    def test_empty_returns_error(self):
        result = compute([])
        assert "error" in result

    def test_basic_metrics(self):
        traces = [
            _flatten(_trace(1, [_round(True, True)])),   # pass + approved
            _flatten(_trace(2, [_round(False, False)])),  # fail + rejected
        ]
        result = compute(traces)
        assert result["n_issues"] == 2
        assert result["primary"]["test_pass_rate"] == pytest.approx(0.5)
        assert result["primary"]["approval_rate_issue"] == pytest.approx(0.5)

    def test_first_pass_rate(self):
        traces = [
            _flatten(_trace(1, [_round(True, True)])),         # first-pass oracle pass
            _flatten(_trace(2, [_round(False, False), _round(True, True)])),  # pass on round 2
        ]
        result = compute(traces)
        assert result["primary"]["first_pass_test_pass_rate"] == pytest.approx(0.5)

    def test_reviewer_precision_perfect(self):
        # All approvals were oracle-correct
        traces = [_flatten(_trace(i, [_round(True, True)])) for i in range(3)]
        result = compute(traces)
        assert result["reviewer"]["precision"] == pytest.approx(1.0)

    def test_reviewer_recall(self):
        # 2 oracle passes, only 1 approved → recall = 0.5
        traces = [
            _flatten(_trace(1, [_round(True, True)])),
            _flatten(_trace(2, [_round(False, True)])),
        ]
        result = compute(traces)
        assert result["reviewer"]["recall"] == pytest.approx(0.5)

    def test_per_issue_list(self):
        traces = [_flatten(_trace(42, [_round(True, True)]))]
        result = compute(traces)
        assert result["per_issue"][0]["number"] == 42

    def test_empty_diff_excluded_from_pass_rate(self):
        # Oracle passes on empty diff (unchanged baseline) — should not inflate test_pass_rate
        t = _trace(1, [_round(True, True, diff_length=0)])
        result = compute([_flatten(t)])
        assert result["primary"]["test_pass_rate"] == pytest.approx(0.0)


# ── compute_split ─────────────────────────────────────────────────────────────

class TestComputeSplit:
    def test_no_held_out(self):
        traces = [_flatten(_trace(1, [_round(True, True)]))]
        result = compute_split(traces, held_out=None)
        assert result["held_out"] is None
        assert result["training_stream"] is None
        assert "all" in result

    def test_split_separates_correctly(self):
        traces = [
            _flatten(_trace(1, [_round(True, True)])),
            _flatten(_trace(2, [_round(False, False)])),
        ]
        result = compute_split(traces, held_out={1})
        assert result["held_out"]["n_issues"] == 1
        assert result["training_stream"]["n_issues"] == 1


# ── load_traces ───────────────────────────────────────────────────────────────

class TestLoadTraces:
    def test_loads_json_files(self, tmp_path):
        import json

        trace = _trace(99, [_round(True, True)])
        (tmp_path / "issue_99.json").write_text(json.dumps(trace))
        (tmp_path / "other_file.txt").write_text("ignored")
        traces = load_traces(str(tmp_path))
        assert len(traces) == 1
        assert traces[0].issue_number == 99

    def test_bad_json_skipped(self, tmp_path):
        (tmp_path / "issue_1.json").write_text("{bad json")
        traces = load_traces(str(tmp_path))
        assert traces == []

    def test_nonexistent_dir(self, tmp_path):
        traces = load_traces(str(tmp_path / "nonexistent"))
        assert traces == []
