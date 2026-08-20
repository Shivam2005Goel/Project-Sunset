import { api } from "@/lib/api";
import { Empty } from "@/components/ui";

export const dynamic = "force-dynamic";

/**
 * The fiduciary record.
 *
 * Not ops garnish. An executor carries fiduciary duty and can be sued, so this is a legal
 * artifact: every action taken on the estate's behalf, the reasoning behind it, and a
 * hash chain that makes rewriting any of it detectable.
 */
export default async function Audit() {
  const { records, total, chain_verified } = await api.audit();

  if (records.length === 0) {
    return (
      <div className="space-y-6">
        <Header total={0} verified={false} />
        <Empty>
          No audit records yet. Run <span className="font-mono text-ink-300">python tasks.py seed</span>.
        </Empty>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Header total={total} verified={chain_verified} />

      <section className="panel overflow-hidden">
        <div className="panel-head">
          <h2 className="panel-title">Every action, and why</h2>
          <span className="panel-note">
            Showing the most recent {records.length} of {total}
          </span>
        </div>
        <div className="max-h-[70vh] overflow-y-auto">
          <table className="w-full">
            <thead className="sticky top-0 bg-ink-900">
              <tr className="border-b border-ink-800 text-left">
                <th className="cell label font-normal">#</th>
                <th className="cell label font-normal">When</th>
                <th className="cell label font-normal">Actor</th>
                <th className="cell label font-normal">Action</th>
                <th className="cell label font-normal">Institution</th>
                <th className="cell label font-normal">Reasoning</th>
                <th className="cell label font-normal">Digest</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-850/60">
              {[...records].reverse().map((record) => (
                <tr key={record.id} className="hover:bg-ink-850/40">
                  <td className="cell tabular-nums text-ink-600">{record.seq}</td>
                  <td className="cell whitespace-nowrap tabular-nums text-ink-500">
                    {new Date(record.at).toLocaleString("en-GB", {
                      day: "2-digit",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </td>
                  <td className="cell text-ink-400">{record.actor}</td>
                  <td className="cell">
                    <span className={`rule ${actionTone(record.action)}`}>{record.action}</span>
                  </td>
                  <td className="cell text-ink-500">{record.institution_id ?? "-"}</td>
                  <td className="cell max-w-[36rem] leading-relaxed text-ink-300">
                    {record.reasoning}
                  </td>
                  <td className="cell font-mono text-2xs text-ink-600" title={record.digest}>
                    {record.digest.slice(0, 10)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function actionTone(action: string): string {
  if (action.startsWith("inbound.blocked") || action.startsWith("outbound.refused")) {
    return "text-alarm-400";
  }
  if (action.startsWith("outbound.sent") || action.startsWith("approval.")) {
    return "text-amber-400";
  }
  if (action.endsWith("CLOSED")) return "text-sage-300";
  return "text-ink-400";
}

function Header({ total, verified }: { total: number; verified: boolean }) {
  return (
    <header className="flex items-end justify-between gap-6">
      <div>
        <h1 className="font-serif text-xl text-ink-100">Fiduciary audit</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-500">
          Every state transition carries the reasoning behind it, and every record is chained
          to its predecessor by SHA-256. Editing one record invalidates every record after it,
          which is what makes this defensible in front of a probate court rather than merely
          tidy.
        </p>
      </div>
      <div
        className={`shrink-0 rounded border px-4 py-2.5 text-right ${
          verified
            ? "border-sage-500/40 bg-sage-500/5"
            : "border-alarm-500/50 bg-alarm-600/10"
        }`}
      >
        <div className="label">Chain integrity</div>
        <div
          className={`mt-0.5 text-sm ${verified ? "text-sage-300" : "text-alarm-400"}`}
        >
          {verified ? "Verified" : "Broken"}
        </div>
        <div className="mt-0.5 text-2xs tabular-nums text-ink-600">{total} records</div>
      </div>
    </header>
  );
}
