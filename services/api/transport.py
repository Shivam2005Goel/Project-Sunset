"""The only code in this repository that sends anything to the outside world.

Read that sentence again, because it is the entire safety argument. There is no second
transport, no "quick send" helper, no direct SMTP call in a service, no debug path. If
you need to send something, you go through `services/api/approvals.py`, which is the only
module permitted to call `deliver()` - `tests/test_policy.py` fails the build if anything
else imports it.

`deliver()` re-checks the approval itself rather than trusting its caller. Defence in
depth costs one function call here and removes an entire class of future mistake: the
gate cannot be bypassed by a caller who forgot, or by a caller who was talked into it.
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.audit.sink import AuditLog, get_audit_log
from packages.core.clock import now
from packages.core.config import Settings, get_settings
from packages.core.logging import get_logger
from packages.core.models import ApprovalRequest, Channel, ClosurePacket, InstitutionCase
from packages.core.telemetry import span
from packages.guardrails.policy import assert_sendable, assert_state_allows_send

log = get_logger("transport")


class Transport:
    """Writes to the outbox. In cloud mode the same method posts to the Gmail API."""

    def __init__(self, settings: Settings | None = None) -> None:
        # Held only when explicitly injected. Caching `get_settings()` here would let a
        # long-lived instance keep writing to a data directory that has since moved -
        # harmless-looking, and the reason a letter can appear to send while landing
        # nowhere anyone is looking.
        self._override = settings

    @property
    def settings(self) -> Settings:
        return self._override or get_settings()

    def _write_local(self, packet: ClosurePacket) -> Path:
        directory = self.settings.outbox_dir
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{packet.drafted_at:%Y%m%d}-{packet.id}.txt"
        path.write_text(
            "\n".join(
                [
                    f"To: {packet.recipient}",
                    f"From: {self.settings.executor_email}",
                    f"Subject: {packet.subject}",
                    f"X-Aftercare-Packet: {packet.id}",
                    f"X-Aftercare-Approval: {packet.approval_id}",
                    f"X-Aftercare-Playbook: {packet.playbook_ref}",
                    f"X-Aftercare-Disclosed: {', '.join(packet.disclosed_fields)}",
                    f"X-Aftercare-Withheld: {', '.join(packet.withheld_fields)}",
                    "",
                    packet.body,
                ]
            ),
            encoding="utf-8",
        )
        manifest = path.with_suffix(".manifest.json")
        manifest.write_text(
            json.dumps(
                {
                    "packet_id": packet.id,
                    "approval_id": packet.approval_id,
                    "recipient": packet.recipient,
                    "channel": packet.channel.value,
                    "disclosed": packet.disclosed_fields,
                    "withheld": packet.withheld_fields,
                    "playbook": packet.playbook_ref,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def _send_email(self, packet: ClosurePacket) -> str:  # pragma: no cover - cloud only
        """Gmail send, as the *executor*, never as the deceased.

        README section 5: `gmail.send` is scoped to the executor's own account. Sending
        as a dead person would be both a fraud risk and, frankly, grotesque.
        """
        import base64
        from email.mime.text import MIMEText

        from googleapiclient.discovery import build

        service = build("gmail", "v1")
        message = MIMEText(packet.body)
        message["To"] = packet.recipient
        message["From"] = self.settings.executor_email
        message["Subject"] = packet.subject
        message["X-Aftercare-Approval"] = packet.approval_id or ""
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return result.get("id", "")

    def transmit(self, packet: ClosurePacket) -> str:
        if self.settings.is_cloud and packet.channel is Channel.EMAIL:  # pragma: no cover
            return self._send_email(packet)
        return str(self._write_local(packet))


_transport: Transport | None = None


def get_transport() -> Transport:
    global _transport
    if _transport is None:
        _transport = Transport()
    return _transport


def set_transport(transport: Transport | None) -> None:
    """Tests only."""
    global _transport
    _transport = transport


def deliver(
    packet: ClosurePacket,
    approval: ApprovalRequest | None,
    case: InstitutionCase,
    *,
    audit: AuditLog | None = None,
) -> str:
    """Send one packet. The gate.

    Raises `PolicyViolation` unless an APPROVED approval record exists for exactly this
    packet, decided by a named human, and the case is sitting in AWAITING_APPROVAL. The
    refusal is audited too - a blocked send is a fiduciary event worth recording.
    """
    audit = audit or get_audit_log()

    with span(
        "transport.deliver",
        institution=case.institution_name,
        packet_id=packet.id,
        approval_id=approval.id if approval else None,
    ):
        try:
            assert_state_allows_send(case)
            assert_sendable(packet, approval)
        except Exception as exc:
            audit.record(
                estate_id=case.estate_id,
                institution_id=case.institution_id,
                case_id=case.id,
                actor="transport",
                action="outbound.refused",
                reasoning=f"Send refused at the approval gate: {exc}",
                payload={"packet_id": packet.id, "error": type(exc).__name__},
            )
            log.error("outbound.refused", packet_id=packet.id, error=str(exc))
            raise

        destination = get_transport().transmit(packet)
        packet.sent_at = now()

        audit.record(
            estate_id=case.estate_id,
            institution_id=case.institution_id,
            case_id=case.id,
            actor="transport",
            action="outbound.sent",
            reasoning=(
                f"Delivered to {packet.recipient} under approval {approval.id} decided by "  # type: ignore[union-attr]
                f"{approval.decided_by}. Disclosed: "  # type: ignore[union-attr]
                f"{', '.join(packet.disclosed_fields) or 'nothing'}. Withheld: "
                f"{', '.join(packet.withheld_fields) or 'nothing'}."
            ),
            payload={
                "packet_id": packet.id,
                "approval_id": approval.id,  # type: ignore[union-attr]
                "recipient": packet.recipient,
                "channel": packet.channel.value,
                "destination": destination,
                "disclosed": packet.disclosed_fields,
                "withheld": packet.withheld_fields,
                "playbook": packet.playbook_ref,
            },
        )
        log.bind(estate_id=case.estate_id, institution_id=case.institution_id).info(
            "outbound.sent", packet_id=packet.id, recipient=packet.recipient
        )
        return destination
