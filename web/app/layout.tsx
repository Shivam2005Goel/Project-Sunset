import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { api } from "@/lib/api";

export const metadata: Metadata = {
  title: "Aftercare",
  description: "An autonomous agent that handles the bureaucracy of death.",
};

export const dynamic = "force-dynamic";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/approvals", label: "Approvals" },
  { href: "/inbound", label: "Inbound mail" },
  { href: "/registry", label: "Playbooks" },
  { href: "/audit", label: "Audit" },
];

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const [clock, estate] = await Promise.all([api.clock(), api.estate()]);
  const pending = estate?.summary.pending_approval ?? 0;

  return (
    <html lang="en">
      <body className="min-h-screen">
        {/* Invariant 6, on screen, always. Nobody should have to be told twice that the
            person in this file is invented. */}
        <div className="border-b border-amber-500/20 bg-amber-500/5 px-6 py-1.5 text-center text-2xs tracking-wide text-amber-400/80">
          Demonstration data. Fictional decedent, fabricated documents, invented institutions.
          Aftercare drafts; the executor decides. Not legal advice.
        </div>

        <div className="mx-auto flex max-w-[1400px] gap-8 px-6 py-6">
          <aside className="w-56 shrink-0">
            <div className="sticky top-6">
              <Link href="/" className="block">
                <div className="font-serif text-lg tracking-tight text-ink-100">Aftercare</div>
                <div className="mt-0.5 text-2xs leading-snug text-ink-500">
                  Estate of {estate?.estate.decedent.full_name ?? "-"}
                </div>
              </Link>

              <nav className="mt-6 space-y-0.5">
                {NAV.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="flex items-center justify-between rounded px-2.5 py-1.5 text-xs text-ink-400 transition-colors hover:bg-ink-900 hover:text-ink-200"
                  >
                    {item.label}
                    {item.href === "/approvals" && pending > 0 && (
                      <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-2xs tabular-nums text-amber-400">
                        {pending}
                      </span>
                    )}
                  </Link>
                ))}
              </nav>

              <SimulatedClock clock={clock} />

              <div className="mt-6 border-t border-ink-850 pt-4 text-2xs leading-relaxed text-ink-600">
                <div className="label mb-1.5">Boundaries</div>
                <p>Never signs. Never asserts authority.</p>
                <p className="mt-1">Never makes a legal determination.</p>
                <p className="mt-1">
                  <span className="text-ink-400">Every outbound letter requires approval.</span>
                </p>
                <p className="mt-1">Ambiguity escalates within one turn.</p>
              </div>
            </div>
          </aside>

          <main className="min-w-0 flex-1 pb-16">{children}</main>
        </div>
      </body>
    </html>
  );
}

function SimulatedClock({ clock }: { clock: Awaited<ReturnType<typeof api.clock>> }) {
  if (!clock.now) {
    return (
      <div className="mt-6 rounded border border-ink-850 px-3 py-2.5 text-2xs text-ink-600">
        API unreachable. Run <span className="font-mono text-ink-400">python tasks.py dev</span>.
      </div>
    );
  }

  const date = new Date(clock.now);
  const simulated = clock.kind === "simulated";

  return (
    <div className="mt-6 rounded border border-ink-850 bg-ink-900 px-3 py-2.5">
      <div className="label">{simulated ? "Simulated date" : "Date"}</div>
      <div className="mt-1 font-mono text-sm tabular-nums text-ink-200">
        {date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}
      </div>
      {simulated && (
        <div className="mt-1 text-2xs text-ink-500">
          Day {clock.elapsed_days ?? 0} of the estate
          {clock.factor && clock.factor > 1 ? ` - replayed at ${clock.factor}x` : ""}
        </div>
      )}
    </div>
  );
}
