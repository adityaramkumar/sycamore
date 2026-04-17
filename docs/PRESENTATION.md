# Presentation Deck

One slide per section. Each slide has:

- **On slide**: bullets or a diagram to show.
- **Say**: speaker notes in plain language. Audience is high schoolers who understand what agents and code review are.

Suggested length: ~18 slides, roughly 15 to 20 minutes.

---

## Slide 1. Title

**On slide:**

- Co-Optimizing an AI Coder and an AI Reviewer
- Subtitle: teaching two AI helpers to learn from each other, without cheating
- Your name, date

**Say:**
Today I'm going to walk through a project where I built a system that lets two AI helpers, one that writes code and one that reviews code, learn from each other using their past conversations. I'll tell you what I found out, including one big surprise.

---

## Slide 2. The problem

**On slide:**

- AI Coder: reads a bug report, writes a fix.
- AI Reviewer: reads the fix, approves or asks for changes.
- They go back and forth until the Reviewer is happy.
- Today these two are trained totally separately. They never learn from their conversations.

**Say:**
Picture a junior engineer and a senior engineer working on a bug together. The junior writes a fix, the senior reviews it, they iterate until the fix is merged. That's exactly how two AI helpers work too. The question my worktrial asked: can we use the transcripts of those conversations to make both AIs better over time?

---

## Slide 3. The trap

**On slide:**

- If we only use the Reviewer's opinion as "is this good?", the Coder can learn to write code that *sounds* good to the Reviewer without actually working.
- Like writing an essay that sounds smart to please the teacher, without really learning the material.
- We need an outside judge.

**Say:**
Here's the subtle part. If the only grade we give the Coder is the Reviewer's thumbs up, the Coder can learn to game the Reviewer. They can happily agree with each other while the code is still broken. Without an outside judge we have no way to tell if the system is actually getting better or just getting better at flattering itself.

---

## Slide 4. The target: arrow

**On slide:**

- arrow is a small Python date/time library.
- 25 real historical bugs, pinned to a commit before any of them were fixed.
- Example bug: `humanize()` says "16 days" is "a month" but it should say "2 weeks".
- All the real fixes exist on GitHub, so we have a ground-truth answer key.

**Say:**
To test this we needed a real codebase with real bugs. arrow is a small Python library for dates and times, and it has a long history of closed bug reports with their fix commits on GitHub. We pinned it to an old commit where none of the bugs had been fixed yet, and pointed our AI helpers at the 25 bugs we wanted to fix.

---

## Slide 5. What is an interaction trace?

**On slide:**

```
Issue: humanize() says "16 days" should be "a month" but should say "2 weeks"

Round 1:
  Coder's fix: <some code>
  Reviewer: "please add a test"

Round 2:
  Coder's fix: <updated code with a test>
  Reviewer: "approved"
```

**Say:**
Every time the Coder and Reviewer talk, we save the full conversation to a file. That file is called a trace. The whole exercise is basically: how much can we learn from these traces to make future runs better?

---

## Slide 6. What we built (big picture)

**On slide:**

```
coder prompt gets:
  + git history (similar past fixes from this repo)
  + memory (distilled lessons from past traces)
  + reviewer feedback (from the previous round)

every fix -> ORACLE (runs real tests, hidden from reviewer)
          -> REVIEWER (gives verdict + comments)

after the issue: distill the trace into memory updates
```

**Say:**
Five pieces. An Oracle that runs the real pytest tests and is the one thing nobody can fool. A git-history block so the Coder can see how past fixes in this repo looked. A small notebook for each agent with distilled lessons from past runs. A distillation step that fills those notebooks. And safety rules on top to prevent the two AIs from colluding.

---

## Slide 7. The oracle is the core insight

**On slide:**

- Runs the real pytest tests on every fix.
- Result is **hidden from the Reviewer**.
- Without it, the two AIs just grade each other in a vacuum.
- With it, we have an honest answer to "did the code actually work?" every round.

**Say:**
This is the single most important piece. Tests don't lie. They don't care how confident the Coder sounded or how picky the Reviewer was. They just run and pass or fail. We hide that result from the Reviewer specifically so the Reviewer can't cheat by just copying the Oracle's answer. We want the Reviewer to have its own opinion, and then we compare its opinion to the Oracle afterward.

---

## Slide 8. Experimental setup

**On slide:**

- Target: 5 curated bugs chosen to span all the main categories (humanize, locale, parser).
- One of them (#1240) is known-hard.
- Two arms:
  - **FULL**: everything turned on (history, memory, distillation).
  - **ABLATE**: everything turned off.
- Same model (Claude Sonnet), same commit, same seed.
- Up to 3 rounds per issue.

**Say:**
This is an ablation study. Standard experimental technique: hold everything constant and turn off the features you care about, then compare. If FULL beats ABLATE, the features helped. If they're the same, they didn't. We picked issues that spanned all the bug categories in the dataset, plus one known-hard case so we'd see where the system struggles.

---

## Slide 9. Metrics and why

**On slide:**

- `test_pass_rate`: did the code actually work? (headline, cannot be faked)
- `first_pass_test_pass_rate`: how often did the Coder solve it without help?
- `reviewer_precision`: when the Reviewer says approve, is it actually correct?
- `reviewer_recall`: when the code is actually correct, does the Reviewer recognize it?
- Balance alerts: catch reviewer-rubber-stamping OR reviewer-over-asking.

**Say:**
The headline number is grounded in the real tests. The reviewer can't inflate it. The per-agent numbers let us blame the right one when something goes wrong. And the balance alerts specifically look for the "two AIs agree while tests disagree" failure mode.

---

## Slide 10. Why this set of metrics is good

**On slide:**

- Only one metric matters for correctness; everything else is supporting detail.
- Per-agent breakdown tells us which AI is improving or falling behind.
- Deliberately skip: raw approval rate (gameable), diff size (volume, not correctness).
- Watchdog alerts are directional: reward hacking vs over-asking are different problems.

**Say:**
A common trap in ML evals is measuring the wrong thing. If we'd used raw approval rate, the Reviewer could just approve everything and we'd have thought we were winning. Grounding in tests makes it impossible to game the headline. The per-agent breakdown is what let me catch the surprise I'll show in a few slides.

---

## Slide 11. How we parallelized

**On slide:**

- Sequential: 1 issue at a time, ~6 minutes each, ~30 min per arm, ~60 min total for 2 arms.
- Parallel: 10 git worktrees (one per worker), all running at once.
- Wall time dropped from ~60 min to ~20 min.
- Tradeoff: each worker has a fresh memory, so we don't test memory accumulation in parallel mode.

**Say:**
Running 5 bugs sequentially in both arms would take an hour. To speed that up I used a git feature called worktrees, which lets you have multiple checked-out copies of the same repo. Each worker ran in its own copy, so they didn't step on each other's files. Ten workers in parallel got us from 60 minutes down to 20. The tradeoff is that memory doesn't accumulate across parallel workers, so for measuring memory specifically we'd need to run sequentially.

---

## Slide 12. Phase A: integration test (Haiku, 3 bugs)

**On slide:**

- First real run after pipeline came together.
- Did the whole system work end-to-end? Yes.
- Caught real bugs in my own system:
  - The Coder used its shell to run `git checkout master`, moving HEAD off our pinned commit.
  - Empty rounds (Coder gave up) were being counted as "Reviewer over-asked", wrong.

**Say:**
The first run was mostly about finding bugs in my own system, not bugs in arrow. And it did. My Coder was secretly cheating by using its shell access to move the repo to a different version. My metrics were miscounting rounds where the Coder produced nothing. Both got fixed before the real measurement.

---

## Slide 13. Phase B: the ablation (Sonnet, 5 bugs)

**On slide:**

| Metric | FULL | ABLATE |
|---|---|---|
| `test_pass_rate` | 100% | 100% |
| `first_pass_test_pass_rate` | 80% | 80% |
| `avg_rounds_to_oracle_pass` | **1.2** | 1.4 |
| `reviewer_recall` | 50% | 57% |
| `balance_gap` | 50% | 43% |

- Ceiling effect on pass rate: all 5 bugs got fixed by both arms.
- Small efficiency win for FULL (history helped Coder reach passing fix faster).
- **The real story is in reviewer_recall**.

**Say:**
Phase B was the real measurement. The headline looked boring: both arms fixed 100% of the bugs. But when I broke it down by agent I saw something interesting. Reviewer recall was only 50%. That means half the time the Coder wrote code that actually passed tests, the Reviewer rejected it anyway. That's a major finding the headline metric alone would have hidden.

---

## Slide 14. Example datapoint: issue 1240

**On slide:**

- Bug: `humanize()` says "16 days" should be "a month" but should say "2 weeks".
- FULL arm:
  - Round 1: Coder wrote an 821-char fix. **Oracle said tests pass**. Reviewer rejected with 28 comments asking for more edge cases.
  - Round 2: Coder submits the same fix again. Oracle still passes. Reviewer still rejects.
  - Round 3: Reviewer still rejects.
- The Coder's fix was correct. The Reviewer just wouldn't accept it.

**Say:**
This is the clearest example of the problem. The Coder wrote a fix. The Oracle said it worked. The Reviewer rejected it three times in a row, asking for more edge cases and more tests. If I'd only had the Reviewer's verdict to go on, I'd have concluded the Coder was bad at this bug. With the Oracle, I could see the Coder was fine and the Reviewer was the one being unreasonable.

---

## Slide 15. The surprise: Reviewer over-asks

**On slide:**

- 7 of 15 non-empty rounds: Coder wrote a passing fix, Reviewer rejected it anyway.
- Not reward hacking. The opposite: "over-asking".
- Old alert name was misleading. Fixed: now we have separate alerts for each direction.
- Real fix: make the Coder write tests up front so the Reviewer has nothing to complain about.

**Say:**
So my Reviewer was too picky. Not lazy, not dishonest, just nitpicky. It kept asking for more tests even when the fix worked. The question was how to fix that. My first instinct was to tell the Reviewer "approve even without tests", but that would swing it to rubber-stamp territory. Much better idea: make the Coder write tests as part of the fix. 22 of 25 real historical fixes in the repo actually include tests, so the Reviewer's instinct to ask for them was reasonable. The problem was the Coder wasn't meeting it.

---

## Slide 16. Phase C: does the fix work?

**On slide:**

- Added "write a regression test" to the Coder's instructions.
- Kept the Reviewer prompt balanced.
- Re-ran the two hardest cases from Phase B.

| | Phase B FULL | Phase C FULL |
|---|---|---|
| 1240 | 3 rounds, rejected | **2 rounds, approved** |
| 815 | 2 rounds, approved | **1 round, approved** |
| `reviewer_recall` | 50% | 66.7% |

- Verified the Coder actually wrote a test on 815 (the diff touches `tests/`).

**Say:**
Phase C confirmed it. On issue 815, the Coder wrote the test up front, and the Reviewer approved on round 1 instead of round 2. On 1240, it got to approved in 2 rounds instead of failing all 3. Reviewer recall went from 50% to 66.7%. Not perfect, but moving in the right direction.

---

## Slide 17. What worked

**On slide:**

- The Oracle (the big unlock). Without it we'd never have caught the Reviewer over-asking.
- Git history for the Coder. Small efficiency win (~0.2 rounds faster to pass).
- Coder writing tests alongside the fix. Directly fixed the over-asking.
- Parallel worktrees. 3x speedup with no downside beyond memory accumulation.
- Directional alerts. Makes the dashboard actually useful.

**Say:**
A few things that definitely worked. The Oracle was the biggest one, because it's what made the Reviewer's over-asking visible at all. Git history gave the Coder concrete examples from past fixes. Making the Coder write tests directly addressed the over-asking defect. And parallel worktrees are a cheap trick that cut our wall time by a lot.

---

## Slide 18. What didn't work (first attempts)

**On slide:**

- First tried to tell the Reviewer "don't reject for missing tests". Overcorrected, swinging toward rubber-stamp. Reverted.
- Anti-flailing rules tuned for Haiku broke Sonnet. Had to relax.
- The Coder still sometimes skips the test on genuinely hard bugs. Open defect.

**Say:**
Plenty of stuff didn't work the first time. I initially tried to patch the over-asking from the Reviewer side, telling it to approve without tests. That was the wrong layer to fix. The Coder writing tests was the better answer. Also my early rules to prevent the Coder from looping on shell commands worked for Haiku but accidentally killed Sonnet, because Sonnet is more thorough and my threshold was too aggressive. Tuned that down.

---

## Slide 19. Safety rails that caught themselves

**On slide:**

- Reviewer Freeze: if the Reviewer misbehaves for N issues, we stop updating its notebook.
- Held-out bugs: 7 of 25 are never used for training, only for grading.
- Asymmetric information: Oracle never seen by Reviewer, Reviewer notebook never seen by Coder.
- Post-Coder HEAD check: detect and revert if the Coder moved the repo off-baseline.

**Say:**
I built a bunch of guardrails up front to prevent pathological dynamics. Several of them actually fired during development, which is how I know they work. The HEAD check caught the Coder sneakily running `git checkout master`. The empty-diff filter caught a metric bug. The directional alerts correctly labeled the over-asking problem after we split them.

---

## Slide 20. What's next

**On slide:**

- Sequential runs within an arm to test memory accumulation (what parallel mode couldn't test).
- Tighten Coder prompt to force a test edit on hard bugs.
- Give the Reviewer git-blame too (not just diffs).
- Value-function head (AlphaGo-style "P(tests pass)" estimator).
- Cross-repo: does this transfer to a different library?

**Say:**
Things I didn't have time for. Running sequentially within an arm would let me measure memory accumulation, which parallel mode washes out. A value function would be an AlphaGo-inspired second opinion alongside the Reviewer. And the big open question: does any of this learning transfer between repos, or is it all arrow-specific?

---

## Slide 21. Summary

**On slide:**

- Two AI helpers teach each other using their past conversations.
- Grounded by real tests (the Oracle), which keeps them honest.
- We found the Coder was fine, the Reviewer was too picky.
- Fixed it by making the Coder write tests up front (addressing the root cause, not the symptom).
- The Oracle + ablations + directional alerts are the key ingredients that made this measurable.

**Say:**
The one-sentence version: we built a way for two AIs to teach each other while keeping them grounded by real tests, and we found out the Coder was doing its job but the Reviewer was too picky. The main lesson for me was that you can't just trust the agents grading each other. You need an outside judge that doesn't care about their feelings.

---

## Slide 22. Thank you / questions

**On slide:**

- Thank you!
- Repo link or QR code
- Questions?

---

# Appendix: numbers and citations

Useful pieces to have on hand if someone asks.

## Phase B raw numbers

- 5 issues (1056, 815, 1224, 1240, 607) across 2 arms.
- 10 parallel workers, 20 minutes wall time.
- FULL: `test_pass_rate=100%`, `first_pass=80%`, `avg_rounds_to_pass=1.2`, `reviewer_recall=50%`, `balance_gap=50%`.
- ABLATE: same `test_pass_rate=100%`, same `first_pass=80%`, `avg_rounds_to_pass=1.4`, `reviewer_recall=57%`, `balance_gap=43%`.

## Phase C raw numbers

- 2 issues (1240, 815) across 2 arms, 4 parallel workers, 15 minutes.
- FULL: `test_pass_rate=100%`, `first_pass=100%`, `reviewer_recall=66.7%`, `balance_gap=33.3%`.
- ABLATE: `test_pass_rate=50%` (1240 still fails without context).

## Stability rules

- Reviewer freeze: triggers if precision < 0.6, or approval rate is in [0, 0.05] or [0.95, 1], or balance gap > 0.30.
- Held-out: 7 of 25 issues (default seed 42).
- Alternating updates: odd training issue updates the Coder, even updates the Reviewer.
- Category-balanced retrieval: always inject at least one memory bullet from a different category than the current issue.

## Docs to point to

- `docs/OVERVIEW.md`: plain-language start-here doc.
- `docs/DESIGN.md`: the technical design.
- `docs/RESULTS.md`: Phase A, B, C writeups with all the numbers.
- `docs/RESPONSE.md`: direct answer to the original worktrial prompt.
