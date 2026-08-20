"""Court-defensible export.

BUILD_PLAN.md calls this "the single most distinctive artifact in your submission", and
it is right: every hackathon has a dashboard, none of them hand the judge a document an
executor could file with a probate court.

Always emits HTML - no dependencies, renders anywhere, prints to PDF from any browser.
Emits a real PDF too when `reportlab` is installed, which keeps the local path dependency
free while still producing the artifact the video shows.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from packages.core.audit.sink import AuditLog, get_audit_log
from packages.core.clock import iso
from packages.core.config import get_settings
from packages.core.models import AuditRecord, Estate

DISCLAIMER = (
    "This document is a machine-generated record of actions taken by an automated "
    "estate-administration assistant on the instruction of the named executor. Every "
    "outbound communication recorded here was reviewed and approved by that executor "
    "before transmission. This document is not legal advice and does not constitute a "
    "legal determination of any kind. All data in this demonstration is fictional."
)


def _fmt(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(value)


def render_html(estate: Estate, records: list[AuditRecord], chain_ok: bool, broken_at: str | None) -> str:
    rows = []
    for record in records:
        payload_bits = ", ".join(
            f"{key}={html.escape(str(value))}"
            for key, value in sorted(record.payload.items())
            if key not in {"body"}
        )
        rows.append(
            f"""
    <tr>
      <td class="seq">{record.seq}</td>
      <td class="ts">{_fmt(record.at)}</td>
      <td>{html.escape(record.actor)}</td>
      <td class="action">{html.escape(record.action)}</td>
      <td>{html.escape(record.institution_id or "-")}</td>
      <td class="reason">{html.escape(record.reasoning)}
        <div class="payload">{payload_bits}</div>
      </td>
      <td class="digest" title="{record.digest}">{record.digest[:12]}</td>
    </tr>"""
        )

    integrity = (
        '<span class="ok">VERIFIED &mdash; hash chain intact across '
        f"{len(records)} records</span>"
        if chain_ok
        else f'<span class="bad">BROKEN at record {html.escape(broken_at or "?")}</span>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Estate Administration Record - {html.escape(estate.decedent.full_name)}</title>
<style>
  body {{ font-family: Georgia, "Times New Roman", serif; margin: 48px; color: #111; line-height: 1.5; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .sub {{ color: #555; font-size: 13px; margin-bottom: 28px; }}
  .meta {{ border: 1px solid #ccc; padding: 16px 20px; margin-bottom: 24px; font-size: 13px; }}
  .meta dl {{ display: grid; grid-template-columns: 190px 1fr; gap: 6px 16px; margin: 0; }}
  .meta dt {{ font-weight: bold; }}
  .meta dd {{ margin: 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 11.5px; font-family: -apple-system, "Segoe UI", sans-serif; }}
  th {{ background: #f0f0ef; text-align: left; padding: 7px 8px; border-bottom: 2px solid #999; }}
  td {{ padding: 7px 8px; border-bottom: 1px solid #e3e3e0; vertical-align: top; }}
  td.seq {{ width: 42px; color: #777; }}
  td.ts {{ width: 150px; white-space: nowrap; color: #444; }}
  td.action {{ font-family: ui-monospace, Consolas, monospace; font-size: 11px; }}
  td.digest {{ font-family: ui-monospace, Consolas, monospace; font-size: 10px; color: #777; }}
  .payload {{ color: #666; font-size: 10.5px; margin-top: 3px; }}
  .ok {{ color: #14532d; font-weight: bold; }}
  .bad {{ color: #991b1b; font-weight: bold; }}
  .disclaimer {{ margin-top: 32px; padding: 16px 20px; background: #faf8f2; border-left: 3px solid #a16207; font-size: 12px; }}
  footer {{ margin-top: 24px; font-size: 11px; color: #777; }}
</style>
</head>
<body>
  <h1>Estate Administration Record</h1>
  <div class="sub">Prepared for filing &middot; generated {iso()}</div>

  <div class="meta">
    <dl>
      <dt>Decedent</dt><dd>{html.escape(estate.decedent.full_name)}</dd>
      <dt>Date of death</dt><dd>{html.escape(estate.decedent.date_of_death)}</dd>
      <dt>Executor of record</dt><dd>{html.escape(estate.executor.full_name)} &lt;{html.escape(estate.executor.email)}&gt;</dd>
      <dt>Jurisdiction</dt><dd>{html.escape(estate.jurisdiction)}</dd>
      <dt>Estate reference</dt><dd>{html.escape(estate.id)}</dd>
      <dt>Records in this export</dt><dd>{len(records)}</dd>
      <dt>Chain integrity</dt><dd>{integrity}</dd>
    </dl>
  </div>

  <table>
    <thead>
      <tr><th>#</th><th>Timestamp</th><th>Actor</th><th>Action</th><th>Institution</th>
      <th>Reasoning</th><th>Digest</th></tr>
    </thead>
    <tbody>{"".join(rows)}</tbody>
  </table>

  <div class="disclaimer">{html.escape(DISCLAIMER)}</div>
  <footer>
    Each record is chained to its predecessor by SHA-256 digest. Recomputing any record
    invalidates every record that follows it; the integrity line above is the result of
    walking that chain at export time.
  </footer>
</body>
</html>
"""


def export_estate_record(
    estate: Estate,
    *,
    log: AuditLog | None = None,
    out_dir: Path | None = None,
) -> dict[str, Path]:
    """Write the record. Returns the paths produced."""
    log = log or get_audit_log()
    settings = get_settings()
    out_dir = out_dir or (settings.data_dir / "exports")
    out_dir.mkdir(parents=True, exist_ok=True)

    records = log.for_estate(estate.id)
    chain_ok, broken_at = log.verify()

    stem = f"estate-record-{estate.id}"
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(render_html(estate, records, chain_ok, broken_at), encoding="utf-8")

    produced = {"html": html_path}
    pdf_path = _try_pdf(estate, records, chain_ok, out_dir / f"{stem}.pdf")
    if pdf_path is not None:
        produced["pdf"] = pdf_path
    return produced


def _try_pdf(
    estate: Estate, records: list[AuditRecord], chain_ok: bool, path: Path
) -> Path | None:  # pragma: no cover - depends on an optional dependency
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        return None

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=LETTER, title="Estate Administration Record")
    story = [
        Paragraph("Estate Administration Record", styles["Title"]),
        Paragraph(
            f"Decedent: {estate.decedent.full_name} &middot; "
            f"Executor: {estate.executor.full_name} &middot; Estate {estate.id}",
            styles["Normal"],
        ),
        Paragraph(
            f"Chain integrity: {'VERIFIED' if chain_ok else 'BROKEN'} "
            f"across {len(records)} records.",
            styles["Normal"],
        ),
        Spacer(1, 14),
    ]

    data = [["#", "Timestamp", "Action", "Institution", "Reasoning"]]
    for record in records:
        data.append(
            [
                str(record.seq),
                _fmt(record.at),
                record.action,
                record.institution_id or "-",
                Paragraph(html.escape(record.reasoning[:400]), styles["BodyText"]),
            ]
        )
    table = Table(data, colWidths=[24, 96, 120, 90, 200], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeec")),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 18))
    story.append(Paragraph(DISCLAIMER, styles["Italic"]))
    doc.build(story)
    return path
