import Link from "next/link";
import { api, money, shortDate } from "@/lib/api";
import { Empty, InstitutionLink, ObligationGraph, Stat, StateBadge } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function Overview() {
  const [estate, obligations, cases] = await Promise.all([
    api.estate(),
    api.obligations(),
    api.cases(),
  ]);

  if (!estate) {
    return (
      <Empty>
        No estate found. Start the API with{" "}
        <span className="font-mono text-ink-300">python tasks.py dev</span>, then seed the
        fictional estate with <span className="font-mono text-ink-300">python tasks.py seed</span>.
      </Empty>
    );
  }

  const { summary, estate: record } = estate;
  const surprises = obligations.nodes.filter((n) => n.is_surprise);
  const escalated = cases.cases.filter((c) => c.state === "ESCALATED");

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="font-serif text-xl text-ink-100">{record.decedent.full_name}</h1>
          <p className="mt-1 text-xs text-ink-500">
            Died {shortDate(record.decedent.date_of_death)} &middot; {record.decedent.last_address}
            {" "}&middot; Executor: {record.executor.full_name}
          </p>
        </div>
        <p className="text-2xs text-ink-600">
          Reasoning by <span className="font-mono text-ink-400">{estate.model_provider}</span>
        </p>
      </header>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat
          label="Obligations"
          value={summary.discovered}
          note={`${summary.surprises} nobody listed`}
        />
        <Stat label="Closed" value={summary.closed} tone="good" note="Confirmed by the institution" />
        <Stat
          label="Escalated"
          value={summary.escalated}
          tone={summary.escalated ? "alarm" : "neutral"}
          note="Waiting on a human decision"
        />
        <Stat
          label="Awaiting approval"
          value={summary.pending_approval}
          tone={summary.pending_approval ? "warn" : "neutral"}
          note="Nothing sends without one"
        />
        <Stat
          label="Recovered"
          value={money(summary.recovered_usd)}
          tone="good"
          note="Money the family would not have found"
        />
      </section>

      {summary.injections_blocked > 0 && (
        <Link
          href="/inbound"
          className="flex items-center justify-between rounded-lg border border-alarm-500/30 bg-alarm-600/5 px-5 py-3 transition-colors hover:bg-alarm-600/10"
        >
          <div>
            <div className="text-xs text-alarm-400">
              {summary.injections_blocked} inbound{" "}
              {summary.injections_blocked === 1 ? "message" : "messages"} blocked at the screen
            </div>
            <div className="mt-0.5 text-2xs text-ink-500">
              Instruction-shaped content in third-party correspondence. None of it reached a
              model; the originals are quarantined for you to read.
            </div>
          </div>
          <span className="text-2xs text-ink-500">Review &rarr;</span>
        </Link>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">Obligation graph</h2>
            <span className="panel-note">
              Reconstructed from {record.decedent.full_name.split(" ")[0]}&rsquo;s documents
            </span>
          </div>
          <ObligationGraph nodes={obligations.nodes} />
        </section>

        <div className="space-y-6">
          {surprises.length > 0 && (
            <section className="panel">
              <div className="panel-head">
                <h2 className="panel-title">Found without being listed</h2>
                <span className="panel-note">{surprises.length} of {summary.discovered}</span>
              </div>
              <ul className="divide-y divide-ink-850">
                {surprises.map((node) => (
                  <li key={node.id} className="px-5 py-3">
                    <div className="flex items-baseline justify-between gap-3">
                      <InstitutionLink caseId={node.case_id} name={node.institution_name} />
                      <StateBadge state={node.state} />
                    </div>
                    <p className="mt-1 text-2xs leading-snug text-ink-500">{node.notes}</p>
                    <div className="mt-1.5 flex gap-4 text-2xs text-ink-600">
                      <span>
                        {node.discovery_method === "REGISTRY"
                          ? "State registry match"
                          : "Inferred from recurring transactions"}
                      </span>
                      <span className="tabular-nums">
                        confidence {(node.confidence * 100).toFixed(0)}%
                      </span>
                      {node.estimated_value_usd ? (
                        <span className="tabular-nums text-sage-300">
                          {money(node.estimated_value_usd)}
                        </span>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {escalated.length > 0 && (
            <section className="panel">
              <div className="panel-head">
                <h2 className="panel-title">Waiting on you</h2>
                <span className="panel-note">The agent stopped rather than guess</span>
              </div>
              <ul className="divide-y divide-ink-850">
                {escalated.map((item) => (
                  <li key={item.id} className="px-5 py-3">
                    <InstitutionLink caseId={item.id} name={item.institution_name} />
                    <p className="mt-1 text-2xs leading-relaxed text-ink-400">
                      {item.escalation_brief}
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Every institution</h2>
          <span className="panel-note">
            Ordered by urgency: pensions and insurers first, subscriptions last
          </span>
        </div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-ink-850 text-left">
              <th className="cell label font-normal">Institution</th>
              <th className="cell label font-normal">Category</th>
              <th className="cell label font-normal">State</th>
              <th className="cell label font-normal">Playbook</th>
              <th className="cell label font-normal">Found by</th>
              <th className="cell label font-normal text-right">Follow-ups</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-850/60">
            {obligations.nodes.map((node) => {
              const item = cases.cases.find((c) => c.id === node.case_id);
              const generic = node.playbook_ref?.startsWith("generic-closure");
              return (
                <tr key={node.id} className="hover:bg-ink-850/40">
                  <td className="cell">
                    <InstitutionLink caseId={node.case_id} name={node.institution_name} />
                    {node.account_fingerprint && (
                      <div className="mt-0.5 font-mono text-2xs text-ink-600">
                        {node.account_fingerprint}
                      </div>
                    )}
                  </td>
                  <td className="cell text-ink-500">
                    {node.category.replace(/_/g, " ").toLowerCase()}
                  </td>
                  <td className="cell">
                    <StateBadge state={node.state} />
                  </td>
                  <td className="cell">
                    <span className={generic ? "font-mono text-2xs text-ink-600" : "font-mono text-2xs text-ink-400"}>
                      {node.playbook_ref}
                    </span>
                    {generic && (
                      <div className="text-2xs text-ink-600">generic template</div>
                    )}
                  </td>
                  <td className="cell text-2xs text-ink-500">
                    {node.discovery_method === "DOCUMENT"
                      ? node.evidence[0]?.source_document
                      : node.discovery_method === "REGISTRY"
                        ? "unclaimed property registry"
                        : "recurring transactions"}
                  </td>
                  <td className="cell text-right tabular-nums text-ink-500">
                    {item?.follow_ups_sent ?? 0}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
