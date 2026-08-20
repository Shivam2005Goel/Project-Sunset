import { api, shortDate } from "@/lib/api";
import { DisclosureTable, Empty } from "@/components/ui";
import { DecisionButtons } from "./decision";

export const dynamic = "force-dynamic";

/**
 * The approval queue.
 *
 * The centre of the whole submission: nothing in this system reaches an institution
 * without a human clicking one of these buttons. The layout puts the letter and the
 * disclosure list side by side deliberately - an executor deciding on twenty letters
 * needs to see what is being sent and what is being withheld in one glance, not in two
 * tabs.
 */
export default async function Approvals() {
  const { queue } = await api.approvals();

  if (queue.length === 0) {
    return (
      <div className="space-y-6">
        <Header count={0} />
        <Empty>
          Nothing is waiting on you. Every drafted letter has been decided.
        </Empty>
      </div>
    );
  }

  const escalations = queue.filter((row) => row.approval.kind === "ESCALATION");
  const outbound = queue.filter((row) => row.approval.kind === "OUTBOUND");
  const amendments = queue.filter((row) => row.approval.kind === "PLAYBOOK_AMENDMENT");

  return (
    <div className="space-y-6">
      <Header count={queue.length} />

      {escalations.length > 0 && (
        <section className="space-y-3">
          <h2 className="label">Decisions the agent will not make ({escalations.length})</h2>
          {escalations.map((row) => (
            <article
              key={row.approval.id}
              className="panel border-alarm-500/30 bg-alarm-600/[0.04]"
            >
              <div className="panel-head border-alarm-500/20">
                <div>
                  <h3 className="panel-title">{row.institution}</h3>
                  <p className="panel-note mt-0.5">
                    Raised {shortDate(row.approval.created_at)} &middot; nothing has been sent
                  </p>
                </div>
                <span className="text-2xs uppercase tracking-widest text-alarm-400">
                  Escalation
                </span>
              </div>
              <div className="px-5 py-4">
                <p className="font-serif text-sm leading-relaxed text-ink-200">
                  {row.approval.brief}
                </p>
                {row.approval.risk_flags.length > 0 && (
                  <ul className="mt-3 space-y-1">
                    {row.approval.risk_flags.map((flag, index) => (
                      <li key={index} className="text-2xs text-ink-500">
                        &bull; {flag}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="mt-4">
                  <DecisionButtons
                    approvalId={row.approval.id}
                    approveLabel="Proceed as recommended"
                    rejectLabel="I will handle this myself"
                  />
                </div>
              </div>
            </article>
          ))}
        </section>
      )}

      {amendments.length > 0 && (
        <section className="space-y-3">
          <h2 className="label">Playbook changes ({amendments.length})</h2>
          {amendments.map((row) => (
            <article key={row.approval.id} className="panel">
              <div className="panel-head">
                <h3 className="panel-title">{row.approval.summary}</h3>
                <span className="text-2xs uppercase tracking-widest text-ink-500">Amendment</span>
              </div>
              <div className="px-5 py-4">
                <p className="text-xs leading-relaxed text-ink-300">{row.approval.brief}</p>
                <ul className="mt-3 space-y-1">
                  {row.approval.risk_flags.map((flag, index) => (
                    <li key={index} className="font-mono text-2xs text-sage-300">
                      + {flag.replace(/^adds: /, "")}
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-2xs text-ink-600">
                  Approving publishes a new version to the registry. Every future estate that
                  touches this institution starts from it.
                </p>
                <div className="mt-4">
                  <DecisionButtons approvalId={row.approval.id} approveLabel="Publish version" />
                </div>
              </div>
            </article>
          ))}
        </section>
      )}

      {outbound.length > 0 && (
        <section className="space-y-3">
          <h2 className="label">Letters waiting to be sent ({outbound.length})</h2>
          {outbound.map((row) => (
            <article key={row.approval.id} className="panel">
              <div className="panel-head">
                <div>
                  <h3 className="panel-title">{row.institution}</h3>
                  <p className="panel-note mt-0.5">
                    To {row.packet?.recipient} &middot; {row.packet?.channel.toLowerCase()} &middot;{" "}
                    <span className="font-mono">{row.packet?.playbook_ref}</span>
                  </p>
                </div>
                <span className="text-2xs text-ink-600">
                  drafted by <span className="font-mono">{row.packet?.model_used}</span>
                </span>
              </div>

              {row.approval.risk_flags.length > 0 && (
                <ul className="border-b border-ink-850 bg-amber-500/[0.03] px-5 py-2.5">
                  {row.approval.risk_flags.map((flag, index) => (
                    <li key={index} className="text-2xs text-amber-400/90">
                      {flag}
                    </li>
                  ))}
                </ul>
              )}

              <div className="grid gap-6 px-5 py-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
                <div>
                  <div className="label mb-2">The letter</div>
                  <div className="mb-2 border-b border-ink-850 pb-2 font-serif text-sm text-ink-100">
                    {row.packet?.subject}
                  </div>
                  <div className="letter max-h-[28rem] overflow-y-auto pr-2">
                    {row.packet?.body}
                  </div>
                </div>

                <div>
                  <div className="label mb-2">Disclosure</div>
                  <div className="max-h-[28rem] overflow-y-auto pr-2">
                    {row.packet && <DisclosureTable disclosures={row.packet.disclosures} />}
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between border-t border-ink-850 px-5 py-3">
                <p className="text-2xs text-ink-600">{row.packet?.reasoning}</p>
                <DecisionButtons approvalId={row.approval.id} />
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}

function Header({ count }: { count: number }) {
  return (
    <header>
      <h1 className="font-serif text-xl text-ink-100">Approval queue</h1>
      <p className="mt-1 text-xs text-ink-500">
        {count === 0
          ? "Nothing outstanding."
          : `${count} ${count === 1 ? "item needs" : "items need"} your decision. Nothing leaves this system until you make it.`}
      </p>
    </header>
  );
}
