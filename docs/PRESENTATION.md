# Presentation Deck

One slide per section. Each slide has:

- **On slide**: bullets or a diagram to show.
- **Say**: speaker notes in plain language. Audience is high schoolers who understand what agents and code review are.

Suggested length: 23 slides, roughly 18 to 22 minutes. Phase D at slide 17 is the most interesting moment. If you need to cut for time, slides 2+3 and 9+10 can each merge into one slide.

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
  + lessons notebook (distilled from past traces)
  + reviewer feedback (from the previous round)

reviewer prompt gets:
  + git history (same retrieval, so it knows the repo's norms)
  + calibration notebook (when it was right, when it was wrong)

every fix -> ORACLE (runs real tests, hidden from reviewer)
          -> REVIEWER (gives verdict + comments)

after the issue: distill the trace into memory updates
```

**Say:**
Five pieces. An Oracle that runs the real pytest tests and is the one thing nobody can fool. A git-history retrieval that shows both agents similar past fixes from this repo. A small notebook for each agent with distilled lessons. A distillation step that fills those notebooks after each issue. And safety rules on top to prevent the two AIs from colluding.

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

## Slide 17. Phase D: sequential memory test (4 issues)

**On slide:**

- First run that actually tests whether memory accumulates across issues.
- 4 issues in sequence, one FULL-arm process.
- **Two new findings, one good, one bad.**

Good:

- Memory does accumulate and does help. After issue 815 produced three "I over-asked" calibration cases, the reviewer on the next issue (1224) had one of those cases in its prompt and approved round 1 correctly.
- The reviewer freeze fired with the new directional reason `reviewer_over_asking` instead of the old misleading `reward_hacking`. The guardrail works.

Bad:

- **Test pass rate dropped from 100% to 50%.**
- On two issues the Coder wrote a regression test that FAILED on its own fix. Oracle reported 217/218 and 153/154. Reviewer approved both anyway because the reviewer doesn't actually run the tests.

**Say:**
Phase D is the most interesting result we got because it made things worse. We finally ran sequentially so memory could accumulate. Memory did help the Reviewer recalibrate. But it also exposed a new problem: now that the Coder is writing tests, sometimes it writes a broken test. The Reviewer can't catch that because the Reviewer reads the diff statically and doesn't execute anything. So the Reviewer approved code where the Coder's own test was failing. That's exactly the "coder tests its own homework" loophole we could have predicted but hadn't seen in the data before.

---

## Slide 18. What worked

**On slide:**

- **The Oracle**. The single biggest unlock. Without it we'd never have caught either the Reviewer over-asking or the broken-test loophole.
- **Git history for both agents**. Coder uses it to see past fix shapes. Reviewer uses it to know the repo's norms. Small efficiency win (~0.2 rounds faster to pass in Phase B).
- **Memory accumulation** (Phase D). Reviewer actually recalibrated after seeing past false-rejections in its prompt.
- **Coder writing tests alongside the fix** (Phase C). Took FULL/815 from 2 rounds to 1 round.
- **Parallel worktrees**. 3x speedup with no downside beyond memory isolation.
- **Directional alerts**. Phase D's freeze fired with the correct `reviewer_over_asking` reason, not a misleading label.

**Say:**
A lot worked. The Oracle is what made everything else measurable. Git history helped both agents. Memory really did accumulate when we ran sequentially. The Coder writing tests fixed the over-asking problem in Phase C. And the directional alerts correctly named every pathology we saw.

---

## Slide 19. What didn't work (first attempts)

**On slide:**

- First tried to tell the Reviewer "don't reject for missing tests". Overcorrected toward rubber-stamp. Reverted.
- Anti-flailing rules tuned for Haiku broke Sonnet's thorough exploration. Relaxed.
- **Phase D revealed a new defect: the Coder sometimes writes broken regression tests.** Tests fail on its own fix. Reviewer can't catch that because it reads statically.
- The Coder still sometimes skips the test on genuinely hard bugs (1240). Open.

**Say:**
Plenty of stuff didn't work the first time. I initially patched the over-asking from the Reviewer side. Wrong layer. The Coder writing tests was the better answer. My early anti-flailing rules were tuned for Haiku and accidentally killed Sonnet's thorough exploration. And Phase D uncovered that the "Coder writes tests" fix introduced a brand-new problem: sometimes the test itself is broken, and the Reviewer approves the diff anyway because the Reviewer doesn't run code. That's the next thing to fix.

---

## Slide 20. Safety rails that caught themselves

**On slide:**

- **Reviewer Freeze** fired in Phase D after 815 with the correct directional reason `reviewer_over_asking(0.75-0.25>0.3)`.
- **Held-out bugs**: 7 of 25 never feed distillation.
- **Asymmetric information**: Oracle never seen by Reviewer, Reviewer notebook never seen by Coder. Verified by re-reading the loop code.
- **Post-Coder HEAD check** caught the Coder sneakily running `git checkout master` in Phase A.
- **Empty-diff exclusion** caught a metric-layer bug that was misattributing "Coder did nothing" to "Reviewer over-asked" in Phase A.

**Say:**
I built a bunch of guardrails up front to prevent pathological dynamics. Several of them actually fired during development, which is how I know they work. The HEAD check caught the Coder cheating. The empty-diff filter caught a real metric bug. Phase D's Reviewer Freeze triggered with the correctly-named directional reason after splitting the alerts. These are the kind of defenses that only earn their keep when they catch something, and they did.

---

## Slide 21. What's next

**On slide:**

- **Highest priority: Oracle-side verification that the Coder's new tests actually fail on the pre-fix code.** Closes the Phase D loophole directly. ~30 lines.
- Tighten Coder prompt: "do not submit if your test is failing". Already in the prompt, make it louder.
- Dedupe calibration cases per issue so one hard bug doesn't over-weight the Reviewer's memory.
- Value-function head (AlphaGo-style P(tests pass) estimator).
- Reviewer `git blame` on modified lines specifically.
- Cross-repo: does this transfer to a different library?

**Say:**
The top item is specifically for the Phase D defect. If we run the Coder's own tests against the pre-fix code, we catch broken tests structurally. Thirty lines. Would close the loophole before any of the bigger ideas. Then the usual bigger stretches: a value function, cross-repo transfer, better retrieval. But the next commit is the oracle-side check.

---

## Slide 22. Summary

**On slide:**

- Two AI helpers teach each other using their past conversations.
- Grounded by real tests (the Oracle), which keeps them honest.
- We found the Coder was fine, the Reviewer was too picky.
- Fixed it by making the Coder write tests up front (addressing the root cause, not the symptom).
- The Oracle + ablations + directional alerts are the key ingredients that made this measurable.

**Say:**
The one-sentence version: we built a way for two AIs to teach each other while keeping them grounded by real tests, and we found out the Coder was doing its job but the Reviewer was too picky. The main lesson for me was that you can't just trust the agents grading each other. You need an outside judge that doesn't care about their feelings.

---

## Slide 23. Thank you / questions

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

## Phase D raw numbers

- 4 issues (1056, 815, 1224, 607) sequential, single FULL process, 22 min wall.
- `test_pass_rate=50%` (2/4), `first_pass_test_pass_rate=50%`, `approval_rate=75%`.
- `reviewer_precision=33.3%`, `reviewer_recall=25%`, `reviewer_fpr=100%`.
- Reviewer frozen after issue 815 with reasons: `precision_below_floor(0.00<0.6)` and `reviewer_over_asking(0.75-0.25>0.3)`.
- Coder memory ended with 1 lesson, reviewer memory with 3 false_rejection cases from 815.
- New defect: on 1056 and 607 the coder wrote tests that failed on its own fix, and the reviewer approved anyway. Two `false_approval` events.

## Stability rules

- Reviewer freeze: triggers if precision < 0.6, approval rate hits saturation, or either directional balance-gap exceeds 0.30.
- Held-out: 7 of 25 issues (default seed 42).
- Alternating updates: odd training issue updates the Coder, even updates the Reviewer.
- Category-balanced retrieval: always inject at least one memory bullet from a different category than the current issue.

## Docs to point to

- `docs/OVERVIEW.md`: plain-language start-here doc.
- `docs/DESIGN.md`: the technical design.
- `docs/RESULTS.md`: Phase A, B, C, D writeups with all the numbers.
- `docs/RESPONSE.md`: direct answer to the original worktrial prompt.
