import Link from "next/link";
import { notFound } from "next/navigation";
import { api, money, shortDate } from "@/lib/api";
import { DisclosureTable, FindingList, StateBadge } from "@/components/ui";

export const dynamic = "force-dynamic";

/**
 * One institution, end to end.
 *
 * The page that answers "what has actually happened with the bank" - the state machine's
 * history with the reasoning behind every transition, every letter drafted, every reply
 * received, and the case file the sub-agent reads back when it wakes.
 */
export default async function Institution({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const detail = await api.caseDetail(id);
  if (!detail) notFound();

  const { case: record, packets, inbound, audit, memory } = detail;
  const latest = packets[packets.length - 1];

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-6">
        <div>
          <Link href="/" className="text-2xs text-ink-600 hover:text-ink-400">
            &larr; All institutions
          </Link>
          <h1 className="mt-1 font-serif text-xl text-ink-100">{record.institution_name}</h1>
          <p className="mt-1 text-xs text-ink-500">
            {record.category.replace(/_/g, " ").toLowerCase()} &middot; opened{" "}
            {shortDate(record.opened_at)}
            {record.closed_at ? ` · closed ${shortDate(record.closed_at)}` : ""} &middot;{" "}
            <span className="font-mono">{record.playbook_ref}</span>
          </p>
        </div>
        <div className="text-right">
          <StateBadge state={record.state} />
          {record.next_wake_at && (
            <p className="mt-1.5 text-2xs text-ink-600">
              Dormant until {shortDate(record.next_wake_at)}
            </p>
          )}
          {record.recovered_amount_usd > 0 && (
            <p className="mt-1 text-2xs text-sage-300">
              {money(record.recovered_amount_usd)} recovered
            </p>
          )}
        </div>
      </header>

      {record.escalation_brief && (
        <section className="panel border-alarm-500/30 bg-alarm-600/[0.04] px-5 py-4">
          <div className="label mb-2 text-alarm-400">Escalated - waiting on you</div>
          <p className="font-serif text-sm leading-relaxed text-ink-200">
            {record.escalation_brief}
          </p>
          <Link href="/approvals" className="mt-3 inline-block text-2xs text-ink-400 hover:text-ink-200">
            Decide in the approval queue &rarr;
          </Link>
        </section>
      )}

      {record.outstanding_requests.length > 0 && (
        <section className="panel px-5 py-4">
          <div className="label mb-2">Outstanding requests</div>
          <ul className="space-y-1">
            {record.outstanding_requests.map((request) => (
              <li key={request} className="text-xs text-amber-400">
                &bull; {request}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">State machine</h2>
            <span className="panel-note">{record.history.length} transitions, each with its reasoning</span>
          </div>
          <ol className="divide-y divide-ink-850">
            {record.history.map((step, index) => (
              <li key={index} className="px-5 py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-mono text-2xs text-ink-400">
                    {step.from_state === step.to_state
                      ? step.to_state
                      : `${step.from_state} → ${step.to_state}`}
                  </span>
                  <span className="whitespace-nowrap text-2xs tabular-nums text-ink-600">
                    {shortDate(step.at)}
                  </span>
                </div>
                <p className="mt-1 text-2xs leading-relaxed text-ink-400">{step.reason}</p>
                <p className="mt-1 text-2xs text-ink-600">
                  {step.event} &middot; {step.actor}
                </p>
              </li>
            ))}
          </ol>
        </section>

        <div className="space-y-6">
          {latest && (
            <section className="panel">
              <div className="panel-head">
                <h2 className="panel-title">Latest letter</h2>
                <span className="panel-note">
                  {latest.sent_at ? `sent ${shortDate(latest.sent_at)}` : "not sent"}
                </span>
              </div>
              <div className="px-5 py-4">
                <div className="border-b border-ink-850 pb-2 font-serif text-sm text-ink-100">
                  {latest.subject}
                </div>
                <div className="letter mt-3 max-h-72 overflow-y-auto pr-2">{latest.body}</div>
                <div className="mt-4 border-t border-ink-850 pt-3">
                  <DisclosureTable disclosures={latest.disclosures} />
                </div>
              </div>
            </section>
          )}

          {memory.length > 0 && (
            <section className="panel">
              <div className="panel-head">
                <h2 className="panel-title">Case file</h2>
                <span className="panel-note">What the sub-agent reads back when it wakes</span>
              </div>
              <ul className="divide-y divide-ink-850">
                {memory.map((entry, index) => {
                  const row = entry as Record<string, string>;
                  return (
                    <li key={index} className="px-5 py-2.5">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="font-mono text-2xs text-ink-500">{row.kind}</span>
                        <span className="text-2xs tabular-nums text-ink-600">
                          {shortDate(row.at)}
                        </span>
                      </div>
                      <p className="mt-0.5 text-2xs text-ink-400">{row.summary}</p>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </div>
      </div>

      {inbound.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">Correspondence received</h2>
            <span className="panel-note">Screened before anything read it</span>
          </div>
          <ul className="divide-y divide-ink-850">
            {inbound.map((message) => {
              const blocked = message.screening?.verdict === "BLOCK";
              return (
                <li key={message.id} className={`px-5 py-3 ${blocked ? "bg-alarm-600/[0.04]" : ""}`}>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className={`text-xs ${blocked ? "text-alarm-400" : "text-ink-300"}`}>
                      {message.subject}
                    </span>
                    <span className="whitespace-nowrap text-2xs tabular-nums text-ink-600">
                      {shortDate(message.received_at)}
                    </span>
                  </div>
                  <p className="mt-0.5 text-2xs text-ink-600">
                    {message.from_address} &middot; {message.handling_note}
                  </p>
                  {blocked && (
                    <div className="mt-2">
                      <FindingList findings={message.screening?.findings ?? []} />
                    </div>
                  )}
                  {!blocked && message.classification && (
                    <p className="mt-1 text-2xs text-ink-500">{message.classification.reasoning}</p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Audit trail</h2>
          <span className="panel-note">{audit.length} records for this institution</span>
        </div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-ink-850 text-left">
              <th className="cell label font-normal">#</th>
              <th className="cell label font-normal">When</th>
              <th className="cell label font-normal">Actor</th>
              <th className="cell label font-normal">Action</th>
              <th className="cell label font-normal">Reasoning</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-850/60">
            {audit.map((entry) => (
              <tr key={entry.id} className="hover:bg-ink-850/40">
                <td className="cell tabular-nums text-ink-600">{entry.seq}</td>
                <td className="cell whitespace-nowrap tabular-nums text-ink-500">
                  {shortDate(entry.at)}
                </td>
                <td className="cell text-ink-500">{entry.actor}</td>
                <td className="cell rule">{entry.action}</td>
                <td className="cell leading-relaxed text-ink-400">{entry.reasoning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
