---
description: Describe exactly what a feature looks like on camera - /democlip <feature>
---

For **$ARGUMENTS**, produce the shot, not the feature description. Building toward a
written script is worth more than any framework choice, so be concrete enough that
someone could record it without asking you a follow-up question.

Give me:

1. **On screen** - which view, which element, what the viewer's eye lands on first, and
   what changes while they watch. Name the actual route or component.
2. **Voiceover** - the exact words, at most two sentences. Clinical. One statistic at
   most. No violins.
3. **Duration** - seconds, and where it sits in the four minutes (see the beat table in
   `BUILD_PLAN.md`).
4. **The precondition** - what state the system has to be in for this shot to work, and
   the command that gets it there (`python tasks.py seed`, then N days of
   `python tasks.py demo`, etc.).
5. **What would ruin the take** - the specific thing likely to go wrong live: an empty
   queue, a clock that has not advanced, a case that already closed.

Check the claim against the code before writing the voiceover. If the feature does not
do what the shot implies, say so plainly - a beat that oversells is worse than a beat
that is cut, and `CLAUDE.md` is explicit that a faked demo loses this hackathon.
