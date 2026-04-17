# Presentation Deck

One slide per section. Each slide has:

- **Title**: usually a takeaway sentence, not a topic label.
- **On slide**: minimal text, 4 bullets max, ~6 words each.
- **Visual**: what to draw, screenshot, or chart. Most are doable with Google Slides shapes, Google Sheets charts, or a screenshot.
- **Say**: speaker notes. Carry the content that isn't on the slide.

22 slides, about 18 to 22 minutes. Slide 16 (Phase D) is your strongest moment; if you need to cut, merge 2+3 and 10+11.

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

**Big question on the slide (bottom, larger font):**
**Can we use those conversations to make both AIs better?**

**Visual:**
Simple horizontal flow with three boxes: `Issue -> Coder -> Reviewer -> (approve | revise)`. Use arrows. A small loop arrow from Reviewer back to Coder labeled "feedback". Put the question in a highlighted box directly below the diagram.

**Say:**
Picture a junior engineer and a senior engineer working on a bug. Junior writes a fix, senior reviews, they iterate. That's exactly how two AI helpers work today. Interesting question: can we use those transcripts to make both AIs better over time?

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

## Slide 6. What we built: five pieces

**On slide:**

| Piece | What it does |
|---|---|
| **Oracle** | Runs the real tests. Hidden from the Reviewer. |
| **Git history** | Shows both AIs past fixes from this repo. |
| **Two notebooks** | Coder stores lessons. Reviewer stores calibration cases. |
| **Distillation** | Updates the notebooks after each bug. |
| **Safety rails** | Held-out bugs, alternating updates, freeze rules. |

**Visual:**
No arrows or flowchart. Just the table above, rendered as 5 stacked rows with the left column bold. Optionally prefix each row with a small icon (test tube, branch, notebook, funnel, shield). Keep it scannable in 10 seconds.

**Say:**
Five pieces, five rows. An Oracle runs the real tests and is the one thing nobody can fool. The git-history retrieval lets both AIs see how similar fixes looked in this repo before. Each AI has a small notebook with distilled lessons. Distillation fills those notebooks after every issue. And safety rails on top prevent bad dynamics.

---

## Slide 7. The Oracle: our teacher

**On slide:**

- Runs the real pytest tests on every fix.
- Result is **hidden from the Reviewer**.
- Otherwise the Reviewer would just copy the Oracle's answer.
- So we keep the Reviewer blind and compare its guess against the Oracle afterward.

**Visual:**
Three boxes: the Coder's fix goes to both the Reviewer (left) and the Oracle (right). Over the Oracle, draw a lock icon pointing at the arrow going TO the Reviewer (the locked path is Oracle → Reviewer, that's the one that's blocked). The Reviewer outputs "approve/reject", the Oracle outputs "pass/fail".

**Say:**
The single most important piece. Tests don't lie. They just run and pass or fail. We hide their result from the Reviewer on purpose. If the Reviewer could see the Oracle's answer, it would just copy it and we'd learn nothing about its judgment. We want the Reviewer to have its own opinion, then we compare.

---

## Slide 8. What goes in the notebooks

**On slide:**

**After every bug, both notebooks get updated based on the Oracle's verdict.**

**Coder's notebook** (lessons to try next time)

- Only updated when Oracle says PASS. Reviewer approval alone does not count.
- Contains 1 to 2 short lessons per bug.
- Tagged by bug category; capped at about 8 per category.
- Example: *"Russian plural bugs are usually in arrow/locales.py. Check the timeframes dict."*

**Reviewer's notebook** (calibration cases)

- Updated every round based on Oracle vs its own verdict.
- Each entry is one of four types:
  - WIN: I approved and tests passed.
  - WIN: I rejected and tests failed.
  - LOSS: I approved but tests failed (missed a bug).
  - LOSS: I rejected but tests passed (too picky).
- Example LOSS: *"I asked for more tests on a fix that already passed. Next time, don't."*

**Visual:**
Center of the slide: a simple "after each bug" arrow pointing to the right.
Left side: a small box labeled "Completed trace + Oracle verdict".
Right side: two stacked notebook icons. Top one labeled "Coder's notebook" with a sample bullet visible. Bottom one labeled "Reviewer's notebook" with a sample WIN entry and a sample LOSS entry visible.

**Say:**
After each bug finishes, we walk the trace and update both notebooks. Coder side: only learn from successes. If the Oracle said the fix passed, we ask a small LLM to boil that fix into one or two generalizable lessons and save them. If the Oracle said it failed, we don't save anything for the Coder. That's how we prevent the Coder from learning to please the Reviewer while writing broken code.

Reviewer side: every round produces a calibration case. It's a 2 by 2 table. Two of the cases are wins (the Reviewer agreed with the Oracle) and two are losses (they disagreed). We store examples of each so next time the Reviewer is shown past wins and past losses right in its prompt. That's how it learns from its own mistakes.

---

## Slide 9. Experimental setup

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

## Slide 10. Metrics: what we measure

**On slide:**

| Metric | Plain English |
|---|---|
| **test_pass_rate** | Did the code actually work? (headline) |
| **precision** | When the Reviewer says yes, is it right? |
| **recall** | Does the Reviewer notice good code? |
| **balance_gap** | Do Reviewer and tests disagree a lot? |

**Visual:**
Dashboard mockup. One big number on the left (test_pass_rate, like "100%"). Four smaller boxes on the right with these exact labels and plain-English glosses under each. Use green/red fill for good vs bad ranges.

**Say:**
The headline is oracle-grounded so the AIs can't inflate it. Precision and recall are how we measure whether the Reviewer is any good, in plain terms. The last one is our watchdog for the two AIs quietly agreeing on something wrong.

---

## Slide 11. Why this set is good

**On slide:**

- **One** metric grounded in reality.
- **Per-agent** breakdown tells us who's at fault.
- **Watchdog** catches pathologies.

**Visual:**
Pyramid or funnel diagram. Wide bottom = noisy signals (approval rate, diff size, comments). Narrow top = the one thing we trust (test_pass_rate). Arrow labeled "ground truth".

**Say:**
A common trap in ML evals is measuring the wrong thing. If we'd used raw approval rate, the Reviewer could approve everything and we'd look like we're winning. Grounding in tests makes it ungameable. The per-agent breakdown is what let me catch the surprise I'll show in a few slides.

---

## Slide 12. How we parallelized

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

## Slide 13. Where we ran it

**On slide:**

Phase A (pilot, 3 bugs, Haiku): pipeline works. Caught 2 bugs in my own system.

Phase B (real run, 5 bugs, Sonnet):

- Both FULL and ABLATE fixed 100% of bugs.
- FULL slightly faster (1.2 rounds vs 1.4).
- **But Reviewer recall was only about 50%.**

*(Reviewer recall: of the fixes that actually worked, how many did the Reviewer approve?)*

**Visual:**
Two-panel slide.

- Left panel: "Phase A" block. Green check labeled "pipeline works". Two small red bug icons below, captions "Coder cheated with git checkout" and "Empty rounds miscounted". Label: "caught in our own system".
- Right panel: "Phase B" block. Grouped bar chart with 4 pairs (test_pass_rate, rounds_to_pass, precision, recall), FULL blue vs ABLATE gray. Red callout on the recall bars pointing down: "only ~50%". Tiny legend: "recall = of correct fixes, how many did the Reviewer approve?".

**Say:**
Phase A was our pilot with Haiku. Three bugs. Mostly verified the pipeline works and caught two bugs in our own system: the Coder was cheating by running `git checkout master` through its shell, and our metrics were miscounting empty rounds. Both fixed before the real measurement.

Phase B was the real ablation. Five bugs, Sonnet, two arms. Looked boring at first because both arms fixed all 5 bugs. But the per-agent breakdown showed something interesting: Reviewer recall was only about half. That means half the time the Coder wrote code that actually worked, the Reviewer rejected it anyway.

---

## Slide 14. Why recall was low: the Reviewer is too picky

**On slide:**

Issue 1240 example. Bug: `humanize(16 days)` should say "2 weeks", not "a month".

| Round | Oracle | Reviewer |
|---|---|---|
| 1 | PASS | REJECT (28 comments) |
| 2 | PASS | REJECT |
| 3 | PASS | REJECT |

Across all 15 rounds: **reviewer rejected 7 correct fixes.**

|  | Tests PASS | Tests FAIL |
|---|---|---|
| Reviewer approved | 5 | 0 |
| Reviewer rejected | **7** | 3 |

Reviewer never approved bad code (precision 100%) but rejected half the good code (recall 50%).

**Visual:**
Two mini visuals side by side.

- Left: issue 1240's round-by-round table. Big green check next to "tests passed every round". Big red X next to "reviewer approved?".
- Right: 2x2 confusion matrix. Red callout on "7 rejected good fixes". Green check on "5 correctly approved". Label: "never approved bad code; missed half the good".

**Say:**
This is the clearest example. On issue 1240 the Coder wrote a working fix. The Oracle said tests passed. The Reviewer rejected three rounds in a row asking for more tests, more edge cases. If I only had the Reviewer's verdict, I'd have concluded the Coder was bad. With the Oracle, I can see the Coder was fine and the Reviewer was too picky. Across all 15 rounds the pattern is the same: 7 out of 15 times, the Reviewer rejected code that actually worked. Not dishonest, just nitpicky. Interestingly, real arrow fixes usually include tests, so the Reviewer's instinct was reasonable. The Coder just wasn't meeting it.

---

## Slide 15. Phase C: fix the Coder, not the Reviewer

**On slide:**

- Instead of telling the Reviewer "approve without tests"
- We told the Coder to **write** the tests up front.

Result:

- Reviewer recall: 50% → **67%**
- Issue 815: 2 rounds → **1 round**
- The Coder now brings what the Reviewer would have asked for.

**Visual:**
Before-and-after bar chart of reviewer_recall: Phase B at 50%, Phase C at 66.7%, arrow pointing up labeled "+16.7".

Under the chart, a three-step flow:
`Reviewer asks for tests -> make Coder write tests -> Reviewer has nothing to ask about`

**Say:**
The obvious fix was to tell the Reviewer to stop asking for tests. But that would swing it toward rubber-stamp territory. The better fix was to make the Coder write the tests in the first place. 22 of 25 real historical arrow fixes included tests too, so this actually matches the repo's culture. Reviewer recall jumped from 50 to 67. Issue 815 went from 2 rounds to 1 round because the Coder delivered what the Reviewer would have asked for.

---

## Slide 16. Phase D: the next problem

**On slide:**

Sequential run, memory accumulates.

Good news:

- Memory worked. Reviewer recalibrated after seeing past rejections.

Bad news:

- Test pass rate dropped to **50%**.
- The Coder now writes tests. Sometimes they're broken (fail on its own fix).
- The Reviewer can't catch this. It reads the diff but doesn't run code.

**The metrics pipeline flagged it without us looking for it. That's the point.**

**Visual:**
Line chart of `test_pass_rate` across the four phases:

- Phase A: 67%
- Phase B: 100%
- Phase C: 100%
- Phase D: **50%** (drop)

Red arrow annotating the Phase D point with the caption: **"Coder writes broken tests. Reviewer approves anyway because it doesn't run code."**

**Say:**
Phase D was the most interesting result because it made things worse. We finally ran sequentially so memory could accumulate. Memory helped, the Reviewer did recalibrate. But now that the Coder writes tests, sometimes the test itself is broken. The Oracle catches it. The Reviewer reads the diff statically and doesn't run anything, so it approves anyway. Classic "Coder tests its own homework". The thing I love about this result is that the metric pipeline caught it without me looking for it. That's what the watchdog alerts are for. Each fix we made revealed the next problem, which is exactly how engineering actually goes.

---

## Slide 17. What worked

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

## Slide 18. What didn't work the first time

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

## Slide 19. Safety rails that caught themselves

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

## Slide 20. What's next

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

## Slide 21. Summary

**On slide:** *(big font, no bullets)*

> Two AIs teach each other.
> Real tests keep them honest.
> Every fix revealed the next one.

**Visual:**
Just the three lines above, centered, large font. Maybe a small closing icon at the bottom (two gears interlocking, or the Coder-Reviewer-Oracle trio from slide 1 redrawn).

**Say:**
The one-sentence version: we built a way for two AIs to teach each other, grounded by real tests that keep them honest. Every defect we fixed made the next one visible. For me the main lesson is that you cannot trust the agents grading each other. You need an outside judge that doesn't care about their feelings, and you need metrics that decompose which AI is at fault when things go wrong.

---

## Slide 22. Thank you

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
