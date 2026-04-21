"""
memory.py: JSON-backed bullet stores for the coder and reviewer agents.

Two stores live under MEMORY_DIR (default ./memory/):
  - coder_lessons.json: capped, category-tagged "lessons learned"
                             populated only from oracle-passing traces
  - reviewer_rubric.json: calibration cases tagged by the 2x2
                             win/loss outcome from DESIGN.md sec 4.2

Design notes:
  * Items are stored as a flat list with a `tag` field. For the coder
    `tag` is a bug category; for the reviewer it is one of the four
    win/loss outcomes.
  * Each store enforces per-tag capacity. Eviction is by lowest
    `hits - uses` (i.e. retrieved often but never seemed to help)
    with `created_at` as a tie-breaker so the freshest stay.
  * Atomic write via tempfile + rename so a crash mid-update never
    leaves a half-written JSON on disk.
  * The store object itself is *not* multi-process safe; only one
    loop process should write at a time, which matches our usage.

Categorization helper `categorize(issue)` does a coarse keyword
match against the issue title/body and the files_changed list.
This is intentionally heuristic; a learned categorizer is future
work (DESIGN.md sec 9).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass

DEFAULT_PER_TAG_CAP = 8
MEMORY_DIR = os.environ.get(
    "MEMORY_DIR",
    os.path.join(os.path.dirname(__file__), "..", "memory"),
)

REVIEWER_OUTCOMES = ("true_approval", "true_rejection", "false_approval", "false_rejection")


@dataclass
class MemoryItem:
    id: str
    text: str
    tag: str
    source_issue: int
    diff_snippet: str = ""
    created_at: float = 0.0
    uses: int = 0
    hits: int = 0


def _new_item(text: str, tag: str, source_issue: int, diff_snippet: str = "") -> MemoryItem:
    return MemoryItem(
        id=uuid.uuid4().hex[:10],
        text=text.strip(),
        tag=tag,
        source_issue=int(source_issue),
        diff_snippet=diff_snippet[:1500],
        created_at=time.time(),
    )


class _Store:
    """Common JSON-backed bullet store with per-tag eviction."""

    def __init__(self, path: str, per_tag_cap: int = DEFAULT_PER_TAG_CAP):
        self.path = path
        self.per_tag_cap = per_tag_cap
        self._lock = threading.Lock()
        self._items: list[MemoryItem] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        self._items = []
        for d in data.get("items", []):
            try:
                self._items.append(MemoryItem(**d))
            except TypeError:
                continue

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"items": [asdict(i) for i in self._items]}, f, indent=2)
        os.replace(tmp, self.path)

    def all(self) -> list[MemoryItem]:
        return list(self._items)

    def by_tag(self, tag: str) -> list[MemoryItem]:
        return [i for i in self._items if i.tag == tag]

    def add(self, item: MemoryItem) -> None:
        """Insert `item` and evict the lowest-utility item in the same
        tag if the per-tag cap is exceeded.
        """
        with self._lock:
            self._items.append(item)
            same_tag = [i for i in self._items if i.tag == item.tag]
            if len(same_tag) > self.per_tag_cap:
                # Lower (hits - uses) means the item is retrieved but
                # rarely helps; tie-break on older created_at.
                same_tag.sort(key=lambda x: (x.hits - x.uses, x.created_at))
                victim = same_tag[0]
                self._items = [i for i in self._items if i.id != victim.id]
            self._save()

    def record_uses(self, ids: list[str], hit_ids: list[str] | None = None) -> None:
        """Bump `uses` for retrieved IDs and `hits` for those in
        `hit_ids`. Called from distill.py after we know whether the
        oracle passed for the round/issue the items were retrieved for.
        """
        if not ids:
            return
        hit_set = set(hit_ids or [])
        with self._lock:
            for i in self._items:
                if i.id in ids:
                    i.uses += 1
                    if i.id in hit_set:
                        i.hits += 1
            self._save()


class CoderMemory(_Store):
    """Per-category 'lessons learned' for the coder preamble."""

    DEFAULT_PATH = os.path.join(MEMORY_DIR, "coder_lessons.json")

    def __init__(self, path: str | None = None, per_tag_cap: int = DEFAULT_PER_TAG_CAP):
        super().__init__(path or self.DEFAULT_PATH, per_tag_cap=per_tag_cap)

    def render_for(
        self,
        issue_category: str,
        k_in_category: int = 2,
        k_diversity: int = 1,
    ) -> tuple[str, list[str]]:
        """Return (prompt_block, retrieved_ids). Empty prompt when no
        items match. Diversity bullet is enforced from a *different*
        category than `issue_category` to prevent topic collapse
        (DESIGN.md sec 5.3).
        """
        in_cat = sorted(self.by_tag(issue_category), key=lambda i: -(i.hits + 1))[:k_in_category]
        other_sorted = sorted(
            (i for i in self._items if i.tag != issue_category),
            key=lambda i: -(i.hits + 1),
        )
        diversity = other_sorted[:k_diversity]
        chosen = in_cat + diversity
        if not chosen:
            return "", []
        lines = [f"- [{i.tag}] {i.text}" for i in chosen]
        block = "Relevant lessons from past fixes (use as guidance, not gospel):\n" + "\n".join(lines)
        return block, [i.id for i in chosen]


class ReviewerMemory(_Store):
    """Win/loss calibration cases for the reviewer prompt."""

    DEFAULT_PATH = os.path.join(MEMORY_DIR, "reviewer_rubric.json")

    OUTCOME_LABELS = {
        "true_approval":   "WIN: I approved and tests passed",
        "true_rejection":  "WIN: I rejected and tests failed",
        "false_approval":  "LOSS: I approved but tests failed (catch this kind of bug)",
        "false_rejection": "LOSS: I rejected but tests passed (do not over-ask for this)",
    }

    def __init__(self, path: str | None = None, per_tag_cap: int = DEFAULT_PER_TAG_CAP):
        super().__init__(path or self.DEFAULT_PATH, per_tag_cap=per_tag_cap)

    def render(self) -> tuple[str, list[str]]:
        """Up to one example per outcome, freshest first, in win/loss
        table order so the prompt matches the mental model.
        """
        chosen: list[MemoryItem] = []
        for outcome in REVIEWER_OUTCOMES:
            cands = sorted(self.by_tag(outcome), key=lambda i: -i.created_at)
            if cands:
                chosen.append(cands[0])
        if not chosen:
            return "", []
        sections: list[str] = []
        for i in chosen:
            label = self.OUTCOME_LABELS.get(i.tag, i.tag)
            snippet = i.diff_snippet[:600]
            sections.append(
                f"### {label}\n"
                f"  Note: {i.text}\n"
                f"  Diff excerpt:\n  ```diff\n  {snippet}\n  ```"
            )
        block = "Calibration examples from past reviews:\n\n" + "\n\n".join(sections)
        return block, [i.id for i in chosen]


# Coarse keyword categorizer. Categories chosen to match the bug
# distribution in data/issues.json. Returns "general" when nothing
# scores above zero.
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("humanize-boundary",
     ("humanize", "month limit", "boundary", "rounding", "weeks vs months", "month boundary"),
     ("arrow/arrow.py",)),
    ("missing-locale-timeframe",
     ("timeframe", "week granularity", "quarter granularity", "missing", "no support for"),
     ("arrow/locales.py",)),
    ("locale-pluralization",
     ("plural", "russian", "czech", "slovak", "korean"),
     ("arrow/locales.py",)),
    ("parsing-edge-case",
     ("pars", "iso", "token", "yy ", "hh ", "dddd", "ddd ", "format string", "microsecond"),
     ("arrow/parser.py",)),
    ("dst-range-escaping",
     ("dst", "range", "span_range", "escape", "escaping", "tzinfo"),
     ("arrow/arrow.py", "arrow/factory.py")),
)


def categorize(issue: dict) -> str:
    """Return a category label for an issue based on title+body+files."""
    text = (issue.get("title", "") + " " + issue.get("body_summary", "")).lower()
    files = set(issue.get("files_changed", []) or [])
    best_name = "general"
    best_score = 0
    for name, keywords, file_hints in _CATEGORY_RULES:
        score = sum(1 for kw in keywords if kw in text)
        score += sum(1 for f in file_hints if f in files)
        if score > best_score:
            best_score = score
            best_name = name
    return best_name


def make_item(text: str, tag: str, source_issue: int, diff_snippet: str = "") -> MemoryItem:
    """Public helper for distill.py to construct items."""
    return _new_item(text, tag, source_issue, diff_snippet)
