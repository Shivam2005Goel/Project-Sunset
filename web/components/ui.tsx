/** Shared display components. Server components except where a page needs interaction. */

import Link from "next/link";
import type { CaseState, Disclosure, ObligationNode, ScreenFinding } from "@/lib/api";
import { fieldLabel, money } from "@/lib/api";

// --- state ---------------------------------------------------------------------------

const STATE_STYLE: Record<CaseState, string> = {
  DISCOVERED: "border-ink-600 text-ink-400",
  PACKET_DRAFTED: "border-ink-600 text-ink-300",
  AWAITING_APPROVAL: "border-amber-500/50 text-amber-400",
  SENT: "border-ink-500 text-ink-300",
  AWAITING_RESPONSE: "border-ink-500 text-ink-400",
  INFO_REQUESTED: "border-amber-500/40 text-amber-400",
  ESCALATED: "border-alarm-500/60 text-alarm-400",
  CLOSED: "border-sage-500/50 text-sage-300",
};

const STATE_LABEL: Record<CaseState, string> = {
  DISCOVERED: "Discovered",
  PACKET_DRAFTED: "Drafted",
  AWAITING_APPROVAL: "Awaiting approval",
  SENT: "Sent",
  AWAITING_RESPONSE: "Awaiting response",
  INFO_REQUESTED: "Information requested",
  ESCALATED: "Escalated",
  CLOSED: "Closed",
};

export function StateBadge({ state }: { state: CaseState | null }) {
  if (!state) return <span className="text-2xs text-ink-600">-</span>;
  return (
    <span
      className={`inline-block whitespace-nowrap rounded border px-2 py-0.5 text-2xs ${STATE_STYLE[state]}`}
    >
      {STATE_LABEL[state]}
    </span>
  );
}

// --- numbers -------------------------------------------------------------------------

export function Stat({
  label,
  value,
  note,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  note?: string;
  tone?: "neutral" | "good" | "warn" | "alarm";
}) {
  const toneClass = {
    neutral: "text-ink-200",
    good: "text-sage-300",
    warn: "text-amber-400",
    alarm: "text-alarm-400",
  }[tone];

  return (
    <div className="panel px-5 py-4">
      <div className="label">{label}</div>
      <div className={`mt-1.5 text-2xl font-light tabular-nums ${toneClass}`}>{value}</div>
      {note && <div className="mt-1 text-2xs leading-snug text-ink-500">{note}</div>}
    </div>
  );
}

// --- the obligation graph ------------------------------------------------------------

const CATEGORY_ORDER = [
  "PENSION",
  "LIFE_INSURANCE",
  "UNCLAIMED_PROPERTY",
  "BANK",
  "BROKERAGE",
  "MORTGAGE",
  "CREDIT_CARD",
  "UTILITY",
  "TELECOM",
  "GOVERNMENT",
  "SUBSCRIPTION",
  "OTHER",
];

/**
 * The estate at the centre, its obligations around it, the ones nobody listed marked.
 *
 * Rendered as inline SVG rather than a chart library: it is one radial layout, it has to
 * survive being screen-recorded, and a dependency that renders differently in a headless
 * browser is a bad thing to discover on day 11.
 */
export function ObligationGraph({ nodes }: { nodes: ObligationNode[] }) {
  const size = 640;
  const centre = size / 2;
  const ordered = [...nodes].sort(
    (a, b) =>
      CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category) ||
      a.institution_name.localeCompare(b.institution_name),
  );

  const radius = 232;
  const points = ordered.map((node, index) => {
    const angle = (index / ordered.length) * Math.PI * 2 - Math.PI / 2;
    return {
      node,
      x: centre + Math.cos(angle) * radius,
      y: centre + Math.sin(angle) * radius,
      angle,
    };
  });

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-auto w-full" role="img"
           aria-label={`Obligation graph: ${nodes.length} institutions`}>
        <circle cx={centre} cy={centre} r={radius} className="fill-none stroke-ink-850" strokeWidth={1} />

        {points.map(({ node, x, y }) => (
          <line
            key={`edge-${node.id}`}
            x1={centre}
            y1={centre}
            x2={x}
            y2={y}
            strokeWidth={node.is_surprise ? 1.4 : 0.8}
            className={node.is_surprise ? "stroke-amber-500/60" : "stroke-ink-800"}
            strokeDasharray={node.discovery_method === "REGISTRY" ? "3 3" : undefined}
          />
        ))}

        <circle cx={centre} cy={centre} r={46} className="fill-ink-850 stroke-ink-700" strokeWidth={1} />
        <text x={centre} y={centre - 4} textAnchor="middle" className="fill-ink-300 text-[11px]">
          Estate
        </text>
        <text x={centre} y={centre + 12} textAnchor="middle" className="fill-ink-500 text-[10px]">
          {nodes.length} obligations
        </text>

        {points.map(({ node, x, y, angle }) => {
          const closed = node.state === "CLOSED";
          const escalated = node.state === "ESCALATED";
          const fill = escalated
            ? "fill-alarm-500"
            : closed
              ? "fill-sage-500"
              : node.is_surprise
                ? "fill-amber-500"
                : "fill-ink-600";
          const onRight = Math.cos(angle) > -0.1;
          const labelX = x + (onRight ? 12 : -12);

          return (
            <g key={node.id}>
              <circle cx={x} cy={y} r={node.is_surprise ? 6.5 : 5} className={`${fill} stroke-ink-950`} strokeWidth={1.5} />
              {node.is_surprise && (
                <circle cx={x} cy={y} r={11} className="fill-none stroke-amber-500/40" strokeWidth={1} />
              )}
              <text
                x={labelX}
                y={y + 3.5}
                textAnchor={onRight ? "start" : "end"}
                className={`text-[9.5px] ${node.is_surprise ? "fill-amber-400" : "fill-ink-400"}`}
              >
                {node.institution_name.length > 30
                  ? `${node.institution_name.slice(0, 28)}...`
                  : node.institution_name}
              </text>
            </g>
          );
        })}
      </svg>

      <Legend />
    </div>
  );
}

function Legend() {
  const items: [string, string][] = [
    ["bg-ink-600", "From an uploaded document"],
    ["bg-amber-500", "Found by the agent - nobody listed it"],
    ["bg-sage-500", "Closed"],
    ["bg-alarm-500", "Escalated to the executor"],
  ];
  return (
    <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2 px-5 pb-4">
      {items.map(([colour, label]) => (
        <span key={label} className="flex items-center gap-2 text-2xs text-ink-500">
          <span className={`h-2 w-2 rounded-full ${colour}`} />
          {label}
        </span>
      ))}
    </div>
  );
}

// --- disclosure ----------------------------------------------------------------------

const SENSITIVITY_STYLE: Record<string, string> = {
  PUBLIC: "text-ink-500",
  LOW: "text-ink-400",
  MEDIUM: "text-amber-400/80",
  HIGH: "text-amber-400",
  CRITICAL: "text-alarm-400",
};

/**
 * What this recipient gets, and what they do not.
 *
 * The withheld rows are the point. An executor approving a letter needs to see what was
 * held back as much as what was enclosed, which is why they are rendered rather than
 * filtered out.
 */
export function DisclosureTable({ disclosures }: { disclosures: Disclosure[] }) {
  const disclosed = disclosures.filter((d) => d.disclosed);
  const withheld = disclosures.filter((d) => !d.disclosed);

  return (
    <div className="space-y-4">
      <Group title={`Enclosed (${disclosed.length})`} rows={disclosed} disclosed />
      <Group title={`Withheld (${withheld.length})`} rows={withheld} disclosed={false} />
    </div>
  );
}

function Group({ title, rows, disclosed }: { title: string; rows: Disclosure[]; disclosed: boolean }) {
  if (rows.length === 0) return null;
  return (
    <div>
      <div className="label mb-2">{title}</div>
      <div className="space-y-1.5">
        {rows.map((row) => (
          <div key={row.field} className="rounded border border-ink-800 bg-ink-850/60 px-3 py-2">
            <div className="flex items-baseline justify-between gap-3">
              <span className={`text-xs ${disclosed ? "text-ink-200" : "text-ink-500 line-through"}`}>
                {fieldLabel(row.field)}
              </span>
              <span className={`text-2xs uppercase tracking-wide ${SENSITIVITY_STYLE[row.sensitivity]}`}>
                {row.sensitivity}
              </span>
            </div>
            <div className="mt-1 font-mono text-2xs text-ink-400">
              {disclosed ? row.value : row.redacted_value}
            </div>
            <div className="mt-1 text-2xs leading-snug text-ink-600">{row.justification}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- guardrail findings --------------------------------------------------------------

const SEVERITY_STYLE: Record<string, string> = {
  low: "border-ink-700 text-ink-400",
  medium: "border-amber-500/40 text-amber-400",
  high: "border-alarm-500/50 text-alarm-400",
  critical: "border-alarm-500 bg-alarm-600/10 text-alarm-400",
};

export function FindingList({ findings }: { findings: ScreenFinding[] }) {
  if (findings.length === 0) {
    return <p className="text-2xs text-ink-600">No findings. This letter read as ordinary correspondence.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {findings.map((finding, index) => (
        <li
          key={`${finding.rule}-${index}`}
          className={`rounded border px-3 py-2 ${SEVERITY_STYLE[finding.severity]}`}
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="rule">{finding.rule}</span>
            <span className="text-2xs uppercase tracking-widest opacity-70">
              {finding.severity} - {finding.layer} layer
            </span>
          </div>
          <p className="mt-1 text-2xs leading-snug text-ink-300">{finding.note}</p>
          {finding.excerpt && (
            <p className="mt-1 truncate font-mono text-2xs text-ink-500">"{finding.excerpt}"</p>
          )}
        </li>
      ))}
    </ul>
  );
}

// --- misc ----------------------------------------------------------------------------

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="panel px-6 py-10 text-center text-xs text-ink-500">{children}</div>
  );
}

export function InstitutionLink({ caseId, name }: { caseId: string | null; name: string }) {
  if (!caseId) return <span>{name}</span>;
  return (
    <Link href={`/institutions/${caseId}`} className="text-ink-200 underline-offset-2 hover:underline">
      {name}
    </Link>
  );
}

export function Money({ value, tone = "neutral" }: { value: number; tone?: "neutral" | "good" }) {
  return (
    <span className={`tabular-nums ${tone === "good" ? "text-sage-300" : "text-ink-300"}`}>
      {money(value)}
    </span>
  );
}
