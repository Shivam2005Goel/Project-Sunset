# Aftercare - 11-Day Execution Plan

**Hard deadline:** Sep 1, 05:30 IST - treat **Aug 30, 23:00** as the real deadline. The
last 30 hours are buffer, and you will need them.

**Governing principle:** the demo video is the product. Everything gets built in the order
that makes the video possible. If a feature won't appear on screen, it's cut.

---

## Day 0 (2 hours) - De-risk before you build

Do this before writing a line of code. These are the things that can kill the project on
day 6 if discovered late.

1. Claim the GCP trial and the $150 credit form. **Credits can take hours-to-days to
   land** - start the clock now.
2. Verify in the live console: **which Gemini model IDs can this project actually call in
   this region, right now?** Are Agent Registry / Runtime / Memory Bank / Model Armor
   generally available, or waitlisted? Write the answers at the top of `CLAUDE.md`. This
   single fact determines whether you build on GEAP or on the fallbacks.
3. Pin exact versions: ADK, `google-genai`, Vertex SDK. Put them in `pyproject.toml` today.
4. Create the repo, drop in the four documents, push.
5. Write the **video script first** - 4 minutes, beat by beat. Building toward a written
   script is worth more than any framework choice.

> If GEAP components are waitlisted, do not panic and do not switch tracks. The fallbacks
> demonstrate the same architectural properties (discovery, versioning, async persistence,
> guardrails, audit). Say in the video: *"designed against Agent Registry's contract, with
> a portable fallback implementation."* That reads as maturity, not compromise.

---

## The Claude Code workflow

Set this up once; it pays for itself by day 3.

**Session hygiene**
- `CLAUDE.md` is the load-bearing artifact. Update the "Current state" checklist at the
  end of every session - it's how tomorrow's session resumes in 10 seconds.
- Use **plan mode** for anything spanning >2 files. Read the plan, push back, *then* let
  it run. Most bad agent output traces to a bad plan you didn't read.
- `/clear` between unrelated tasks. A polluted context makes worse decisions than a fresh one.
- Keep sessions to one capability. "Build discovery" is a session. "Build the app" is not.

**Custom commands** - in `.claude/commands/`:

| Command | What it does |
|---|---|
| `/checkpoint` | Full suite, policy proof, smoke, format, conventional commit, update the Current state checklist. |
| `/adversary` | Re-read the guardrails, generate 5 new payloads targeting current gaps, run, **report what got through before fixing anything**. |
| `/playbook <institution>` | Scaffold a new institution playbook against the schema, with real operational knowledge in it. |
| `/democlip <feature>` | Given a feature, describe exactly what is on screen, the voiceover, the duration, the precondition, and what would ruin the take. |
| `/daystatus` | Verify the checklist against reality rather than repeating it, and recommend cuts if the remaining work does not fit. |

**Subagents** - parallel workers for genuinely independent tracks. Good splits:
Terraform/IAM vs. discovery logic; dashboard vs. backend FSM; adversarial generation vs.
everything. Bad split: two agents on the same Pydantic models.

**The feedback loop that matters:** screenshot the dashboard, paste it in, say what's
wrong. Claude Code iterates visually far better than from a text description of a layout
problem.

---

## Day-by-day

### Day 1 - Skeleton + adapters + infra
Repo structure per README §6. Pydantic models. All four adapters with **fallback
implementations written first** - they're simpler, and they unblock every later day.
Terraform for Firestore, Pub/Sub, GCS, BigQuery, service accounts. One real model call
end-to-end through `packages/core/llm.py` with a trace exported.

**Exit test:** `python tasks.py verify` passes.

### Day 2 - Discovery
The obligation-graph builder. Multimodal parse of PDFs and mail photos into normalized
`Obligation` records with confidence and evidence pointers. Build the fictional corpus at
the same time, including the institutions that are discoverable only from inference -
those are the demo's surprise.

**Exit test:** seeded corpus → 23 obligations, ≥3 not present in the naive list.

### Day 3 - Playbooks + registry
Playbook YAML schema. Six institution playbooks written in depth (a bank, a life insurer,
a pension provider, a mobile carrier, a utility, a brokerage). Registry publisher with
semantic versioning. Amendment path - a sub-agent proposing v1.1 when an institution
demands something new.

**Exit test:** publish, fetch by name+version, diff two versions.

### Day 4 - FSM + orchestrator
The explicit state machine, persisted per institution. Root agent reading the graph,
resolving playbooks, spawning sub-agents. First closure packet drafted end-to-end.

**Exit test:** seeded estate → 23 sub-agents, 23 packets drafted, all `AWAITING_APPROVAL`.

### Day 5 - Inbound + guardrails ⭐ **highest-value day**
Gmail watch → Pub/Sub → injection screen → classifier → FSM transition. Write the
adversarial suite as you go: 40 payloads, including injections embedded in *scanned
images* of letters. OCR-layer injection is the impressive one, and it's the one that'll be
visible in the video.

**Exit test:** `python tasks.py test-adv` - zero payloads reach model context unscreened.

### Day 6 - Approval queue + PII minimizer
Per-recipient disclosure computation. The side-by-side diff view. The static-analysis test
proving no bypass path exists.

**Exit test:** `test-policy` fails when you deliberately add a bypass, passes when you
remove it. **Verify both directions** - a guard nobody has watched fail is not a guard.

### Day 7 - Dashboard
Obligation graph, per-institution timeline, approval queue, audit view, simulated clock.
Iterate via screenshots. This is where the presentation score is won or lost - spend the
full day, don't ship a Bootstrap table.

### Day 8 - Observability + audit
OpenTelemetry spans across the full lifecycle. BigQuery append-only sink. **Reasoning-chain
export as a document an executor could hand to a probate court** - build this, it's the
single most distinctive artifact in the submission.

### Day 9 - Time-warp + polish
Drive the scripted six-week corpus through the real pipeline. Full dry run. Fix what
breaks. Freeze features at end of day.

### Day 10 - Deploy + capture
Clean deploy from scratch into a **brand-new GCP project** - this is the true test of the
README, and it will find bugs. Capture all §7 proof screenshots. Tag `v1.0`.

### Day 11 - Video + submission
Record. Edit. Write the Devpost description (features, tech, data sources, findings, and
learnings - the "learnings" field is scored and most teams leave it thin). Publish the
bonus blog post and the social post. **Submit 24 hours early.**

---

## The 4-minute video, beat by beat

| Time | On screen | Voiceover beat |
|---|---|---|
| 0:00-0:25 | A stack of letters. One statistic. | The problem, stated once, without sentimentality. |
| 0:25-0:50 | Upload certificate. Graph assembles. Four nodes flash amber. | "It found four the family didn't know about." |
| 0:50-1:10 | Registry, playbook v1.0 fetched | Reuse argument: encode once, every future estate benefits. |
| 1:10-1:50 | Clock spins. Institutions turn green. | Weeks of autonomous operation, agents dormant between letters. |
| 1:50-2:15 | Injection blocked in a scanned letter | Inbound mail is untrusted input. Guardrail catches it live. |
| 2:15-2:40 | PII diff: pension vs wine society | Least-disclosure, computed per recipient. |
| 2:40-3:05 | **Cloud Run console + Vertex logs + live request** | Proof it runs on Google Cloud. Non-negotiable, don't rush it. |
| 3:05-3:25 | Escalation brief; executor approves | "The agent drafts. The human decides. Always." |
| 3:25-3:50 | Audit export; final dashboard | Fiduciary duty. 19 closed, 2 escalated, $11,563 recovered. |
| 3:50-4:00 | Safety card | Fictional data. Not legal advice. |

Record it **unedited in one take** where possible - the criteria say "live, unedited
demo," and jump cuts read as concealment.

---

## Cuts, ranked

If you're behind, cut in this order - and cut early, not on day 10:

1. Unclaimed-property registry lookup (nice, not load-bearing)
2. Playbooks 5-6 (three deep beats six shallow)
3. Portal form-fill outbound channel (email only)
4. Playbook auto-amendment (demo it manually)
5. Audit document export (show the BigQuery view instead)

**Never cut:** the approval boundary, the injection screen, the Cloud Run proof shot, or
the time-warp. Those four are the submission.

---

## Failure modes that sink teams like this one

- **Building the platform instead of the demo.** You are not shipping a product. You are
  shipping four minutes of proof.
- **Discovering on day 8 that a GEAP service is waitlisted.** Day 0 fixes this.
- **A dashboard that looks like a hackathon dashboard.** 30% of the score is presentation.
- **Deploying only from your laptop's already-configured project.** Fresh-project deploy on
  day 10, or your reproducibility claim is untested.
- **Sentimentality.** Handle the subject matter clinically and respectfully. One statistic,
  no violins, no stock footage of candles. Restraint reads as seriousness.
