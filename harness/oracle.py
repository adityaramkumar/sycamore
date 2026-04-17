"""
oracle.py — Run a targeted subset of arrow's pytest suite against the
current working tree of the target repo, returning a structured result
that the loop records as ground truth for the trace.

The oracle is the only objective signal in the system. The reviewer
agent MUST NEVER see its output (asymmetric information per DESIGN.md
section 5.1 / 7.2).

At the pinned baseline c9cecaf, arrow ships nose-era test files named
tests/*_tests.py. They run cleanly through pytest on Python 3.13 once
chai, nose, pytz, simplejson, and python-dateutil are installed. We
override pytest's python_files discovery pattern to pick up the legacy
filename convention.

Issue.files_changed in data/issues.json contains both the legacy name
(tests/arrow_tests.py) and the post-2019 name (tests/test_arrow.py).
TEST_NAME_NORMALIZE maps the post-2019 names back to the names that
exist at baseline, so we can target the same tests regardless of which
naming convention the source data used.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
import re
import subprocess
import sys
import time

REPO = os.environ.get("TARGET_REPO_PATH", "./arrow")
DEFAULT_TIMEOUT_S = int(os.environ.get("ORACLE_TIMEOUT_S", "120"))
MAX_FAILURES_LOGGED = 15

# After the targeted-test slice passes, optionally run the broader
# tests/ directory as a regression sanity check. Catches cross-file
# breakage a narrow slice would miss (e.g. a fix in arrow/arrow.py
# that accidentally breaks something in tests/locales_tests.py).
# Toggle off via ORACLE_BROADER_CHECK=0 if it gets too slow.
BROADER_CHECK_ENABLED = os.environ.get("ORACLE_BROADER_CHECK", "1") != "0"

SOURCE_TO_TESTS: dict[str, str] = {
    "arrow/arrow.py":     "tests/arrow_tests.py",
    "arrow/locales.py":   "tests/locales_tests.py",
    "arrow/parser.py":    "tests/parser_tests.py",
    "arrow/factory.py":   "tests/factory_tests.py",
    "arrow/formatter.py": "tests/formatter_tests.py",
    "arrow/api.py":       "tests/api_tests.py",
}

TEST_NAME_NORMALIZE: dict[str, str] = {
    "tests/test_arrow.py":     "tests/arrow_tests.py",
    "tests/test_locales.py":   "tests/locales_tests.py",
    "tests/test_parser.py":    "tests/parser_tests.py",
    "tests/test_factory.py":   "tests/factory_tests.py",
    "tests/test_formatter.py": "tests/formatter_tests.py",
    "tests/test_api.py":       "tests/api_tests.py",
}


@dataclass
class OracleResult:
    """Structured outcome of a single oracle invocation.

    Primary field (what downstream metrics key on):
      passed          True iff the targeted slice passed AND (if run) the
                      broader regression check also passed.

    Targeted slice (the issue-specific test files):
      targeted_files  Test files we asked pytest to run. ["tests/"] means
                      fallback-to-full-suite — usually indicates the issue
                      changed source files we have no test mapping for.
      n_tests / n_passed / n_failed / failing_tests
      elapsed_s       Wall time of the targeted slice.

    Broader regression check (only populated when targeted passed AND
    ORACLE_BROADER_CHECK is enabled). Runs the entire tests/ directory.
      broader_ran         True if we actually ran it.
      broader_passed      True iff every test in tests/ passed.
      broader_n_tests / broader_n_failed / broader_failing
      broader_elapsed_s

    Failure modes:
      timed_out       subprocess hit ORACLE_TIMEOUT_S.
      error           pytest itself crashed (e.g. patched code fails to
                      import, HEAD drift, pytest-cov flags from tox.ini,
                      etc). When non-empty, n_tests will typically be 0.
    """
    passed: bool
    targeted_files: list[str]
    n_tests: int = 0
    n_passed: int = 0
    n_failed: int = 0
    failing_tests: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    timed_out: bool = False
    error: str = ""
    # Broader regression check (optional).
    broader_ran: bool = False
    broader_passed: bool = False
    broader_n_tests: int = 0
    broader_n_failed: int = 0
    broader_failing: list[str] = field(default_factory=list)
    broader_elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_test_files(files_changed: list[str], repo_abs: str) -> list[str]:
    """Map an issue's files_changed to deduplicated test files that
    exist at the current commit. Returns [] if nothing maps, signalling
    a fallback to the full suite.
    """
    targets: set[str] = set()
    for f in files_changed or []:
        if f.startswith("tests/"):
            targets.add(TEST_NAME_NORMALIZE.get(f, f))
        elif f in SOURCE_TO_TESTS:
            targets.add(SOURCE_TO_TESTS[f])
    return [t for t in sorted(targets) if os.path.exists(os.path.join(repo_abs, t))]


_SUMMARY_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped)", re.IGNORECASE)
_FAILED_LINE_RE = re.compile(r"^FAILED\s+(\S+)")


def _parse_pytest_output(out: str, targets: list[str], elapsed_s: float, returncode: int) -> OracleResult:
    counts = {"passed": 0, "failed": 0, "error": 0, "errors": 0, "skipped": 0}
    for n, kind in _SUMMARY_RE.findall(out):
        counts[kind.lower()] = max(counts[kind.lower()], int(n))
    n_passed = counts["passed"]
    n_failed = counts["failed"] + counts["error"] + counts["errors"]
    n_tests = n_passed + n_failed + counts["skipped"]

    failing: list[str] = []
    for line in out.splitlines():
        m = _FAILED_LINE_RE.match(line.strip())
        if m:
            failing.append(m.group(1))
        if len(failing) >= MAX_FAILURES_LOGGED:
            break

    # Collection failure (e.g. a syntax error in the patched code) leaves
    # n_tests == 0 with a nonzero exit. Surface the tail of pytest's
    # output as the error so the trace records why the oracle bailed.
    error = ""
    if n_tests == 0 and returncode != 0:
        error = "\n".join(out.splitlines()[-8:])[:1000]

    return OracleResult(
        passed=(returncode == 0 and n_failed == 0 and n_tests > 0),
        targeted_files=targets,
        n_tests=n_tests,
        n_passed=n_passed,
        n_failed=n_failed,
        failing_tests=failing,
        elapsed_s=round(elapsed_s, 3),
        error=error,
    )


def _run_pytest(targets: list[str], repo_abs: str, timeout_s: int) -> tuple[OracleResult, bool]:
    """Run pytest against `targets` from `repo_abs`. Returns (result, timed_out).

    `--override-ini addopts=` neutralizes any pytest-cov flags the
    repo's setup.cfg/tox.ini might inject (master arrow's tox.ini does
    this and would crash pytest in our venv). The python_files override
    picks up the legacy *_tests.py naming used at baseline c9cecaf.
    """
    cmd = [
        sys.executable, "-m", "pytest",
        *targets,
        "-p", "no:cacheprovider",
        "--override-ini", "addopts=",
        "--override-ini", "python_files=*_tests.py *_test.py test_*.py",
        "--tb=no",
        "-q",
        "--no-header",
    ]
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_abs,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        result = OracleResult(
            passed=False,
            targeted_files=targets,
            elapsed_s=time.time() - start,
            timed_out=True,
            error=f"pytest timed out after {timeout_s}s",
        )
        return result, True
    result = _parse_pytest_output(
        proc.stdout + proc.stderr,
        targets,
        time.time() - start,
        proc.returncode,
    )
    return result, False


def run(issue: dict, repo: str | None = None, timeout_s: int | None = None) -> OracleResult:
    """Run the oracle against the current working tree of `repo`.

    1. Targeted slice: pytest on the test files inferred from
       issue['files_changed'] (falls back to full tests/ if none apply).
    2. Broader regression check: if (1) passed AND ORACLE_BROADER_CHECK
       is on, also run the full tests/ directory. Both must pass for
       `result.passed` to be True.
    """
    repo_abs = os.path.abspath(repo or REPO)
    timeout = timeout_s or DEFAULT_TIMEOUT_S

    targets = _resolve_test_files(issue.get("files_changed", []), repo_abs)
    if not targets:
        targets = ["tests/"]

    result, _ = _run_pytest(targets, repo_abs, timeout)

    # Skip broader check if the targeted slice already failed (no signal
    # to gain — something's already broken, we know the diff is bad) OR
    # if the targeted slice WAS the full tests/ directory (no point
    # running it twice) OR if broader check is disabled.
    if (
        not result.passed
        or not BROADER_CHECK_ENABLED
        or targets == ["tests/"]
    ):
        return result

    broader_start = time.time()
    broader, _ = _run_pytest(["tests/"], repo_abs, timeout)
    result.broader_ran = True
    result.broader_passed = broader.passed
    result.broader_n_tests = broader.n_tests
    result.broader_n_failed = broader.n_failed
    result.broader_failing = broader.failing_tests
    result.broader_elapsed_s = round(time.time() - broader_start, 3)
    # passed is only True if BOTH checks pass
    result.passed = result.passed and broader.passed
    if not broader.passed and not result.error:
        # Surface the broader failure so traces are diagnosable.
        result.error = (
            f"targeted slice passed but broader tests/ failed: "
            f"{broader.n_failed}/{broader.n_tests}, "
            f"first: {broader.failing_tests[:3]}"
        )
    return result


def _cli():
    """Standalone smoke-test: `python -m harness.oracle <issue_number>`.
    Runs the oracle against whatever state the target repo is currently
    in (does not check out the baseline for you).
    """
    if len(sys.argv) < 2:
        print("Usage: python -m harness.oracle <issue_number>", file=sys.stderr)
        sys.exit(2)
    try:
        issue_num = int(sys.argv[1])
    except ValueError:
        print(f"Invalid issue number: {sys.argv[1]!r}", file=sys.stderr)
        sys.exit(2)

    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "issues.json")
    with open(data_path) as f:
        data = json.load(f)
    issue = next((i for i in data["issues"] if i["number"] == issue_num), None)
    if issue is None:
        print(f"Issue {issue_num} not found in data/issues.json", file=sys.stderr)
        sys.exit(1)

    result = run(issue)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    _cli()
