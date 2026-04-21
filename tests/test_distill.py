"""Tests for harness/distill.py — round classification, note building.

claude_agent_sdk is mocked at the module level so no live connection is needed.
"""
import os
import sys
import types
from unittest import mock

# Stub out claude_agent_sdk before importing harness.distill so the module-level
# import doesn't fail in environments without the SDK installed.
_sdk_stub = types.ModuleType("claude_agent_sdk")
for _attr in (
    "AssistantMessage", "ClaudeAgentOptions", "ClaudeSDKClient",
    "ResultMessage", "ToolUseBlock", "create_sdk_mcp_server", "tool",
):
    setattr(_sdk_stub, _attr, mock.MagicMock())
sys.modules.setdefault("claude_agent_sdk", _sdk_stub)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.distill import (  # noqa: E402
    _build_reviewer_note,
    _classify_round,
    _select_coder_lesson_round,
    _truncate,
    alternating_schedule,
)

# ── _classify_round ───────────────────────────────────────────────────────────

class TestClassifyRound:
    def test_true_approval(self):
        assert _classify_round(approved=True, oracle_passed=True) == "true_approval"

    def test_false_approval(self):
        assert _classify_round(approved=True, oracle_passed=False) == "false_approval"

    def test_true_rejection(self):
        assert _classify_round(approved=False, oracle_passed=False) == "true_rejection"

    def test_false_rejection(self):
        assert _classify_round(approved=False, oracle_passed=True) == "false_rejection"

    def test_all_outcomes_covered(self):
        outcomes = {
            _classify_round(True, True),
            _classify_round(True, False),
            _classify_round(False, False),
            _classify_round(False, True),
        }
        assert outcomes == {"true_approval", "false_approval", "true_rejection", "false_rejection"}


# ── _build_reviewer_note ──────────────────────────────────────────────────────

class TestBuildReviewerNote:
    def _oracle(self, n_failed: int = 0, failing: list | None = None) -> dict:
        return {
            "passed": n_failed == 0,
            "n_failed": n_failed,
            "failing_tests": failing or [],
        }

    def _round_data(self, comments: list | None = None) -> dict:
        return {"comments": comments or []}

    def test_true_approval_note(self):
        note = _build_reviewer_note("true_approval", self._round_data(), self._oracle())
        assert "Approved" in note
        assert "passed" in note.lower()

    def test_true_rejection_includes_comment(self):
        note = _build_reviewer_note(
            "true_rejection",
            self._round_data(comments=["needs edge case test"]),
            self._oracle(n_failed=2),
        )
        assert "needs edge case test" in note

    def test_false_approval_includes_failure_info(self):
        note = _build_reviewer_note(
            "false_approval",
            self._round_data(),
            self._oracle(n_failed=3, failing=["tests/arrow_tests.py::TestFoo::test_bar"]),
        )
        assert "3" in note
        assert "arrow_tests" in note

    def test_false_rejection_includes_comment(self):
        note = _build_reviewer_note(
            "false_rejection",
            self._round_data(comments=["please add localization tests"]),
            self._oracle(),
        )
        assert "please add localization tests" in note

    def test_no_comment_falls_back_to_placeholder(self):
        note = _build_reviewer_note(
            "true_rejection",
            self._round_data(comments=[]),
            self._oracle(n_failed=1),
        )
        assert "no comment" in note

    def test_comment_truncated(self):
        long_comment = "x" * 300
        note = _build_reviewer_note(
            "true_rejection",
            self._round_data(comments=[long_comment]),
            self._oracle(n_failed=1),
        )
        # The note template fills in comment[:160]; verify note is not massive
        assert len(note) < 600


# ── _select_coder_lesson_round ────────────────────────────────────────────────

def _make_round(oracle_passed: bool, round_num: int = 1) -> dict:
    return {
        "round": round_num,
        "diff": f"+ fix for round {round_num}",
        "oracle": {"passed": oracle_passed},
    }


class TestSelectCoderLessonRound:
    def test_prefers_first_oracle_pass(self):
        trace = {
            "comments_per_round": [
                _make_round(True, round_num=1),
                _make_round(True, round_num=2),
            ]
        }
        selected = _select_coder_lesson_round(trace)
        assert selected is not None
        assert selected["round"] == 1

    def test_picks_first_pass_even_if_not_round_1(self):
        trace = {
            "comments_per_round": [
                _make_round(False, round_num=1),
                _make_round(True, round_num=2),
            ]
        }
        selected = _select_coder_lesson_round(trace)
        assert selected is not None
        assert selected["round"] == 2

    def test_returns_none_when_no_oracle_pass(self):
        trace = {
            "comments_per_round": [
                _make_round(False, round_num=1),
                _make_round(False, round_num=2),
            ]
        }
        assert _select_coder_lesson_round(trace) is None

    def test_empty_trace_returns_none(self):
        assert _select_coder_lesson_round({"comments_per_round": []}) is None
        assert _select_coder_lesson_round({}) is None


# ── _truncate ─────────────────────────────────────────────────────────────────

class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_long_string_truncated(self):
        # _truncate returns s[:n-1] + "..." so length is (n-1) + 3 = n+2 when len(s) > n
        result = _truncate("abcdefghij", 5)
        assert result.endswith("...")
        assert len(result) < len("abcdefghij")

    def test_exact_length_unchanged(self):
        assert _truncate("hello", 5) == "hello"


# ── alternating_schedule ──────────────────────────────────────────────────────

class TestAlternatingSchedule:
    def test_odd_updates_coder(self):
        s = alternating_schedule(1)
        assert s["update_coder"] is True
        assert s["update_reviewer"] is False

    def test_even_updates_reviewer(self):
        s = alternating_schedule(2)
        assert s["update_coder"] is False
        assert s["update_reviewer"] is True

    def test_sequence(self):
        coders = [alternating_schedule(i)["update_coder"] for i in range(1, 7)]
        assert coders == [True, False, True, False, True, False]
