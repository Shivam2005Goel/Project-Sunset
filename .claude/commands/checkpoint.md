---
description: Run the full test suite, commit, and update the Current state checklist
---

End-of-session ritual. Do all of it, in order, and stop at the first failure rather than
committing over a broken build.

1. `python tasks.py test` - the full suite must be green. If anything fails, fix it or
   explain why it is acceptable; do not commit red.
2. `python tasks.py test-policy` - the structural safety proof. This one is never
   allowed to be weakened to make a feature pass. If it fails, the feature is wrong.
3. `python tasks.py smoke` - end to end. Confirm the line still reads
   `SMOKE PASS - 23 discovered - 19 closed - 2 escalated - 2 pending`. If the numbers
   moved, say which letter or rule changed them before doing anything else.
4. `python tasks.py fmt`
5. Commit with a conventional message (`feat:`, `fix:`, `test:`, `docs:`, `chore:`),
   one capability per commit. Describe what changed and why, not which files.
6. Update the **Current state** checklist at the bottom of `CLAUDE.md`: tick what is
   done, and rewrite the "What is NOT done" paragraph so it is still true. That
   paragraph is how tomorrow's session avoids re-litigating yesterday's decisions.

If the working tree is dirty with unrelated changes, say so and ask before committing.
