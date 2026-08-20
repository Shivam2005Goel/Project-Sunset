import { api } from "@/lib/api";
import { Empty } from "@/components/ui";

export const dynamic = "force-dynamic";

/**
 * The registry.
 *
 * The reuse argument, made visible. The obligation graph is rebuilt for every estate; the
 * playbooks are encoded once and get better every time an institution surprises one. That
 * is what "cataloged for cross-department use" means in practice - not a directory
 * listing, a compounding asset.
 */
export default async function Registry() {
  const [{ playbooks }, { amendments }] = await Promise.all([api.registry(), api.amendments()]);

  if (playbooks.length === 0) {
    return (
      <div className="space-y-6">
        <Header />
        <Empty>
          No playbooks published. Run{" "}
          <span className="font-mono text-ink-300">python tasks.py publish-playbooks</span>.
        </Empty>
      </div>
    );
  }

  const specific = playbooks.filter((p) => p.name !== "generic-closure");
  const generic = playbooks.find((p) => p.name === "generic-closure");

  return (
    <div className="space-y-6">
      <Header />

      <section className="grid gap-3 md:grid-cols-2">
        {specific.map((playbook) => (
          <article key={playbook.name} className="panel px-5 py-4">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="text-sm text-ink-100">{playbook.display_name}</h2>
              <span className="font-mono text-2xs text-sage-300">v{playbook.latest}</span>
            </div>
            <div className="mt-0.5 text-2xs uppercase tracking-wide text-ink-600">
              {playbook.category.replace(/_/g, " ").toLowerCase()}
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {playbook.versions.map((version) => (
                <span
                  key={version}
                  className={`rounded border px-1.5 py-0.5 font-mono text-2xs ${
                    version === playbook.latest
                      ? "border-sage-500/40 text-sage-300"
                      : "border-ink-700 text-ink-500"
                  }`}
                >
                  {version}
                </span>
              ))}
            </div>
            {playbook.notes?.[playbook.latest] && (
              <p className="mt-2 text-2xs leading-snug text-ink-500">
                {playbook.notes[playbook.latest]}
              </p>
            )}
            <p className="mt-2 font-mono text-2xs text-ink-600">{playbook.name}</p>
          </article>
        ))}
      </section>

      {amendments.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2 className="panel-title">Amendments proposed by sub-agents</h2>
            <span className="panel-note">
              An institution asked for something the playbook did not list
            </span>
          </div>
          <ul className="divide-y divide-ink-850">
            {amendments.map((amendment) => {
              const record = amendment as Record<string, string | string[]>;
              const published = record.status === "PUBLISHED";
              return (
                <li key={String(record.id)} className="px-5 py-3">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-mono text-xs text-ink-300">
                      {String(record.playbook_name)}{" "}
                      <span className="text-ink-600">v{String(record.from_version)}</span>
                      {" -> "}
                      <span className="text-sage-300">v{String(record.proposed_version)}</span>
                    </span>
                    <span
                      className={`text-2xs uppercase tracking-widest ${
                        published ? "text-sage-300" : "text-amber-400"
                      }`}
                    >
                      {published ? "published" : "awaiting approval"}
                    </span>
                  </div>
                  <ul className="mt-1.5 space-y-0.5">
                    {(record.add_required_documents as string[]).map((document) => (
                      <li key={document} className="font-mono text-2xs text-sage-300">
                        + {document}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1.5 text-2xs leading-snug text-ink-500">
                    {String(record.rationale)}
                  </p>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {generic && (
        <section className="panel px-5 py-4">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm text-ink-300">The long tail</h2>
            <span className="font-mono text-2xs text-ink-500">
              {generic.name}@{generic.latest}
            </span>
          </div>
          <p className="mt-2 max-w-3xl text-2xs leading-relaxed text-ink-500">
            Institutions without a dedicated playbook fall back to a generic closure template
            plus human review. The template asks the institution to state its own requirements
            rather than guessing at them, and the executor should expect one extra round trip.
            Every use of it is a candidate for a real playbook - which is how the catalog grows.
          </p>
        </section>
      )}
    </div>
  );
}

function Header() {
  return (
    <header>
      <h1 className="font-serif text-xl text-ink-100">Institution playbooks</h1>
      <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-500">
        One institution&rsquo;s closure process, encoded once and versioned. The obligation
        graph is rebuilt for every estate; these are not. When a sub-agent meets a demand its
        playbook did not anticipate, it proposes a new version - and every future estate that
        touches that institution starts from the better one.
      </p>
    </header>
  );
}
