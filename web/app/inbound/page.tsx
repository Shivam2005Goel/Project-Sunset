import { api } from "@/lib/api";
import { Empty, FindingList } from "@/components/ui";

export const dynamic = "force-dynamic";

/**
 * Every letter the estate received, and what the screen made of it.
 *
 * Blocked messages get the most space on the page. That is the right emphasis: the
 * interesting event is not that ordinary mail was classified correctly, it is that a
 * letter arrived carrying instructions for a machine and stopped here.
 */
export default async function Inbound() {
  const { messages, blocked } = await api.inbound();

  if (messages.length === 0) {
    return (
      <div className="space-y-6">
        <Header total={0} blocked={0} />
        <Empty>
          No inbound mail yet. Run{" "}
          <span className="font-mono text-ink-300">python tasks.py demo</span> to replay six
          simulated weeks of correspondence through the live pipeline.
        </Empty>
      </div>
    );
  }

  const blockedMessages = messages.filter((m) => m.screening?.verdict === "BLOCK");
  const ordinary = messages.filter((m) => m.screening?.verdict !== "BLOCK");

  return (
    <div className="space-y-6">
      <Header total={messages.length} blocked={blocked} />

      {blockedMessages.length > 0 && (
        <section className="space-y-3">
          <h2 className="label">Blocked before reaching a model ({blockedMessages.length})</h2>
          {blockedMessages.map((message) => (
            <article
              key={message.id}
              className="panel border-alarm-500/30 bg-alarm-600/[0.04]"
            >
              <div className="panel-head border-alarm-500/20">
                <div className="min-w-0">
                  <h3 className="panel-title truncate">{message.subject}</h3>
                  <p className="panel-note mt-0.5">
                    From {message.from_address} &middot;{" "}
                    {message.source === "SCAN" ? "scanned document" : "email"} &middot;{" "}
                    {new Date(message.received_at).toLocaleDateString("en-GB", {
                      day: "2-digit",
                      month: "short",
                    })}
                  </p>
                </div>
                <span className="shrink-0 rounded border border-alarm-500/50 px-2 py-0.5 text-2xs uppercase tracking-widest text-alarm-400">
                  Blocked
                </span>
              </div>

              <div className="px-5 py-4">
                <FindingList findings={message.screening?.findings ?? []} />
                <div className="mt-4 grid gap-3 border-t border-ink-850 pt-3 text-2xs text-ink-500 sm:grid-cols-2">
                  <p>
                    <span className="text-ink-400">Case state unchanged.</span> A blocked message
                    moves nothing; the institution&rsquo;s case sits exactly where it was.
                  </p>
                  <p>
                    <span className="text-ink-400">No model saw this.</span> The screen produced no
                    sanitized projection, so there was nothing for a prompt to interpolate. The
                    original is quarantined for you to read.
                  </p>
                </div>
              </div>
            </article>
          ))}
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2 className="panel-title">Ordinary correspondence</h2>
          <span className="panel-note">
            Screened, sanitized, classified, routed back into the state machine
          </span>
        </div>
        <table className="w-full">
          <thead>
            <tr className="border-b border-ink-850 text-left">
              <th className="cell label font-normal">Received</th>
              <th className="cell label font-normal">From</th>
              <th className="cell label font-normal">Subject</th>
              <th className="cell label font-normal">Classified as</th>
              <th className="cell label font-normal">What happened</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-850/60">
            {ordinary.map((message) => (
              <tr key={message.id} className="hover:bg-ink-850/40">
                <td className="cell whitespace-nowrap tabular-nums text-ink-500">
                  {new Date(message.received_at).toLocaleDateString("en-GB", {
                    day: "2-digit",
                    month: "short",
                  })}
                </td>
                <td className="cell max-w-[14rem] truncate text-ink-500">
                  {message.from_address}
                </td>
                <td className="cell max-w-[20rem] truncate text-ink-300">{message.subject}</td>
                <td className="cell">
                  <ClassificationBadge label={message.classification?.label ?? "-"} />
                  {message.classification?.requested_documents?.length ? (
                    <div className="mt-1 text-2xs text-ink-600">
                      {message.classification.requested_documents.join(", ")}
                    </div>
                  ) : null}
                </td>
                <td className="cell text-ink-500">{message.handling_note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function Header({ total, blocked }: { total: number; blocked: number }) {
  return (
    <header>
      <h1 className="font-serif text-xl text-ink-100">Inbound mail</h1>
      <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-500">
        Every inbound message is untrusted third-party content. A scanned letter is an
        injection vector, so screening runs <em>before</em> any model sees the text - not
        after, and not with a model.
        {total > 0 && ` ${total} received, ${blocked} blocked.`}
      </p>
    </header>
  );
}

const CLASS_STYLE: Record<string, string> = {
  ACKNOWLEDGEMENT: "text-ink-400",
  DOCUMENT_REQUEST: "text-amber-400",
  REJECTION: "text-alarm-400",
  COMPLETION: "text-sage-300",
  IRRELEVANT: "text-ink-600",
  UNKNOWN: "text-alarm-400",
};

function ClassificationBadge({ label }: { label: string }) {
  return (
    <span className={`text-2xs uppercase tracking-wide ${CLASS_STYLE[label] ?? "text-ink-500"}`}>
      {label.replace(/_/g, " ").toLowerCase()}
    </span>
  );
}
