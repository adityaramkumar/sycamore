# Overview

Plain-language walkthrough of the project from start to finish. If
you want the technical design, see [DESIGN.md](DESIGN.md). If you
want the measured results, see [RESULTS.md](RESULTS.md). If you want
the original problem statement, see [PROBLEM.md](PROBLEM.md).

## The setup

Imagine two AI helpers working on a software bug.

- **Coder**: reads a bug report and writes code to fix it.
- **Reviewer**: looks at the Coder's fix and says "looks good, ship it"
  or "nope, here's what's wrong."

They go back and forth until the Reviewer is happy or they give up.
That's basically how a junior engineer and a senior engineer work
together on a team.

## What is an interaction trace?

Every time they talk, we save the conversation to a file. That file
is called a **trace**. It looks roughly like this:

```
Issue: "humanize() says '16 days' should be 'a month' but should say '2 weeks'"

Round 1:
  Coder's fix: <some code>
  Reviewer says: "nope, add tests"

Round 2:
  Coder's fix: <updated code>
  Reviewer says: "approved!"
```

That whole conversation, start to finish, is one trace. We save one
per bug.

## The problem we were asked to solve

Today's AI coder and AI reviewer are trained separately. They don't
learn from each other. The worktrial prompt asked: can we use the
traces above to teach both of them to be better, without the bad
stuff happening?

Bad stuff we want to avoid:

- The Coder learns to write code that *sounds* good to the Reviewer
  but doesn't actually work. Like writing an essay that sounds smart
  but has no real content, just to please the teacher.
- The Reviewer gets too easy (rubber-stamps everything) or too harsh
  (rejects everything). Both are useless.

## What we built

Four main pieces:

1. **An Oracle.** This is the ultimate honest judge. We run the real
   pytest tests on every fix the Coder writes. Tests can't be fooled.
   Crucially, the Reviewer never sees what the tests said, so it
   can't cheat by copying the tests' answer.

2. **A "memory" for each helper.** Like a small cheat sheet they
   carry. The Coder's memory has lessons from past fixes that worked
   ("when the bug is about Russian plurals, check arrow/locales.py").
   The Reviewer's memory has notes about when it was right and when
   it was wrong compared to the tests.

3. **Git history lookup for the Coder.** Before working on a bug,
   the Coder gets handed 2 or 3 old commits from the project's
   history that look topically similar. Like getting a peek at how
   previous similar bugs were fixed.

4. **Safety rules.** Like "never update both helpers at the same
   time", "hide a few bugs as a pop quiz so we can tell if the
   helpers are really learning or just memorizing", and "if the
   Reviewer goes crazy, freeze its memory before it spreads bad
   habits."

The target project is `arrow`, a small Python library for dates and
times. 25 real historical bugs. All the fixes already exist on
GitHub, but we pin the project to an old commit from before any of
them were fixed so we can grade the AI's work against the real fix.

## What is an ablation?

An ablation is a fancy word for "what if we turn off this feature
and see what happens?" You compare two runs:

- **Full**: everything turned on.
- **Ablate**: memory off, git history off.

If Full beats Ablate, the features helped. If they're the same, the
features didn't matter. That's it.

## What happened (our results)

We ran the two versions on 5 bugs, in parallel (both versions at
the same time).

- **Both versions fixed all 5 bugs** (100% test pass rate in both).
- The Full version did it **slightly faster** on average (1.2 rounds
  vs 1.4 rounds). Small signal, but real.
- **Biggest surprise**: the Reviewer was the problem. 7 out of 15
  times the Coder wrote a fix that actually worked, the Reviewer
  still rejected it and asked for more changes. The Coder was fine.
  The Reviewer was a nitpicker.

So the main takeaway was "our Reviewer is grumpy, not our Coder is
dumb."

## Stuff we fixed along the way

Some silly bugs showed up during testing that we had to hunt down:

- The Coder was using its shell access to run `git checkout master`,
  which secretly moved the project to a different commit than we
  thought we were testing on. We added a check that catches this
  and resets the project back.
- When the Coder produced no fix at all, the tests obviously still
  passed (because nothing changed), and our metrics were counting
  that as "the Reviewer over-asked", which was wrong. We fixed the
  metrics to ignore empty rounds.
- The Coder's shell access is still a bit of an unfenced yard.
  Reasonable next step is to tighten which shell commands it can run.

## What we'd do next

With 2 more weeks:

- Run the helpers in a specific *order* (not all at once) so the
  memory actually gets used across multiple bugs. Parallel mode was
  fast but lost that benefit.
- Fix the Reviewer's "too grumpy" problem by rewriting its prompt.
- Give the Reviewer access to git history too. It should know the
  repo's style, not just judge in a vacuum.

With 2 more months:

- Try the same system on a different project entirely and see what
  transfers.
- Add a human spot-check so a real person sanity-reviews some fixes,
  in case the tests miss something.

## One-sentence version

We built a way for an AI coder and an AI reviewer to teach each
other using their past conversations, with a real test suite as the
honest judge, and found out the coder is actually fine but the
reviewer is too picky.

## Where to read next

- [DESIGN.md](DESIGN.md): how the pieces fit together, what we
  borrowed from GANs and AlphaGo, and why we made the choices we did.
- [RESPONSE.md](RESPONSE.md): direct answer to each question in the
  original worktrial prompt.
- [RESULTS.md](RESULTS.md): all the actual measurements from the
  real runs, plus honest caveats about what the numbers do and don't
  show.
- [PROBLEM.md](PROBLEM.md): the original worktrial prompt.
