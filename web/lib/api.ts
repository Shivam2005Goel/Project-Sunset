/**
 * Thin client over services/api.
 *
 * Every page is a server component that fetches here, so the dashboard holds no state of
 * its own and cannot drift from what the backend actually believes. That matters more
 * than it sounds: a dashboard with its own copy of the case state is a dashboard that can
 * show a letter as sent when it was not.
 */

const BASE = process.env.AFTERCARE_API ?? "http://127.0.0.1:8000";

export class ApiDown extends Error {
  constructor(readonly path: string, cause: unknown) {
    super(`Aftercare API unreachable at ${BASE}${path}`);
    this.cause = cause;
  }
}

async function get<T>(path: string): Promise<T> {
  try {
    const response = await fetch(`${BASE}${path}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return (await response.json()) as T;
  } catch (error) {
    throw new ApiDown(path, error);
  }
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(detail.detail ?? "request failed");
  }
  return (await response.json()) as T;
}

/** Never throws. Pages use this so an API that is down renders a message, not a stack trace. */
export async function tryGet<T>(path: string, fallback: T): Promise<T> {
  try {
    return await get<T>(path);
  } catch {
    return fallback;
  }
}

// --- types -------------------------------------------------------------------------

export type CaseState =
  | "DISCOVERED"
  | "PACKET_DRAFTED"
  | "AWAITING_APPROVAL"
  | "SENT"
  | "AWAITING_RESPONSE"
  | "INFO_REQUESTED"
  | "ESCALATED"
  | "CLOSED";

export interface Summary {
  estate_id: string;
  decedent_name: string;
  discovered: number;
  surprises: number;
  closed: number;
  escalated: number;
  pending_approval: number;
  in_flight: number;
  recovered_usd: number;
  injections_blocked: number;
  simulated_date: string | null;
  by_state: Record<string, number>;
}

export interface Clock {
  now: string;
  kind: "system" | "simulated";
  start?: string;
  factor?: number;
  elapsed_days?: number;
}

export interface Estate {
  id: string;
  decedent: { full_name: string; date_of_birth: string; date_of_death: string; last_address: string };
  executor: { full_name: string; email: string; relationship: string };
  jurisdiction: string;
  fictional: boolean;
}

export interface Evidence {
  source_document: string;
  page: number | null;
  excerpt: string;
  kind: string;
}

export interface ObligationNode {
  id: string;
  institution_id: string;
  institution_name: string;
  category: string;
  account_fingerprint: string | null;
  confidence: number;
  discovery_method: "DOCUMENT" | "INFERENCE" | "REGISTRY" | "EXECUTOR";
  is_surprise: boolean;
  evidence: Evidence[];
  estimated_value_usd: number | null;
  recurring_amount_usd: number | null;
  notes: string | null;
  case_id: string | null;
  state: CaseState | null;
  playbook_ref: string | null;
  recovered_usd: number;
}

export interface Disclosure {
  field: string;
  sensitivity: "PUBLIC" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  value: string | null;
  redacted_value: string | null;
  disclosed: boolean;
  justification: string;
  required_by: string | null;
}

export interface Packet {
  id: string;
  institution_name: string;
  recipient: string;
  channel: string;
  subject: string;
  body: string;
  disclosures: Disclosure[];
  playbook_ref: string;
  model_used: string;
  reasoning: string;
  sent_at: string | null;
  approval_id: string | null;
}

export interface Approval {
  id: string;
  estate_id: string;
  case_id: string;
  kind: "OUTBOUND" | "ESCALATION" | "PLAYBOOK_AMENDMENT";
  packet_id: string | null;
  status: "PENDING" | "APPROVED" | "REJECTED";
  summary: string;
  brief: string;
  risk_flags: string[];
  created_at: string;
}

export interface QueueRow {
  approval: Approval;
  institution: string;
  category: string;
  state: CaseState;
  packet: Packet | null;
}

export interface Transition {
  from_state: CaseState;
  to_state: CaseState;
  event: string;
  at: string;
  actor: string;
  reason: string;
  audit_id: string | null;
}

export interface InstitutionCase {
  id: string;
  institution_id: string;
  institution_name: string;
  category: string;
  state: CaseState;
  playbook_ref: string | null;
  history: Transition[];
  opened_at: string;
  closed_at: string | null;
  next_wake_at: string | null;
  follow_ups_sent: number;
  escalation_brief: string | null;
  recovered_amount_usd: number;
  outstanding_requests: string[];
}

export interface ScreenFinding {
  rule: string;
  severity: "low" | "medium" | "high" | "critical";
  layer: string;
  excerpt: string;
  note: string;
}

export interface InboundMessage {
  id: string;
  from_address: string;
  subject: string;
  source: string;
  received_at: string;
  institution_id: string | null;
  handling_note: string;
  screening: { verdict: "ALLOW" | "SANITIZE" | "BLOCK"; findings: ScreenFinding[]; sanitized_text: string } | null;
  classification: { label: string; confidence: number; reasoning: string; requested_documents: string[] } | null;
}

export interface AuditRecord {
  id: string;
  seq: number;
  at: string;
  institution_id: string | null;
  case_id: string | null;
  actor: string;
  action: string;
  reasoning: string;
  payload: Record<string, unknown>;
  digest: string;
}

export interface PlaybookEntry {
  name: string;
  display_name: string;
  category: string;
  latest: string;
  versions: string[];
  notes: Record<string, string>;
}

// --- endpoints ---------------------------------------------------------------------

export const api = {
  health: () => tryGet<Record<string, unknown>>("/health", { status: "unreachable" }),
  clock: () => tryGet<Clock>("/api/clock", { now: "", kind: "system" }),
  estate: () =>
    tryGet<{ estate: Estate; summary: Summary; clock: Clock; model_provider: string } | null>(
      "/api/estate",
      null,
    ),
  obligations: () => tryGet<{ nodes: ObligationNode[] }>("/api/obligations", { nodes: [] }),
  cases: () => tryGet<{ cases: InstitutionCase[] }>("/api/cases", { cases: [] }),
  caseDetail: (id: string) =>
    tryGet<{
      case: InstitutionCase;
      packets: Packet[];
      inbound: InboundMessage[];
      audit: AuditRecord[];
      memory: Record<string, unknown>[];
    } | null>(`/api/cases/${id}`, null),
  approvals: () => tryGet<{ queue: QueueRow[] }>("/api/approvals", { queue: [] }),
  inbound: () =>
    tryGet<{ messages: InboundMessage[]; blocked: number }>("/api/inbound", {
      messages: [],
      blocked: 0,
    }),
  inboundRaw: (id: string) =>
    tryGet<{ raw: string; warning: string; quarantined: boolean } | null>(
      `/api/inbound/${id}/raw`,
      null,
    ),
  audit: () =>
    tryGet<{ records: AuditRecord[]; total: number; chain_verified: boolean }>("/api/audit", {
      records: [],
      total: 0,
      chain_verified: false,
    }),
  registry: () => tryGet<{ playbooks: PlaybookEntry[] }>("/api/registry", { playbooks: [] }),
  amendments: () =>
    tryGet<{ amendments: Record<string, unknown>[] }>("/api/amendments", { amendments: [] }),
  states: () =>
    tryGet<{ states: string[]; transitions: Record<string, string[]>; dormant: string[] }>(
      "/api/states",
      { states: [], transitions: {}, dormant: [] },
    ),
  diff: (left: string, right: string) =>
    tryGet<{ rows: DiffRow[]; left: DiffSide; right: DiffSide } | null>(
      `/api/disclosure-diff?left=${left}&right=${right}`,
      null,
    ),
};

export interface DiffSide {
  packet_id: string;
  recipient: string;
  playbook: string;
}

export interface DiffRow {
  field: string;
  sensitivity: string;
  left: string;
  left_disclosed: boolean;
  right: string;
  right_disclosed: boolean;
  differs: boolean;
}

// --- formatting --------------------------------------------------------------------

export function money(value: number): string {
  return value.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function shortDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function fieldLabel(field: string): string {
  return field.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
