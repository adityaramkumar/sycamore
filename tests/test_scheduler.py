"""Tests for harness/scheduler.py — split, schedule_for, reviewer_audit."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.scheduler import (
    make_split,
    reviewer_audit,
    schedule_for,
)

# ── make_split ────────────────────────────────────────────────────────────────

class TestMakeSplit:
    def test_sizes_correct(self):
        train, held = make_split(list(range(1, 26)), heldout_size=7, seed=42)
        assert len(train) == 18
        assert len(held) == 7

    def test_disjoint(self):
        train, held = make_split(list(range(1, 26)), heldout_size=7, seed=42)
        assert set(train) & set(held) == set()

    def test_union_is_full_set(self):
        numbers = list(range(1, 26))
        train, held = make_split(numbers, heldout_size=7, seed=42)
        assert sorted(train + held) == sorted(numbers)

    def test_deterministic(self):
        a_train, a_held = make_split(list(range(25)), heldout_size=5, seed=99)
        b_train, b_held = make_split(list(range(25)), heldout_size=5, seed=99)
        assert a_train == b_train
        assert a_held == b_held

    def test_different_seeds_differ(self):
        _, held_1 = make_split(list(range(25)), heldout_size=5, seed=1)
        _, held_2 = make_split(list(range(25)), heldout_size=5, seed=2)
        assert held_1 != held_2

    def test_zero_heldout(self):
        train, held = make_split([1, 2, 3], heldout_size=0)
        assert held == []
        assert sorted(train) == [1, 2, 3]

    def test_all_heldout(self):
        train, held = make_split([1, 2, 3], heldout_size=3)
        assert train == []
        assert len(held) == 3

    def test_invalid_size_raises(self):
        with pytest.raises(ValueError):
            make_split([1, 2, 3], heldout_size=10)

    def test_negative_size_raises(self):
        with pytest.raises(ValueError):
            make_split([1, 2, 3], heldout_size=-1)

    def test_deduplicates_input(self):
        train, held = make_split([1, 1, 2, 2, 3], heldout_size=1, seed=42)
        assert len(train) + len(held) == 3

    def test_returns_sorted_lists(self):
        train, held = make_split(list(range(10, 0, -1)), heldout_size=3, seed=42)
        assert train == sorted(train)
        assert held == sorted(held)


# ── schedule_for ──────────────────────────────────────────────────────────────

class TestScheduleFor:
    def test_held_out_updates_nothing(self):
        sched = schedule_for(held_out=True, stream_index=1, reviewer_frozen=False)
        assert sched["update_coder"] is False
        assert sched["update_reviewer"] is False

    def test_odd_index_updates_coder(self):
        sched = schedule_for(held_out=False, stream_index=1, reviewer_frozen=False)
        assert sched["update_coder"] is True
        assert sched["update_reviewer"] is False

    def test_even_index_updates_reviewer(self):
        sched = schedule_for(held_out=False, stream_index=2, reviewer_frozen=False)
        assert sched["update_coder"] is False
        assert sched["update_reviewer"] is True

    def test_frozen_reviewer_not_updated_on_even(self):
        sched = schedule_for(held_out=False, stream_index=2, reviewer_frozen=True)
        assert sched["update_reviewer"] is False

    def test_frozen_reviewer_does_not_affect_coder(self):
        sched = schedule_for(held_out=False, stream_index=1, reviewer_frozen=True)
        assert sched["update_coder"] is True

    def test_alternation_over_sequence(self):
        updates = [
            schedule_for(held_out=False, stream_index=i, reviewer_frozen=False)
            for i in range(1, 7)
        ]
        coders = [u["update_coder"] for u in updates]
        reviewers = [u["update_reviewer"] for u in updates]
        assert coders == [True, False, True, False, True, False]
        assert reviewers == [False, True, False, True, False, True]


# ── reviewer_audit ────────────────────────────────────────────────────────────

def _make_trace(approved: bool, oracle_passed: bool, diff_length: int = 100) -> dict:
    return {
        "issue_number": 1,
        "issue_title": "test",
        "rounds": 1,
        "approved": approved,
        "oracle_passed_final": oracle_passed,
        "first_pass_oracle": oracle_passed,
        "comments_per_round": [{
            "round": 1,
            "approved": approved,
            "diff_length": diff_length,
            "diff": "+ some change",
            "comments": [],
            "oracle": {"passed": oracle_passed, "n_tests": 5, "n_passed": 5 if oracle_passed else 3},
        }],
    }


class TestReviewerAudit:
    def test_under_min_samples_not_frozen(self):
        traces = [_make_trace(True, True)]  # 1 sample < min_samples=4
        health = reviewer_audit(traces, min_samples=4)
        assert health.frozen is False
        assert health.reasons == []

    def test_precision_computed_correctly(self):
        # 3 true_approval, 1 true_rejection → precision = 1.0, but approval_rate = 0.75
        traces = (
            [_make_trace(True, True)] * 3 +
            [_make_trace(False, False)] * 1
        )
        health = reviewer_audit(traces, min_samples=4, approval_high=0.95)
        assert health.precision == pytest.approx(1.0)
        assert health.frozen is False

    def test_low_precision_triggers_freeze(self):
        # 2 true_approval, 3 false_approval → precision = 2/5 = 0.4
        traces = (
            [_make_trace(True, True)] * 2 +
            [_make_trace(True, False)] * 3
        )
        health = reviewer_audit(traces, min_samples=4, precision_floor=0.6)
        assert health.frozen is True
        assert any("precision" in r for r in health.reasons)

    def test_approval_saturation_high(self):
        traces = [_make_trace(True, True) for _ in range(6)]
        health = reviewer_audit(traces, min_samples=4, approval_high=0.95)
        assert health.frozen is True
        assert any("approval_saturation_high" in r for r in health.reasons)

    def test_approval_saturation_low(self):
        traces = [_make_trace(False, False) for _ in range(6)]
        health = reviewer_audit(traces, min_samples=4, approval_low=0.05)
        assert health.frozen is True
        assert any("approval_saturation_low" in r for r in health.reasons)

    def test_reviewer_over_asking(self):
        # All oracle pass but reviewer rejects → over_asking
        traces = [_make_trace(False, True) for _ in range(6)]
        health = reviewer_audit(traces, min_samples=4, balance_gap_max=0.30)
        assert health.frozen is True
        assert any("reviewer_over_asking" in r for r in health.reasons)

    def test_reward_hacking(self):
        # All approved but oracle fails → reward hacking
        traces = [_make_trace(True, False) for _ in range(6)]
        health = reviewer_audit(traces, min_samples=4, balance_gap_max=0.30)
        assert health.frozen is True
        assert any("reward_hacking" in r for r in health.reasons)

    def test_health_fields_populated(self):
        traces = [_make_trace(True, True) for _ in range(4)]
        health = reviewer_audit(traces, min_samples=4)
        assert health.samples == 4
        assert 0.0 <= health.precision <= 1.0
        assert 0.0 <= health.approval_rate_per_round <= 1.0
        assert health.balance_gap >= 0.0
