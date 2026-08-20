"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { post } from "@/lib/api";

/**
 * The only interactive control in the dashboard.
 *
 * `decided_by` is required by the API and written into the audit record, so the field is
 * present and pre-filled rather than hidden - the person approving is part of the
 * fiduciary record, and a UI that quietly stamps "system" would undermine the whole
 * point.
 */
export function DecisionButtons({
  approvalId,
  approveLabel = "Approve and send",
  rejectLabel = "Reject",
}: {
  approvalId: string;
  approveLabel?: string;
  rejectLabel?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [decidedBy, setDecidedBy] = useState("Daniel R. Halloran");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showNote, setShowNote] = useState(false);

  async function decide(approved: boolean) {
    setError(null);
    if (!decidedBy.trim()) {
      setError("An approval has to record who made it.");
      return;
    }
    try {
      await post(`/api/approvals/${approvalId}/decide`, {
        approved,
        decided_by: decidedBy.trim(),
        note: note.trim(),
      });
      startTransition(() => router.refresh());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "request failed");
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2">
        <input
          value={decidedBy}
          onChange={(event) => setDecidedBy(event.target.value)}
          aria-label="Deciding executor"
          className="w-44 rounded border border-ink-700 bg-ink-850 px-2 py-1.5 text-xs text-ink-200 placeholder:text-ink-600 focus:border-ink-500 focus:outline-none"
          placeholder="Your name"
        />
        <button
          type="button"
          onClick={() => setShowNote((value) => !value)}
          className="btn btn-quiet"
          disabled={pending}
        >
          Note
        </button>
        <button
          type="button"
          onClick={() => decide(false)}
          className="btn btn-reject"
          disabled={pending}
        >
          {rejectLabel}
        </button>
        <button
          type="button"
          onClick={() => decide(true)}
          className="btn btn-approve"
          disabled={pending}
        >
          {pending ? "Recording..." : approveLabel}
        </button>
      </div>

      {showNote && (
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={2}
          placeholder="Why you decided this way. Goes into the audit record."
          className="w-96 rounded border border-ink-700 bg-ink-850 px-2 py-1.5 text-xs text-ink-200 placeholder:text-ink-600 focus:border-ink-500 focus:outline-none"
        />
      )}

      {error && <p className="text-2xs text-alarm-400">{error}</p>}
    </div>
  );
}
