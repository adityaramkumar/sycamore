"""Tests for harness/history.py — keyword extraction, scoring, rendering."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.history import (
    HistoricalCommit,
    _extract_keywords,
    _score,
    render_block,
    retrieved_sha_list,
)

# ── _extract_keywords ─────────────────────────────────────────────────────────

class TestExtractKeywords:
    def _issue(self, title: str, body: str = "", files: list | None = None) -> dict:
        return {"title": title, "body_summary": body, "files_changed": files or []}

    def test_basic_extraction(self):
        issue = self._issue("humanize boundary month", body="")
        kws = _extract_keywords(issue)
        assert "humanize" in kws
        assert "boundary" in kws
        assert "month" in kws

    def test_stopwords_excluded(self):
        issue = self._issue("fix the bug in the and or but")
        kws = _extract_keywords(issue)
        for word in ("fix", "the", "bug", "and", "or", "but"):
            assert word not in kws, f"stopword '{word}' leaked into keywords"

    def test_file_basenames_included(self):
        issue = self._issue("some issue", files=["arrow/locales.py"])
        kws = _extract_keywords(issue)
        assert "locales" in kws

    def test_test_file_basename_stripped(self):
        issue = self._issue("some issue", files=["tests/arrow_tests.py"])
        kws = _extract_keywords(issue)
        assert "arrow" in kws
        assert "tests" not in kws

    def test_max_n_respected(self):
        long_title = " ".join(f"uniqueword{i}" for i in range(20))
        issue = self._issue(long_title)
        kws = _extract_keywords(issue, max_n=5)
        assert len(kws) <= 5 + 1  # +1 for possible file basename (none here)

    def test_min_word_length(self):
        # Words with fewer than 3 chars should not match the regex (a-z][a-z0-9]{2,})
        issue = self._issue("ab cd ef gh")
        kws = _extract_keywords(issue)
        assert kws == []

    def test_deduplication(self):
        issue = self._issue("humanize humanize humanize")
        kws = _extract_keywords(issue)
        assert kws.count("humanize") == 1


# ── _score ────────────────────────────────────────────────────────────────────

class TestScore:
    def test_keyword_hit(self):
        kw_hits, _, composite = _score(
            subject="fix humanize boundary rounding",
            files=["arrow/arrow.py"],
            keywords=["humanize", "boundary"],
            issue_files={"arrow/locales.py"},
        )
        assert kw_hits == 2
        assert composite >= 6  # 2 keyword hits * 3

    def test_file_overlap_bonus(self):
        _, file_hits, composite = _score(
            subject="update arrow",
            files=["arrow/arrow.py"],
            keywords=["humanize"],
            issue_files={"arrow/arrow.py"},
        )
        assert file_hits == 1
        assert composite >= 2  # file overlap * 2

    def test_no_match(self):
        kw_hits, file_hits, composite = _score(
            subject="unrelated commit",
            files=["other.py"],
            keywords=["humanize"],
            issue_files={"arrow/arrow.py"},
        )
        assert kw_hits == 0
        assert file_hits == 0
        assert composite == 0


# ── render_block ──────────────────────────────────────────────────────────────

class TestRenderBlock:
    def _commit(self, sha: str = "abcdef1234567890", subject: str = "fix bug") -> HistoricalCommit:
        return HistoricalCommit(
            sha=sha,
            date="2020-01-01",
            subject=subject,
            files=["arrow/arrow.py"],
            score=5,
        )

    def test_empty_list_returns_empty_string(self):
        assert render_block([]) == ""

    def test_single_commit_included(self):
        c = self._commit(sha="abcdef1234567890", subject="fix humanize")
        block = render_block([c])
        assert "abcdef12" in block
        assert "fix humanize" in block

    def test_multiple_commits(self):
        commits = [
            self._commit("aaaa1234bbbb5678", "fix locale"),
            self._commit("cccc1234dddd5678", "fix parser"),
        ]
        block = render_block(commits)
        assert "fix locale" in block
        assert "fix parser" in block

    def test_diff_excerpt_shown_for_top_commit(self):
        c = self._commit()
        c.diff_excerpt = "--- a/arrow/arrow.py\n+++ b/arrow/arrow.py\n@@ -1 +1 @@\n-old\n+new"
        block = render_block([c])
        assert "```diff" in block
        assert "+new" in block

    def test_no_diff_excerpt_for_second_commit(self):
        c1 = self._commit("aaaa1234bbbb5678", "top commit")
        c1.diff_excerpt = "some diff"
        c2 = self._commit("cccc1234dddd5678", "second commit")
        c2.diff_excerpt = ""
        block = render_block([c1, c2])
        assert block.count("```diff") == 1

    def test_many_files_truncated_in_display(self):
        c = self._commit()
        c.files = [f"arrow/file{i}.py" for i in range(10)]
        block = render_block([c])
        assert "..." in block


# ── retrieved_sha_list ────────────────────────────────────────────────────────

def test_retrieved_sha_list_empty():
    assert retrieved_sha_list([]) == []


def test_retrieved_sha_list_extracts_shas():
    commits = [
        HistoricalCommit(sha="abc123", date="2020", subject="fix"),
        HistoricalCommit(sha="def456", date="2020", subject="feat"),
    ]
    assert retrieved_sha_list(commits) == ["abc123", "def456"]
