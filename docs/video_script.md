# Aftercare: 4-Minute Demo Script

> **Format Notes:** Unedited, single-take screen recording. Speak clinically and respectfully. No violins, no stock footage of candles.

---

### **0:00 - 0:25 | The Problem**
**On Screen:**
A stark, simple slide: A stack of letters representing bureaucratic mail. A single, bold statistic (e.g., "$58 billion sits in state unclaimed-property databases"). 
**Voiceover:**
"When someone dies, their family inherits months of administrative work across dozens of institutions. Accounts left open accrue fees. Policies lapse. Billions sit unclaimed because heirs never knew an account existed. This is Aftercare: an autonomous agent that handles the bureaucracy of death."

---

### **0:25 - 0:50 | Ingestion & Discovery**
**On Screen:**
The Next.js dashboard. The cursor uploads a fabricated death certificate and a folder of statements. The screen transitions to the Obligation Graph assembling in real-time. Three nodes pulse and turn yellow.
**Voiceover:**
"The executor uploads a death certificate and recent statements. Aftercare’s multimodal discovery agent reconstructs the estate's obligation graph. Today, it found twenty-three relationships. Three of these—pulsing yellow—were discovered purely through recurring-debit inference and registry lookups. It found three institutions the family didn't even know about."

---

### **0:50 - 1:10 | The Playbook Registry**
**On Screen:**
Dashboard focuses on a specific institution node (e.g., "Chase Bank"). The UI fetches and displays "Playbook v1.0".
**Voiceover:**
"For each institution, Aftercare pulls a versioned playbook from the Agent Registry. This encodes the required documents, submission channels, and SLAs. This is our core reuse argument: once an institution’s playbook is encoded, every future estate benefits from it automatically."

---

### **1:10 - 1:50 | The Sub-Agent Fleet & Time-Warp**
**On Screen:**
The simulated-date clock in the corner spins rapidly forward (400x speed). The estate FSM transitions. Nodes slowly turn from yellow (AWAITING_RESPONSE) to green (CLOSED).
**Voiceover:**
"Aftercare runs a dedicated, long-lived sub-agent for each institution. These agents operate autonomously over weeks—sleeping, holding no CPU, and waking only when inbound correspondence arrives. What you're watching is six simulated weeks of real inbound mail, replayed through the live production pipeline at 400x speed."

---

### **1:50 - 2:15 | Guardrails & Prompt Injection**
**On Screen:**
An inbound scanned letter is displayed. The Model Armor UI flashes red, blocking the message. The screen highlights an OCR-layer prompt injection embedded in the scan.
**Voiceover:**
"We treat all inbound mail as untrusted third-party input. Here, an adversarial scanned letter attempts a prompt injection. Before it ever reaches the model's context, our guardrail screens it, flags the OCR-layer attack, and blocks the transition."

---

### **2:15 - 2:40 | PII Minimizer**
**On Screen:**
A side-by-side diff view of two outbound packets. Left: Pension fund gets the full death certificate. Right: A magazine subscription cancellation gets only a name and date.
**Voiceover:**
"Outbound communications pass through a PII minimizer. We enforce least-disclosure per recipient. The pension fund gets the full certificate, but the magazine subscription gets only a name and a date—nothing more."

---

### **2:40 - 3:05 | Cloud Run Proof**
**On Screen:**
Switch tabs to the Google Cloud Console. Show the five deployed Cloud Run services, all scaled to zero. Switch to Vertex AI request logs showing live Gemini 3.5 Flash queries.
**Voiceover:**
"Everything runs on Google Cloud. Our five services scale to zero on Cloud Run, utilizing Gemini 3.5 Flash for multimodal inference and classification. This architecture runs comfortably inside a zero-trust model with one scoped service account per agent."

---

### **3:05 - 3:25 | The Human Approval Boundary**
**On Screen:**
The dashboard's Approval Queue. An "Escalation Brief" is shown (a one-paragraph summary of ambiguity). The cursor clicks "Approve".
**Voiceover:**
"But here is our most important safety boundary: no outbound communication is ever sent without explicit executor approval. If an institution demands something unexpected, the agent escalates with a one-paragraph brief. The agent drafts. The human decides. Always."

---

### **3:25 - 3:50 | The Audit Log**
**On Screen:**
The BigQuery console showing the append-only sink. The UI exports a formatted PDF timeline of the estate. 
**Voiceover:**
"Because executors carry fiduciary duty, every state transition writes an append-only, hash-chained record to BigQuery. The result is a court-defensible audit log of every action taken and its reasoning. Today, the agent closed nineteen accounts, escalated two, and recovered $11,563."

---

### **3:50 - 4:00 | Disclaimer**
**On Screen:**
A stark text card: "All demonstration data uses a fictional decedent and fabricated documents. Aftercare does not provide legal advice."
**Voiceover:**
"All demo data is a fictional decedent with fabricated documents. Thank you."
