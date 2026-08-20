---
description: Scaffold a new institution playbook - /playbook <institution name>
---

Add a playbook for **$ARGUMENTS** to `packages/playbooks/catalog/`.

Read `packages/playbooks/schema.py` first, then two existing playbooks (start with
`meridian-trust-bank.yaml` and `cascadia-securities.yaml`) so the new one matches their
depth. A shallow playbook is worse than no playbook - the generic template at least
admits it does not know.

Requirements:

- **Fictional institution.** Invariant 6. Model the process on publicly documented
  bereavement procedures for that sector; never reproduce a real company's internal
  process or any real customer data.
- `required_disclosures` may only name fields in `packages/guardrails/pii.py`'s
  `FIELD_CATALOG`, and may never name one on `NEVER_DISCLOSE`. The schema validator will
  reject it otherwise, which is the point.
- `submission_note` must carry real operational knowledge - the thing that costs an
  executor a round trip if they do not know it. If you cannot think of one, you do not
  understand this institution's process well enough to encode it yet.
- `escalation_triggers` must name the situations where this sector tempts an automated
  agent into a legal determination or a money movement.
- `closure_signals` must be phrases that actually appear in that sector's closure
  letters, because `packages/core/offline.py::CLASS_SIGNALS` matches on them.
- Set `source` honestly.

Then: add the institution to `demo/estate.py` if it should appear in the demo estate,
run `python tasks.py test -k playbooks`, and check that
`test_the_catalog_covers_the_six_institutions_the_plan_calls_for` still reflects reality
(update the count and categories if you have deliberately grown the catalog).
