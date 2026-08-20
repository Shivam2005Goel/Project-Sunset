---
description: Where the build actually stands against the 11-day plan
---

Read `BUILD_PLAN.md` and the Current state checklist in `CLAUDE.md`, then verify the
claims rather than repeating them:

1. Run `python tasks.py smoke` and report the actual numbers.
2. Run `python tasks.py test` and report pass/fail counts.
3. For each ticked day in the checklist, name the file or test that proves it. If you
   cannot find one, the tick is wrong - say so.
4. State what is genuinely NOT done, with emphasis on anything that has never been run
   against real GCP.
5. Given today's date and the Sep 1 deadline, say whether the remaining work fits, and
   if it does not, recommend which items from the "Cuts, ranked" list to take - in order,
   and early rather than on day 10.

Be blunt. The deadline is the binding constraint, not ambition.
