"""Tests for harness/oracle.py — pytest output parsing and test file resolution."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.oracle import (
    OracleResult,
    _parse_pytest_output,
    _resolve_test_files,
)

# ── _parse_pytest_output ──────────────────────────────────────────────────────

class TestParsePytestOutput:
    def _parse(self, out: str, returncode: int = 0) -> OracleResult:
        return _parse_pytest_output(out, ["tests/arrow_tests.py"], 1.0, returncode)

    def test_all_passed(self):
        out = "20 passed in 1.23s"
        r = self._parse(out, returncode=0)
        assert r.passed is True
        assert r.n_passed == 20
        assert r.n_failed == 0
        assert r.n_tests == 20
        assert r.error == ""

    def test_some_failed(self):
        out = "18 passed, 2 failed in 1.23s\nFAILED tests/arrow_tests.py::TestFoo::test_bar"
        r = self._parse(out, returncode=1)
        assert r.passed is False
        assert r.n_passed == 18
        assert r.n_failed == 2
        assert "tests/arrow_tests.py::TestFoo::test_bar" in r.failing_tests

    def test_collection_error(self):
        out = "ERROR collecting tests/arrow_tests.py\nSyntaxError: invalid syntax"
        r = self._parse(out, returncode=2)
        assert r.passed is False
        assert r.n_tests == 0
        assert r.error != ""

    def test_skipped_counted(self):
        out = "10 passed, 2 skipped in 0.5s"
        r = self._parse(out, returncode=0)
        assert r.passed is True
        assert r.n_tests == 12
        assert r.n_passed == 10

    def test_errors_counted_as_failures(self):
        out = "5 passed, 1 error in 0.8s"
        r = self._parse(out, returncode=1)
        assert r.n_failed == 1
        assert r.passed is False

    def test_elapsed_stored(self):
        r = _parse_pytest_output("3 passed in 0.1s", ["tests/"], 2.5, 0)
        assert r.elapsed_s == pytest.approx(2.5)

    def test_targeted_files_stored(self):
        targets = ["tests/arrow_tests.py", "tests/locales_tests.py"]
        r = _parse_pytest_output("5 passed", targets, 0.1, 0)
        assert r.targeted_files == targets

    def test_max_failures_capped(self):
        lines = [f"FAILED tests/t.py::Test::test_{i}" for i in range(20)]
        out = "\n".join(lines)
        r = self._parse(out, returncode=1)
        assert len(r.failing_tests) <= 15

    def test_zero_tests_passing_returncode_is_not_passed(self):
        # pytest exits 0 but collected nothing — should not count as passed
        r = self._parse("", returncode=0)
        assert r.passed is False


# ── _resolve_test_files ───────────────────────────────────────────────────────

class TestResolveTestFiles:
    def test_source_to_tests_mapping(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "arrow_tests.py").touch()
        result = _resolve_test_files(["arrow/arrow.py"], str(tmp_path))
        assert result == ["tests/arrow_tests.py"]

    def test_test_name_normalize(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "locales_tests.py").touch()
        result = _resolve_test_files(["tests/test_locales.py"], str(tmp_path))
        assert result == ["tests/locales_tests.py"]

    def test_missing_file_excluded(self, tmp_path):
        # The mapped test file does not exist on disk
        result = _resolve_test_files(["arrow/arrow.py"], str(tmp_path))
        assert result == []

    def test_deduplication(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "arrow_tests.py").touch()
        # Both the source file and its legacy test name map to the same test file
        result = _resolve_test_files(
            ["arrow/arrow.py", "tests/test_arrow.py"], str(tmp_path)
        )
        assert result.count("tests/arrow_tests.py") == 1

    def test_unknown_file_returns_empty(self, tmp_path):
        result = _resolve_test_files(["arrow/unknown_module.py"], str(tmp_path))
        assert result == []

    def test_empty_input(self, tmp_path):
        assert _resolve_test_files([], str(tmp_path)) == []
        assert _resolve_test_files(None, str(tmp_path)) == []


# ── OracleResult.to_dict ──────────────────────────────────────────────────────

class TestOracleResultToDict:
    def test_round_trips(self):
        r = OracleResult(
            passed=True,
            targeted_files=["tests/arrow_tests.py"],
            n_tests=10,
            n_passed=10,
        )
        d = r.to_dict()
        assert d["passed"] is True
        assert d["targeted_files"] == ["tests/arrow_tests.py"]
        assert d["n_tests"] == 10
        assert "broader_ran" in d
