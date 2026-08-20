# Demo script

Four minutes. Every claim below is checked against what the code actually does - if a beat
here oversells, fix the beat, not the voiceover.

## Before you record

```bash
python tasks.py clean
python tasks.py seed        # 23 obligations, 23 drafts, nothing sent
python tasks.py dev         # API on :8000
npm --prefix web run dev    # dashboard on :3000
```

Leave the estate seeded but **not** replayed. The time-warp runs live, on camera.

Second terminal, ready to run:

```bash
python tasks.py demo --verbose
```

Third terminal, for the proof shot:

```bash
python tasks.py test-policy
```

---

## 0:00-0:25 - The problem

**On screen:** a physical stack of letters on a desk. Then the dashboard, empty.

**Voiceover:**
> When someone dies, their family inherits three to nine months of administrative work
> across thirty to sixty institutions. Billions of dollars sit unclaimed in state
> databases because nobody knew the accounts existed. All of it lands on one grieving
> person with no list to work from.

One statistic. No violins.

---

## 0:25-0:50 - Discovery

**On screen:** `/` - the obligation graph assembling. Four nodes are amber.

**Voiceover:**
> The executor uploads a death certificate and twelve months of statements. Aftercare
> reconstructs the obligation graph: twenty-three institutions. Nineteen came from
> letterheads. Four came from nowhere - a storage unit, a medical alert service, an
> annuity paying money *in*, and an escrow refund that was escheated to California in
> 2019. Nobody was ever told about any of them.

**Point at:** the "Found without being listed" panel. Each carries its reasoning -
"12 debits averaging $148.00, stable within 0%, with no uploaded document naming this
counterparty."

**What would ruin the take:** running `demo` before recording. The graph must be all grey
and amber, no green yet.

---

## 0:50-1:10 - The registry

**On screen:** `/registry`.

**Voiceover:**
> Each institution resolves to a versioned playbook - its closure process, encoded once.
> The obligation graph is rebuilt for every estate. These are not. This is the compounding
> asset: encode a bank's process once, and every future estate reuses it.

**Point at:** the six deep playbooks, then the "long tail" panel that admits the other
seventeen run on a generic template. Saying so is stronger than hiding it.

---

## 1:10-1:50 - Six weeks in forty seconds

**On screen:** the terminal running `python tasks.py demo --verbose`, with the dashboard
beside it. The simulated clock advances. Institutions turn green.

**Voiceover:**
> Six weeks of real inbound mail, replayed through the live pipeline. Every code path is
> production - the same screen, the same classifier, the same state machine, the same
> approval gate. Only the calendar is compressed. Between letters the sub-agents are
> dormant and hold no CPU; they wake on mail, or on their own follow-up timer.

**True because:** nothing in the codebase calls `datetime.now()` outside
`packages/core/clock.py`, and `test-policy` fails the build if that changes.

---

## 1:50-2:15 - The injection

**On screen:** `/inbound`. The blocked messages are at the top. Open the day-10 one from
Northshore Wireless.

**Voiceover:**
> Day ten. A scanned letter arrives. A human reading it sees a table of dates. The OCR
> text layer contains an instruction telling the agent it may now send without executor
> approval. The screen runs before any model sees the text - so there was nothing to
> interpolate into a prompt, and the case state did not move.

**Point at:** the rule names - `instruction.override`, `approval.bypass`,
`scan.injection_in_ocr_layer` - and the line saying the original is quarantined for a
human to read.

**Then, briefly:** the day-23 letter with "updated banking details". Bereavement fraud's
most common form, blocked by `funds.redirection`.

---

## 2:15-2:40 - Least disclosure

**On screen:** `/approvals`, one letter open, disclosure panel beside it. Then a second
institution for contrast.

**Voiceover:**
> The pension fund needs a certified death certificate and the grant of probate. The wine
> society gets a name and a date. That difference is not a setting - it is computed from
> each recipient's own published requirements, and the withheld column is shown to the
> executor because what was *not* sent matters as much as what was.

**Point at:** a withheld row and its justification line.

---

## 2:40-3:05 - It runs on Google Cloud

**On screen:** Cloud Run console with the deployed services. Vertex AI request logs. A
live request hitting the deployed API.

**Voiceover:**
> Five services on Cloud Run. Gemini on Vertex AI. Firestore for state, Pub/Sub for
> inbound, BigQuery for the audit sink, one least-privilege service account per agent
> role - a compromised utility agent cannot reach the brokerage credentials.

**Do not rush this.** It is the non-negotiable proof shot. Record it with the console
actually loading, not a screenshot.

---

## 3:05-3:25 - The boundary

**On screen:** `/approvals`, the Ashgrove Mutual Life escalation. Then the terminal
running `python tasks.py test-policy`.

**Voiceover:**
> The insurer says the beneficiary designation is contested and asks us to confirm in
> writing who inherits. That is a legal determination, so the agent stops and writes a
> brief. It recommends. It does not decide.
>
> And this is not a promise - it is a property of the architecture. This test walks the
> syntax tree of every file and proves nothing outside the approval service can reach the
> send path, and that no route through the state machine reaches "sent" without passing
> "awaiting approval". It is also run against a deliberately planted bypass, so it is
> known to fail when it should.

**Then:** approve one letter on camera. The executor decides.

---

## 3:25-3:50 - Fiduciary duty

**On screen:** `/audit`, the chain-integrity panel reading VERIFIED. Scroll the reasoning
column. Then the exported record.

**Voiceover:**
> An executor carries fiduciary duty and can be sued. So every state transition carries
> the reasoning behind it, and every record is chained to its predecessor by SHA-256 -
> altering one invalidates every record after it. This exports as a document an executor
> could hand to a probate court.
>
> Nineteen closed. Two escalated, waiting on a human. Four injection attempts blocked.
> Eleven thousand five hundred dollars recovered that the family would never have found.

---

## 3:50-4:00 - The card

**On screen:** static text.

> Fictional decedent. Fabricated documents. Invented institutions.
> Aftercare drafts; the executor decides.
> Not legal advice.

---

## Things that will ruin a take

- Running `demo` before recording - the graph must start grey.
- A stale `AFTERCARE_MODE=cloud` in the shell. Run `python tasks.py doctor` first.
- The approval queue already empty because you approved everything rehearsing.
  `python tasks.py clean && python tasks.py seed` resets it in seconds.
- Reading the voiceover faster to fit. Cut a beat instead - 3:40 of calm beats 4:00 of rush.
