# Dashboard

Next.js 15, App Router, Tailwind. Every page is a server component that reads
`services/api` - the dashboard holds no state of its own, so it cannot show a letter as
sent when it was not.

```bash
npm install
npm run dev          # http://localhost:3000
```

The API must be running (`python tasks.py dev`, port 8000). Point elsewhere with
`AFTERCARE_API`.

| Route | What it is for |
|---|---|
| `/` | Obligation graph, the four found without being listed, every institution and its state |
| `/approvals` | **The approval queue.** The only interactive surface, and the only way anything sends |
| `/inbound` | Every letter received; blocked ones first, with the rule that caught them |
| `/registry` | Playbook catalog, versions, and amendments proposed by sub-agents |
| `/audit` | The fiduciary record and its chain-integrity check |
| `/institutions/[id]` | One case end to end: transitions with reasoning, letters, replies, case file |

## Design notes

Restraint reads as seriousness. One muted accent for progress, one for alarm, a lot of
grey, and no animation. Letters are set in a serif at a readable measure because an
executor reads every word before approving; a monospace wall of text is not readable.

The banner naming the data as fictional is in `app/layout.tsx` and appears on every page.
It is not decoration - invariant 6 says demo data is fictional, and saying so once in a
README is not the same as saying so on screen.
