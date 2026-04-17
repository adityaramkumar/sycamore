# Presentation Deck

One slide per section. Each slide has:

- **Title**: usually a takeaway sentence, not a topic label.
- **On slide**: minimal text, 4 bullets max, ~6 words each.
- **Visual**: what to draw, screenshot, or chart. Most are doable with Google Slides shapes, Google Sheets charts, or a screenshot.
- **Say**: speaker notes. Carry the content that isn't on the slide.

23 slides, about 18 to 22 minutes. Slide 17 (Phase D) is your strongest moment; if you need to cut, merge 2+3 and 9+10.

Design principles used here:

- Audience should listen, not read. Text on the slide should fit in 5 seconds.
- One visual per slide, centered, fills most of the space.
- Titles do storytelling work ("The reviewer was the bottleneck") rather than labeling work ("Reviewer analysis").
- If a chart and text both fit, drop the text.

---

## Slide 1. Title

**On slide:**

- Co-Optimizing an AI Coder and an AI Reviewer
- Your name | date

**Visual:**
Simple icon graphic: two robots (or speech bubbles) facing each other, one labeled "Coder", one labeled "Reviewer". Below them a small icon of a test tube or green checkmark labeled "Oracle". No other text.

**Say:**
Today I'm walking through a project where two AI helpers, one that writes code and one that reviews it, learn from each other using their past conversations. I'll show you what I built, what I measured, and one big surprise.

---

## Slide 2. Two AIs talking to each other

**On slide:**

- Coder writes a fix.
- Reviewer approves or asks for changes.
- They iterate until done.
- Today, neither learns from the conversation.

**Visual:**
A simple horizontal flow with three boxes: `Issue -> Coder -> Reviewer -> (approve | revise)`. Use arrows. A small loop arrow from Reviewer back to Coder labeled "feedback".

**Say:**
Picture a junior engineer and a senior engineer working on a bug. Junior writes a fix, senior reviews, they iterate. That's exactly how two AI helpers work too. The interesting question: can we use the transcripts of those conversations to make both AIs better over time?

---

## Slide 3. The trap: they can agree on nonsense

**On slide:**

- If the Reviewer is our only grader, the Coder learns to please it.
- Not to actually fix the bug.
- We need an outside judge.

**Visual:**
A Venn diagram with two circles: "Reviewer approves" and "Tests actually pass". The overlap is tiny. Label the non-overlapping "Reviewer approves" part with a question mark.

**Say:**
Here's the subtle part. If the only grade we give the Coder is the Reviewer's thumbs up, the Coder can learn to game the Reviewer. They can happily agree with each other while the code is still broken. We need an outside judge that doesn't care about their feelings.

---

## Slide 4. 25 real bugs, 1 small library

**On slide:**

- `arrow-py/arrow`: Python date/time library.
- 25 real historical bugs.
- Pinned to a commit before any were fixed.

**Visual:**
Screenshot of the arrow GitHub repo or a highlighted GitHub issue (issue #1240 is a good one: "humanize reports '16 days' as 'a month'"). Alternative: arrow logo + three stats: "25 bugs", "800 commits", "2019 baseline".

**Say:**
We needed a real codebase with real bugs. arrow is a small Python library for dates and times with a long history of closed bug reports on GitHub. We pinned it to an old commit before the bugs were fixed, and pointed our AI helpers at 25 of them.

---

## Slide 5. What is an interaction trace?

**On slide:**

```
issue + [(fix_1, review_1), (fix_2, review_2), ...]
```

Just the conversation, saved as JSON.

**Visual:**
Show an actual trace snippet. Use a short real one:

```json
{
  "issue_number": 815,
  "rounds": [
    { "round": 1, "diff": "...", "approved": false,
      "comments": ["add a test for Czech week"] },
    { "round": 2, "diff": "...", "approved": true }
  ]
}
```

Highlight "approved" in each round with a red/green color.

**Say:**
Every time the two AIs talk, we save the whole conversation to a file. We call it a trace. The whole exercise is basically: can we learn enough from these traces to make the next run better?

---

## Slide 6. The system in one picture

**On slide:** *(no bullets, diagram only)*

**Visual:**
The architecture diagram from `docs/DESIGN.md`. Screenshot the mermaid graph. Highlight with a red box or arrow the **"HIDDEN from reviewer"** edge from Oracle to Trace Store, and add a callout: "this is the one thing neither AI can fool".

**Say:**
Five pieces. An Oracle that runs real pytest tests and is the one thing nobody can fool. A git-history retrieval that shows both agents similar past fixes from this repo. A small notebook for each agent with distilled lessons. A distillation step that fills those notebooks after each issue. And safety rails on top.

---

## Slide 7. The Oracle: our teacher

**On slide:**

- Runs real pytest tests on every fix.
- Result is **hidden from the Reviewer**.
- Can't be fooled by a fast-talking AI.

**Visual:**
Three boxes: the Coder's fix goes to both the Reviewer (left) and the Oracle (right). Over the Oracle, draw a lock icon. Label: "hidden from reviewer". The Reviewer outputs "approve/reject", the Oracle outputs "pass/fail".

**Say:**
This is the single most important piece. Tests don't lie. They just run and pass or fail. We hide their result from the Reviewer on purpose. We want the Reviewer to have its own opinion, and then we compare that opinion to the Oracle afterward.

---

## Slide 8. Experimental setup

**On slide:**

| | FULL | ABLATE |
|---|---|---|
| Git history | on | off |
| Memory | on | off |
| Distillation | on | off |

Same model, same bugs, same seed.

**Visual:**
The table above, rendered clean. Next to it, a row of 5 little bug icons (for 5 issues) with arrows into FULL and ABLATE boxes.

**Say:**
Standard ablation study. Two versions of the system. One has all the features turned on. One has them all turned off. Same AI model, same bugs, same random seed. Only difference is the features we're testing.

---

## Slide 9. Metrics: what we measure

**On slide:**

- `test_pass_rate`: did it actually work? (headline)
- `reviewer_precision`: when the Reviewer says yes, is it right?
- `reviewer_recall`: does the Reviewer recognize good code?
- `balance_gap`: do the two AIs quietly agree on something wrong?

**Visual:**
Dashboard mockup. One big number on the left (test_pass_rate, like "100%"). Four smaller boxes on the right with mini labels (precision, recall, fpr, balance_gap). Use green/red fill to show "good" vs "bad" ranges.

**Say:**
The headline metric is oracle-grounded. The reviewer can't inflate it. The three smaller ones let us blame the right AI when something goes wrong. The last one is a watchdog for the "two AIs agree on nonsense" failure.

---

## Slide 10. Why this set is good

**On slide:**

- **One** metric grounded in reality.
- **Per-agent** breakdown tells us who's at fault.
- **Watchdog** catches pathologies.

**Visual:**
Pyramid or funnel diagram. Wide bottom = noisy signals (approval rate, diff size, comments). Narrow top = the one thing we trust (test_pass_rate). Arrow labeled "ground truth".

**Say:**
A common trap in ML evals is measuring the wrong thing. If we'd used raw approval rate, the Reviewer could approve everything and we'd look like we're winning. Grounding in tests makes it ungameable. The per-agent breakdown is what let me catch the surprise I'll show in a few slides.

---

## Slide 11. How we parallelized

**On slide:**

- 10 copies of the repo, one per worker.
- Wall time: 60 min sequential, 20 min parallel.

**Visual:**
Side-by-side comparison:
- Left: a single horizontal bar labeled "Sequential: ~60 min" broken into 10 colored segments (one per issue).
- Right: 10 stacked short bars labeled "Parallel: ~20 min", all starting at 0.
Label the gap: "3x speedup".

**Say:**
Running 10 bugs one at a time takes about an hour. To speed that up I used git worktrees, which are 10 checked-out copies of the same repo. Each worker ran in its own copy so they didn't step on each other. Ten workers in parallel cut wall time by about 3x.

---

## Slide 12. Phase A: integration test (Haiku)

**On slide:**

- 3 bugs.
- Did the plumbing work? Yes.
- Caught 2 bugs in my own system.

**Visual:**
A split panel:
- Left: green checkmark and "Pipeline works".
- Right: two red bug icons labeled "Coder cheated with git checkout" and "Empty rounds miscounted".

**Say:**
The first run was mostly about finding bugs in my own system, not bugs in arrow. And it did. My Coder was cheating with its shell access. My metrics were miscounting empty rounds. Both got fixed before the real measurement.

---

## Slide 13. Phase B: the ablation

**On slide:**

- Both arms: 100% bugs fixed.
- FULL slightly faster (1.2 rounds vs 1.4).
- But look at reviewer recall.

**Visual:**
Grouped bar chart. X-axis: 4 metrics (`test_pass_rate`, `rounds_to_pass`, `precision`, `recall`). Y-axis: value. Two bars per metric: FULL (blue) and ABLATE (gray).
- `test_pass_rate`: both 100%
- `rounds_to_pass`: 1.2 vs 1.4
- `precision`: both 100%
- **`recall`: 50% vs 57%, both short.** Highlight this column.

**Say:**
Phase B was the real measurement. On the headline, both arms fixed all 5 bugs. Looked boring. But the per-agent breakdown told a different story. Reviewer recall was only around half. Half the time the Coder wrote code that actually passed tests, the Reviewer rejected it anyway. That's a big finding the headline alone would have hidden.

---

## Slide 14. One bug up close: issue 1240

**On slide:**

- Bug: `humanize(16 days)` should say "2 weeks", not "a month".

| Round | Diff | Oracle | Reviewer |
|---|---|---|---|
| 1 | 821 chars | **PASS** | **REJECT** (28 comments) |
| 2 | 821 chars | PASS (cached) | REJECT |
| 3 | 821 chars | PASS (cached) | REJECT |

**Visual:**
Screenshot of a real diff (arrow's fix for 1240 is public). Next to it, an email-or-comment bubble with the Reviewer's first few rejection comments. Big red X next to "Approved?" but a big green check next to "Tests pass?"

**Say:**
This is the clearest example of the problem. The Coder wrote a fix. The Oracle said it worked. The Reviewer rejected it three times in a row, asking for more edge cases and more tests. If I'd only had the Reviewer's verdict, I'd have concluded the Coder was bad. With the Oracle, I could see the Coder was fine and the Reviewer was the picky one.

---

## Slide 15. The surprise: Reviewer is too picky

**On slide:**

- **7 of 15 rounds: good fix, rejected anyway.**
- Precision 100%, recall 50%.

**Visual:**
A 2x2 confusion matrix:

|  | **Tests PASS** | **Tests FAIL** |
|---|---|---|
| **Reviewer approves** | 5 (true approval) | 0 |
| **Reviewer rejects** | **7 (false rejection, BIG)** | 3 (true rejection) |

Highlight the 7 in red. Big label next to it: "the problem".

**Say:**
So the Reviewer is too picky. Not lazy, not dishonest. Nitpicky. It kept asking for more tests even when the fix worked. Interestingly, the real arrow fixes usually include tests too, so the Reviewer's instinct was reasonable. The problem was the Coder wasn't meeting it.

---

## Slide 16. Phase C: fix the Coder, not the Reviewer

**On slide:**

- Tell the Coder to write tests up front.
- Recall jumped 50% to 67%.

**Visual:**
Two bars showing `reviewer_recall`:
- Before (Phase B): 50%
- After (Phase C): 66.7%
Arrow pointing up, labeled "+16.7".

Under the chart: one line showing the root-cause reasoning in a flow: `reviewer asks for tests -> make coder write tests -> reviewer has nothing to ask about`.

**Say:**
Instead of telling the Reviewer "approve without tests" (wrong layer), I changed the Coder's instructions to write a regression test alongside the fix. Reviewer recall improved right away. Issue 815 went from 2 rounds to 1 round because the Coder brought the test the Reviewer would have asked for.

---

## Slide 17. Phase D: the surprise that came back

**On slide:**

- Sequential run, memory accumulates across issues.
- Memory helped. **But test_pass_rate dropped to 50%.**
- The Coder writes tests that fail on its own fix.

**Visual:**
Headline chart. Line showing `test_pass_rate` over the four phases:
- Phase A: 67%
- Phase B: 100%
- Phase C: 100%
- Phase D: **50%** (drop)
Next to the Phase D point, a caption: "Coder tests its own homework".

**Say:**
Phase D was the most interesting result because it made things worse. For the first time I ran sequentially, so memory could accumulate. Memory did help the Reviewer calibrate. But writing tests opened a new hole: sometimes the Coder writes a broken test. The Oracle catches the failing test, but the Reviewer, which doesn't actually run code, approves anyway. Classic "coder tests its own homework". The metric pipeline caught it without me looking for it, which is the whole point.

---

## Slide 18. What worked

**On slide:**

- The Oracle.
- Git history for both AIs.
- Memory that actually accumulated.
- Directional alerts.
- Parallel worktrees.

**Visual:**
Five green checkmarks in a row or column, each with a short label. Could use emoji icons: (oracle) test tube, (git history) branches, (memory) notebook, (alerts) siren, (parallel) lightning bolt.

**Say:**
A lot worked. The Oracle is what made everything else measurable. Git history helped both agents see the repo's patterns. Memory really did accumulate when we ran sequentially. And the directional alerts correctly named every pathology we saw.

---

## Slide 19. What didn't work the first time

**On slide:**

- Fixed over-asking from the wrong side.
- Anti-flailing rules too aggressive.
- Coder writes broken tests (Phase D).

**Visual:**
Three items with red X marks. Next to each, a smaller "->" with a green arrow showing the later fix. Example:
- Told Reviewer "don't ask for tests" -> made Coder write tests instead
- Killed Sonnet's exploration at turn 12 -> raised budget
- Coder's own tests sometimes fail -> **still open**

**Say:**
Plenty didn't work the first time. The "make the Reviewer less picky" approach swung it toward rubber-stamp. The Coder-writes-tests fix worked but introduced Phase D's broken-test problem. Each fix made the next problem visible, which is how engineering usually goes.

---

## Slide 20. Safety rails that caught themselves

**On slide:**

- Reviewer freeze.
- Held-out bugs.
- Asymmetric information.
- HEAD drift check.
- Empty-diff filter.

**Visual:**
5 small icons in a grid (or a row). Each icon represents one rail, with a small green check next to it for "fired correctly during development". Can use shield icons in Google Slides.

**Say:**
I built a bunch of guardrails up front. Several fired during development, which is how I know they work. The HEAD check caught the Coder cheating. The empty-diff filter caught a metric bug. Phase D's Reviewer freeze fired with the correctly-named directional reason. Guardrails that never fire are theater; these earned their keep.

---

## Slide 21. What's next

**On slide:**

Top priority:
- Run Coder's tests on the pre-fix code.

Stretch:
- Value-function head (AlphaGo-style).
- Cross-repo transfer.

**Visual:**
A vertical priority list. The top item in a red box labeled "next 2 weeks". Below it, a couple of stretch items in gray labeled "2 months". Arrow pointing forward or timeline icon.

**Say:**
The top item is specifically for the Phase D defect. If we run the Coder's own test against the pre-fix code and check that it fails, we catch broken tests structurally. Thirty lines. Then the usual stretches: a value function, cross-repo transfer. But the next commit is the oracle-side check.

---

## Slide 22. Summary

**On slide:** *(big font, no bullets)*

> Two AIs teach each other.
> Real tests keep them honest.
> Every fix revealed the next one.

**Visual:**
Just the three lines above, centered, large font. Maybe a small closing icon at the bottom (two gears interlocking, or the Coder-Reviewer-Oracle trio from slide 1 redrawn).

**Say:**
The one-sentence version: we built a way for two AIs to teach each other, grounded by real tests that keep them honest. Every defect we fixed made the next one visible. For me the main lesson is that you cannot trust the agents grading each other. You need an outside judge that doesn't care about their feelings, and you need metrics that decompose which AI is at fault when things go wrong.

---

## Slide 23. Thank you

**On slide:**

- Thank you
- Repo link or QR code
- Questions?

**Visual:**
QR code to your GitHub repo. Your name + contact. Nothing else.

**Say:**
Thank you! Happy to take questions.

---

# Appendix: numbers and citations

Useful to have on hand for Q&A.

## Phase B raw numbers

- 5 issues (1056, 815, 1224, 1240, 607), 2 arms, 10 parallel workers, 20 min wall.
- FULL: `test_pass_rate=100%`, `first_pass=80%`, `avg_rounds_to_pass=1.2`, `reviewer_recall=50%`, `balance_gap=50%`.
- ABLATE: same `test_pass_rate=100%`, same `first_pass=80%`, `avg_rounds_to_pass=1.4`, `reviewer_recall=57%`, `balance_gap=43%`.

## Phase C raw numbers

- 2 issues (1240, 815), 2 arms, 4 parallel workers, 15 min.
- FULL: `test_pass_rate=100%`, `first_pass=100%`, `reviewer_recall=66.7%`, `balance_gap=33.3%`.
- ABLATE: `test_pass_rate=50%` (1240 still fails without context).

## Phase D raw numbers

- 4 issues (1056, 815, 1224, 607) sequential, single FULL process, 22 min wall.
- `test_pass_rate=50%` (2/4), `first_pass=50%`, `approval_rate=75%`.
- `reviewer_precision=33.3%`, `reviewer_recall=25%`, `reviewer_fpr=100%`.
- Reviewer frozen after 815 with `precision_below_floor(0.00<0.6)` and `reviewer_over_asking(0.75-0.25>0.3)`.
- Memory ended with 1 coder lesson, 3 reviewer false_rejection cases from 815.
- New defect on 1056 and 607: coder's own added tests failed; reviewer approved anyway.

## Stability rules

- Reviewer freeze: precision < 0.6, approval rate saturation, or either directional balance gap > 0.30.
- Held-out: 7 of 25 issues (default seed 42).
- Alternating updates: odd training issue updates the Coder, even updates the Reviewer.
- Category-balanced retrieval: always inject at least one memory bullet from a different category.

## Docs to point to

- `docs/OVERVIEW.md`: plain-language start-here doc.
- `docs/DESIGN.md`: the technical design.
- `docs/RESULTS.md`: Phase A, B, C, D writeups with all the numbers.
- `docs/RESPONSE.md`: direct answer to the worktrial prompt.
