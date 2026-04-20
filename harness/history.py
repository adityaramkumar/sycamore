"""
history.py: Retrieve relevant past commits from pre-baseline git log.

Gives the coder cheap access to the target repo's history of similar
fixes without requiring any LLM call. Strictly bounded to commits at
or before the pinned baseline, so our 25 eval issues' fix commits
are invisible by construction (verified: 0/25 ancestors of c9cecaf).

Motivation: the per-issue coder/reviewer memories are inductive.
they accumulate only from traces we produce in our own loop. arrow
has ~7 years / ~800 commits of pre-baseline history that are a much
richer source of concrete fix patterns than our first few traces.
This module is RAG-over-git-log.

Retrieval strategy (intentionally simple):
  1. Extract discriminating keywords from issue title + body.
  2. For each file in issue.files_changed that exists at baseline,
     list commits that touched it within the baseline scope. This
     scope-by-file is the strongest signal.
  3. For each candidate commit, score = keyword matches in subject
     + file-overlap bonus. Keep top-k.
  4. Render as a compact prompt block with optional top-1 diff excerpt.

No LLM calls. Pure git + string. ~100ms per issue.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

DEFAULT_K = 3
MAX_DIFF_EXCERPT_CHARS = 1500
MAX_SUBJECT_CHARS = 110
COMMITS_PER_FILE = 12  # per-file search breadth

# Stopwords common in bug-report prose AND generic commit-message filler.
# Words that match too many irrelevant commits hurt retrieval more than
# they help, so we filter them before grep.
_STOPWORDS = frozenset({
    "a", "an", "and", "or", "but", "the", "in", "on", "at", "to", "for",
    "of", "with", "by", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "as", "from",
    "not", "no", "do", "does", "did", "doing", "have", "has", "had",
    "should", "would", "could", "can", "will", "may", "might",
    "bug", "fix", "fixes", "fixed", "error", "fail", "fails", "failed",
    "issue", "result", "results", "wrong", "incorrect", "correct",
    "instead", "expected", "actual", "because", "due",
    "when", "where", "why", "how", "what", "which", "who",
    "return", "returns", "returned", "returning",
    "raise", "raises", "raised", "raising",
    # Generic commit-message filler. Matching on these pulls in "update
    # locales.py" / "add support for X" / "improve Y" for everything.
    "add", "added", "adds", "adding", "remove", "removed", "removes",
    "removing", "update", "updated", "updates", "updating",
    "change", "changed", "changes", "changing", "improve", "improved",
    "improves", "improving", "improvement", "support", "supports",
    "supported", "supporting", "use", "uses", "used", "using",
    "make", "makes", "made", "making", "allow", "allows", "allowed",
    "new", "old", "longer", "shorter", "different", "implemented",
    "implement", "implements", "implementation", "broken", "also",
    "still", "only", "there", "here", "now", "then",
})

_WORD_RE = re.compile(r"[a-z_][a-z_0-9]{2,}")


@dataclass
class HistoricalCommit:
    sha: str
    date: str
    subject: str
    files: list[str] = field(default_factory=list)
    score: int = 0
    diff_excerpt: str = ""

    def short(self) -> str:
        return self.sha[:8]


def _extract_keywords(issue: dict, max_n: int = 8) -> list[str]:
    """Discriminating keywords from title + body, plus file basenames."""
    text = " ".join([
        str(issue.get("title", "")),
        str(issue.get("body_summary", "")),
    ]).lower()
    tokens = _WORD_RE.findall(text)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in _STOPWORDS or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_n:
            break
    # File basenames without .py are strong category signals.
    for f in issue.get("files_changed", []) or []:
        base = os.path.basename(str(f))
        if base.endswith(".py"):
            base = base[:-3]
        if base.endswith("_tests"):
            base = base[:-6]
        if base.startswith("test_"):
            base = base[5:]
        if base and base not in seen:
            seen.add(base)
            out.append(base)
    return out


def _safe_paths(issue: dict, repo_abs: str) -> list[str]:
    """Return issue.files_changed entries that currently exist on disk.

    Uses `git ls-files` on the target path so we respect the state of
    HEAD. Missing paths are silently skipped; they may not exist at
    baseline (new-style test file names for legacy commits, etc.).
    """
    candidates: list[str] = []
    for f in issue.get("files_changed", []) or []:
        if os.path.exists(os.path.join(repo_abs, f)):
            candidates.append(f)
    # If nothing mapped, fall back to the canonical source files so we
    # still get *some* history signal instead of empty results.
    if not candidates:
        for f in ("arrow/arrow.py", "arrow/locales.py", "arrow/parser.py"):
            if os.path.exists(os.path.join(repo_abs, f)):
                candidates.append(f)
    return candidates


def _git(args: list[str], repo_abs: str, timeout: int = 15) -> str:
    """Run a git command with a short timeout; return stdout ('' on failure)."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=repo_abs,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout


def _log_for_file(baseline: str, path: str, repo_abs: str) -> list[tuple[str, str, str]]:
    """List (sha, date, subject) for commits touching `path` within baseline."""
    fmt = "%H\x1f%ad\x1f%s"
    out = _git(
        ["log", baseline, f"--max-count={COMMITS_PER_FILE}", f"--pretty=format:{fmt}",
         "--date=short", "--", path],
        repo_abs,
    )
    rows: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _files_for_sha(sha: str, repo_abs: str) -> list[str]:
    out = _git(["show", "--no-patch", "--name-only", "--pretty=format:", sha], repo_abs)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _diff_for_sha(sha: str, repo_abs: str, max_chars: int) -> str:
    out = _git(["show", "--no-color", "--stat", "--patch", sha], repo_abs)
    return out[:max_chars]


def _score(subject: str, files: list[str], keywords: list[str],
           issue_files: set[str]) -> tuple[int, int, int]:
    """Return (keyword_hits, file_overlap, composite_score).

    Returning keyword_hits separately lets the caller enforce a
    "must have semantic overlap" filter, not just file overlap.
    """
    subj_low = subject.lower()
    keyword_hits = sum(1 for kw in keywords if kw in subj_low)
    file_overlap = sum(1 for f in files if f in issue_files)
    composite = keyword_hits * 3 + file_overlap * 2
    return keyword_hits, file_overlap, composite


def retrieve_similar_fixes(
    issue: dict,
    repo_abs: str,
    baseline: str,
    k: int = DEFAULT_K,
    forbidden_shas: set[str] | None = None,
) -> list[HistoricalCommit]:
    """Return up to k pre-baseline commits likely relevant to `issue`.

    `forbidden_shas`: commits we must NEVER return (typically the 25
    eval issues' fix_commits). Defense-in-depth against accidental leak
    even though baseline-scope already makes them unreachable.
    """
    keywords = _extract_keywords(issue)
    safe_paths = _safe_paths(issue, repo_abs)
    issue_files = set(issue.get("files_changed", []) or [])
    forbidden = forbidden_shas or set()

    # Gather candidate (sha, date, subject) tuples, deduped by sha.
    candidates: dict[str, tuple[str, str]] = {}
    for path in safe_paths:
        for sha, date, subject in _log_for_file(baseline, path, repo_abs):
            if sha in forbidden:
                continue
            if sha not in candidates:
                candidates[sha] = (date, subject)

    # Score and rank. Fetch files-touched only for top candidates to
    # keep git calls bounded.
    scored = []
    for sha, (date, subject) in candidates.items():
        # Cheap pre-score from subject alone before paying for files_for_sha.
        pre = sum(1 for kw in keywords if kw in subject.lower())
        scored.append((pre, sha, date, subject))
    scored.sort(key=lambda t: -t[0])
    top_candidates = scored[: k * 4]  # over-fetch, re-score with files

    results: list[HistoricalCommit] = []
    for _, sha, date, subject in top_candidates:
        files = _files_for_sha(sha, repo_abs)
        kw_hits, _file_hits, composite = _score(subject, files, keywords, issue_files)
        # Require at least 1 keyword hit in the subject. Pure file-overlap
        # matches (e.g. "Python 3.7 support" touching arrow/arrow.py) are
        # structurally similar but semantically unrelated, and lowering
        # the retrieval quality hurts the coder more than it helps.
        if kw_hits < 1:
            continue
        results.append(HistoricalCommit(
            sha=sha,
            date=date,
            subject=subject[:MAX_SUBJECT_CHARS],
            files=files,
            score=composite,
        ))

    results.sort(key=lambda c: -c.score)
    results = results[:k]

    if results:
        # Attach diff excerpt to top-1 only.
        results[0].diff_excerpt = _diff_for_sha(
            results[0].sha, repo_abs, MAX_DIFF_EXCERPT_CHARS
        )

    return results


def render_block(commits: list[HistoricalCommit]) -> str:
    """Format retrieved commits as a coder prompt preamble."""
    if not commits:
        return ""
    lines: list[str] = [
        "Past fixes from this repo's history that look related "
        "(baseline-scoped; use as concrete examples, not copy-paste):"
    ]
    for c in commits:
        file_bits = ", ".join(c.files[:4]) + ("" if len(c.files) <= 4 else f", ...(+{len(c.files)-4})")
        lines.append(f"- {c.short()} {c.date}  {c.subject}")
        if file_bits:
            lines.append(f"    files: {file_bits}")
    if commits and commits[0].diff_excerpt:
        lines.append("")
        lines.append(f"Excerpt of the most-similar fix ({commits[0].short()}):")
        lines.append("```diff")
        lines.append(commits[0].diff_excerpt.strip())
        lines.append("```")
    return "\n".join(lines)


def retrieved_sha_list(commits: list[HistoricalCommit]) -> list[str]:
    """For trace logging."""
    return [c.sha for c in commits]


def _cli() -> None:
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m harness.history <issue_number>", file=sys.stderr)
        sys.exit(2)
    issue_num = int(sys.argv[1])
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "issues.json")
    with open(data_path) as f:
        data = json.load(f)
    issue = next((i for i in data["issues"] if i["number"] == issue_num), None)
    if issue is None:
        print(f"Issue {issue_num} not found", file=sys.stderr)
        sys.exit(1)
    baseline = data["baseline_commit"]
    repo = os.path.abspath(os.environ.get("TARGET_REPO_PATH", "./arrow"))
    forbidden = {i["fix_commit"] for i in data["issues"]}
    commits = retrieve_similar_fixes(issue, repo, baseline, forbidden_shas=forbidden)
    block = render_block(commits)
    print(f"Retrieved {len(commits)} commit(s) for issue #{issue_num}:\n")
    print(block or "(no matches)")
    print("\n---")
    print(f"SHAs: {retrieved_sha_list(commits)}")


if __name__ == "__main__":
    _cli()
