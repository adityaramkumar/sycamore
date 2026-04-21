"""Tests for harness/memory.py — stores, eviction, categorize, render."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.memory import (
    REVIEWER_OUTCOMES,
    CoderMemory,
    ReviewerMemory,
    categorize,
    make_item,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_coder_memory(tmp_path, per_tag_cap: int = 3) -> CoderMemory:
    return CoderMemory(path=str(tmp_path / "coder.json"), per_tag_cap=per_tag_cap)


def _make_reviewer_memory(tmp_path, per_tag_cap: int = 3) -> ReviewerMemory:
    return ReviewerMemory(path=str(tmp_path / "reviewer.json"), per_tag_cap=per_tag_cap)


# ── CoderMemory ───────────────────────────────────────────────────────────────

class TestCoderMemoryAddAndPersist:
    def test_add_and_retrieve(self, tmp_path):
        m = _make_coder_memory(tmp_path)
        item = make_item("Check locales.py for plural forms.", "locale-pluralization", 101)
        m.add(item)
        assert len(m.all()) == 1
        assert m.all()[0].text == "Check locales.py for plural forms."

    def test_persists_across_instances(self, tmp_path):
        path = str(tmp_path / "coder.json")
        m1 = CoderMemory(path=path)
        m1.add(make_item("Lesson A", "general", 1))
        m2 = CoderMemory(path=path)
        assert len(m2.all()) == 1
        assert m2.all()[0].text == "Lesson A"

    def test_eviction_at_cap(self, tmp_path):
        m = _make_coder_memory(tmp_path, per_tag_cap=2)
        for i in range(3):
            item = make_item(f"Lesson {i}", "general", i)
            m.add(item)
        assert len(m.by_tag("general")) == 2

    def test_eviction_prefers_low_utility(self, tmp_path):
        m = _make_coder_memory(tmp_path, per_tag_cap=2)
        # high utility item
        good = make_item("Useful lesson", "general", 1)
        good.hits = 5
        good.uses = 1
        m.add(good)
        # low utility item (never helped)
        bad = make_item("Useless lesson", "general", 2)
        bad.hits = 0
        bad.uses = 5
        m.add(bad)
        # Adding a third should evict the lowest (hits - uses) = bad
        new = make_item("New lesson", "general", 3)
        m.add(new)
        texts = {i.text for i in m.by_tag("general")}
        assert "Useless lesson" not in texts
        assert "Useful lesson" in texts

    def test_eviction_only_within_tag(self, tmp_path):
        m = _make_coder_memory(tmp_path, per_tag_cap=2)
        for i in range(2):
            m.add(make_item(f"cat-a {i}", "cat-a", i))
        for i in range(2):
            m.add(make_item(f"cat-b {i}", "cat-b", i + 10))
        assert len(m.by_tag("cat-a")) == 2
        assert len(m.by_tag("cat-b")) == 2

    def test_by_tag_filters(self, tmp_path):
        m = _make_coder_memory(tmp_path)
        m.add(make_item("A", "parsing-edge-case", 1))
        m.add(make_item("B", "humanize-boundary", 2))
        assert len(m.by_tag("parsing-edge-case")) == 1
        assert len(m.by_tag("humanize-boundary")) == 1


class TestCoderMemoryRenderFor:
    def test_empty_store_returns_empty(self, tmp_path):
        m = _make_coder_memory(tmp_path)
        block, ids = m.render_for("general")
        assert block == ""
        assert ids == []

    def test_in_category_items_included(self, tmp_path):
        m = _make_coder_memory(tmp_path)
        m.add(make_item("Lesson for humanize", "humanize-boundary", 1))
        block, ids = m.render_for("humanize-boundary")
        assert "Lesson for humanize" in block
        assert len(ids) == 1

    def test_diversity_item_from_different_category(self, tmp_path):
        m = _make_coder_memory(tmp_path)
        m.add(make_item("Lesson A", "humanize-boundary", 1))
        m.add(make_item("Lesson B", "locale-pluralization", 2))
        block, ids = m.render_for("humanize-boundary")
        assert "Lesson B" in block  # diversity item from other category
        assert len(ids) == 2

    def test_capped_at_k_in_category(self, tmp_path):
        m = _make_coder_memory(tmp_path, per_tag_cap=5)
        for i in range(4):
            m.add(make_item(f"Lesson {i}", "general", i))
        block, ids = m.render_for("general", k_in_category=2)
        # Should include only 2 in-category items
        assert sum(1 for i in m.all() if i.id in ids and i.tag == "general") <= 2


class TestCoderMemoryRecordUses:
    def test_record_uses_bumps_count(self, tmp_path):
        m = _make_coder_memory(tmp_path)
        item = make_item("A lesson", "general", 1)
        m.add(item)
        m.record_uses([item.id])
        assert m.all()[0].uses == 1
        assert m.all()[0].hits == 0

    def test_record_uses_with_hit(self, tmp_path):
        m = _make_coder_memory(tmp_path)
        item = make_item("A lesson", "general", 1)
        m.add(item)
        m.record_uses([item.id], hit_ids=[item.id])
        assert m.all()[0].uses == 1
        assert m.all()[0].hits == 1


# ── ReviewerMemory ────────────────────────────────────────────────────────────

class TestReviewerMemory:
    def test_add_and_render_one_outcome(self, tmp_path):
        m = _make_reviewer_memory(tmp_path)
        item = make_item("Approved correctly", "true_approval", 50)
        m.add(item)
        block, ids = m.render()
        assert "true_approval" in block.lower() or "WIN: I approved" in block
        assert len(ids) == 1

    def test_render_all_four_outcomes(self, tmp_path):
        m = _make_reviewer_memory(tmp_path)
        for outcome in REVIEWER_OUTCOMES:
            m.add(make_item(f"note for {outcome}", outcome, 1))
        block, ids = m.render()
        assert len(ids) == 4
        for outcome in REVIEWER_OUTCOMES:
            assert outcome in block or ReviewerMemory.OUTCOME_LABELS[outcome][:20] in block

    def test_render_freshest_first(self, tmp_path):
        m = _make_reviewer_memory(tmp_path)
        old = make_item("Old case", "true_approval", 1)
        old.created_at = 1000.0
        m.add(old)
        new = make_item("New case", "true_approval", 2)
        new.created_at = 9999.0
        m.add(new)
        block, _ = m.render()
        assert "New case" in block

    def test_empty_store_returns_empty(self, tmp_path):
        m = _make_reviewer_memory(tmp_path)
        block, ids = m.render()
        assert block == ""
        assert ids == []


# ── categorize ────────────────────────────────────────────────────────────────

class TestCategorize:
    def _issue(self, title: str, body: str = "", files: list | None = None) -> dict:
        return {"title": title, "body_summary": body, "files_changed": files or []}

    def test_humanize_boundary(self):
        issue = self._issue("humanize shows month instead of weeks", files=["arrow/arrow.py"])
        assert categorize(issue) == "humanize-boundary"

    def test_locale_pluralization(self):
        issue = self._issue("Russian plural forms are wrong", files=["arrow/locales.py"])
        assert categorize(issue) == "locale-pluralization"

    def test_parsing_edge_case(self):
        issue = self._issue("ISO 8601 parsing fails for some tokens", files=["arrow/parser.py"])
        assert categorize(issue) == "parsing-edge-case"

    def test_dst(self):
        issue = self._issue("DST range escaping broken", files=["arrow/arrow.py"])
        assert categorize(issue) == "dst-range-escaping"

    def test_general_fallback(self):
        issue = self._issue("Some unrelated issue with no matching keywords")
        assert categorize(issue) == "general"

    def test_missing_locale_timeframe(self):
        issue = self._issue("Missing quarter granularity in timeframe", files=["arrow/locales.py"])
        assert categorize(issue) == "missing-locale-timeframe"


# ── make_item ─────────────────────────────────────────────────────────────────

class TestMakeItem:
    def test_fields_populated(self):
        item = make_item("A lesson", "general", 42, diff_snippet="+ some code")
        assert item.text == "A lesson"
        assert item.tag == "general"
        assert item.source_issue == 42
        assert item.diff_snippet == "+ some code"
        assert item.id != ""
        assert item.created_at > 0

    def test_diff_snippet_truncated(self):
        item = make_item("X", "general", 1, diff_snippet="x" * 2000)
        assert len(item.diff_snippet) <= 1500

    def test_text_stripped(self):
        item = make_item("  lesson with spaces  ", "general", 1)
        assert item.text == "lesson with spaces"
