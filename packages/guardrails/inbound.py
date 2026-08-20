"""Inbound screening.

Every inbound message is untrusted third-party content. A scanned letter is an injection
vector: nothing stops an institution's mailroom - or someone impersonating one - from
printing "ignore your previous instructions" in 6pt grey at the bottom of page two, and
nothing stops OCR from faithfully lifting it into your pipeline.

So: **screen before the model, not with the model.** This module is deterministic. It
runs first, it is authoritative for BLOCK, and a blocked message never produces a
sanitized projection at all - there is nothing for a downstream prompt to interpolate.

Three layers get screened independently, because attacks hide in the ones people forget:

* `text`       - the message body as delivered
* `ocr`        - the text layer lifted out of a scanned page image
* `attachment` - text extracted from an attached document

`packages/guardrails/payloads.py` holds the adversarial corpus; `tests/test_adversarial.py`
asserts none of it reaches model context unscreened.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from packages.core.models import ScreenFinding, ScreenResult, Verdict

# --- rules ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str  # low | medium | high | critical
    pattern: re.Pattern[str]
    note: str


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


RULES: tuple[Rule, ...] = (
    Rule(
        "instruction.override",
        "critical",
        _rx(r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|earlier|above|all)\b[^.\n]{0,30}\b(instruction|prompt|rule|direction|message)"),
        "Attempts to replace the agent's operating instructions.",
    ),
    Rule(
        "instruction.new_directive",
        "critical",
        _rx(r"^\s*(new instructions?|updated instructions?|system\s*(prompt|message)|###\s*instruction|\[system\])\s*[:\-]"),
        "Presents itself as a new system-level directive.",
    ),
    Rule(
        "instruction.role_redefinition",
        "high",
        _rx(r"\byou are (now|no longer)\b|\b(act|behave) as (if|an?|the)\b|\bpretend (you|to be)\b|\bfrom now on,? you\b"),
        "Attempts to redefine the agent's role.",
    ),
    Rule(
        "instruction.addresses_machine",
        "high",
        _rx(r"\b(if you (are|'re) (an? )?(ai|bot|language model|automated)|as an? (ai|language model)|dear (ai|assistant|automated system)|attention:? (ai|agent|bot))\b"),
        "Addresses an automated reader rather than a person - a letter to a human has no reason to.",
    ),
    Rule(
        "tool.poisoning",
        "critical",
        _rx(r"\b(call|invoke|execute|run|use)\s+(the\s+)?(tool|function|command|api|endpoint|shell)\b|<\s*(tool_call|function_call|invoke)\b|\{\s*\"(tool|function)_?(name|call)\"\s*:|\b(execute|invoke|call|run)_function\s*\("),
        "Attempts to trigger a tool call from inside message content.",
    ),
    Rule(
        "tool.active_uri",
        "critical",
        _rx(r"\b(javascript|vbscript|data):(?:void|text/html|[^\s)\"']{0,20}\()|\bfile://"),
        "Contains an executable URI scheme - correspondence has no reason to carry one.",
    ),
    Rule(
        "tool.shell",
        "critical",
        _rx(r"\b(curl|wget|rm\s+-rf|bash\s+-c|powershell|subprocess|os\.system|eval\()\b"),
        "Contains shell or code-execution payload.",
    ),
    Rule(
        "exfiltration.bulk_documents",
        "critical",
        _rx(r"\b(forward|send|email|transmit|upload|share|release)\b[^.\n]{0,60}\b(entire (estate )?file|all documents|all estate documents|everything you hold|complete estate file|full estate file|whole file)\b"),
        "Requests bulk disclosure of the estate file. A real process asks for named documents.",
    ),
    Rule(
        # No bereavement services department has ever needed an API key. Mentioning one at
        # all is the signal - no proximity to a request verb required.
        "exfiltration.machine_secrets",
        "critical",
        _rx(r"\b(api[- ]?key|service account|access token|bearer token|private key|oauth token|client secret|credentials for|your credentials)\b"),
        "References machine credentials - never part of estate correspondence.",
    ),
    Rule(
        "exfiltration.human_secrets",
        "critical",
        _rx(r"\b(reply|respond|send|forward|email|provide|include|disclose|transmit|share|supply|furnish)\b[^.\n]{0,90}\b(social security number|\bssn\b|password|\bpin\b|security question|one-?time (code|passcode)|full account number)\b"),
        "Requests a secret that no closure process legitimately needs in writing.",
    ),
    Rule(
        "exfiltration.prompt",
        "high",
        _rx(r"\b(reveal|print|repeat|show|output|disclose)\b[^.\n]{0,30}\b(system prompt|your instructions|your rules|configuration|initial prompt)\b"),
        "Attempts to extract the agent's own instructions.",
    ),
    Rule(
        # Scoped to a *change* of destination, not to any address at all - institutions
        # legitimately print a contact address in every letter they send.
        "exfiltration.redirect_address",
        "high",
        _rx(r"\b((new|updated|changed|alternative|different)\s+(e-?mail|address|contact|destination)|address has changed|all future correspondence|rather than the address|instead of the address)\b[^.\n]{0,90}[\w.+-]+@[\w-]+\.[a-z]{2,}"),
        "Redirects correspondence to an address the estate did not nominate.",
    ),
    Rule(
        "approval.bypass",
        "critical",
        _rx(r"\b(no|without)\s+(executor\s+)?(approval|authorisation|authorization|review|sign-?off)\b|\b(skip|bypass|waive)\b[^.\n]{0,30}\b(approval|review|verification|confirmation)\b|\b(pre-?approved|auto-?approve)\b"),
        "Asserts that the human approval step can be skipped. It cannot.",
    ),
    Rule(
        "secrecy.conceal_from_human",
        "critical",
        _rx(r"\b(do not|don'?t|never)\b[^.\n]{0,30}\b(tell|inform|notify|mention|copy|show)\b[^.\n]{0,30}\b(executor|family|human|client|customer|next of kin)\b|\bwithout (informing|notifying|telling)\b"),
        "Asks the agent to conceal an action from the executor.",
    ),
    Rule(
        "funds.redirection",
        "critical",
        _rx(r"\b(wire|transfer|remit|deposit|release)\b[^.\n]{0,40}\b(balance|funds|proceeds|payment|benefit)\b[^.\n]{0,40}\b(to|into)\b|\b(updated|new|revised|amended)\b[^.\n]{0,20}\b(bank|banking|account|routing|iban|sort code)\b[^.\n]{0,20}\bdetails\b"),
        "Attempts to redirect estate funds. The classic bereavement fraud.",
    ),
    Rule(
        "urgency.pressure",
        "medium",
        _rx(r"\b(immediately|urgent(ly)?|within 24 hours|before end of day|failure to comply)\b[^.\n]{0,60}\b(or|otherwise|else)\b[^.\n]{0,40}\b(penalty|forfeit|closed|lost|escheat|legal action)\b"),
        "Manufactured urgency paired with a threatened consequence.",
    ),
    Rule(
        "delimiter.escape",
        "high",
        _rx(r">>>\s*END|<<<\s*(UNTRUSTED|BEGIN)|\[/?INST\]|<\|(im_(start|end)|endoftext|system)\|>|\{\{\s*system\s*\}\}|```\s*system"),
        "Contains delimiters used to break out of a fenced prompt block.",
    ),
    Rule(
        "html.hidden_text",
        "high",
        _rx(r"<!--[\s\S]{0,400}?-->|style\s*=\s*[\"'][^\"']*(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|color\s*:\s*#f{3,6}\s*;?\s*background)[^\"']*[\"']"),
        "Text hidden from a human reader but visible to a parser.",
    ),
    Rule(
        "scan.machine_directive",
        "high",
        _rx(r"\b(machine[- ]readable (instruction|directive)|for automated processing only|agent[- ]only (note|instruction)|processing note for (the )?(system|agent|ai))\b"),
        "A scanned letter carrying directives aimed at automated processing.",
    ),
    Rule(
        "identity.impersonation",
        "high",
        _rx(r"\b(this message is from|on behalf of) (the )?(executor|estate attorney|probate court|aftercare|system administrator)\b[^.\n]{0,40}\b(authoris|authoriz|instruct|approve)"),
        "Claims to speak with the executor's or the court's authority.",
    ),
    Rule(
        "legal.determination_request",
        "medium",
        _rx(r"\b(confirm|state|certify|declare)\b[^.\n]{0,40}\b(who inherits|the beneficiar(y|ies) (is|are)|entitle(d|ment)|legal(ly)? (heir|owner)|valid(ity)? of the claim)\b"),
        "Invites the agent to make a legal determination. Boundary 2 says it escalates.",
    ),
)

ZERO_WIDTH = "​‌‍‎‏⁠﻿"
BIDI_OVERRIDE = "‪‫‬‭‮⁦⁧⁨⁩"

# Cyrillic and Greek characters that render as Latin letters. Used to smuggle a keyword
# past a naive matcher - "іgnore" with a Ukrainian i looks identical and matches nothing.
HOMOGLYPHS = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "у": "y", "х": "x", "і": "i", "ѕ": "s", "һ": "h",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C",
    "Х": "X", "І": "I", "Ο": "O", "Α": "A", "Β": "B",
    "Ε": "E", "Η": "H", "Μ": "M", "Ν": "N", "Τ": "T",
}

BASE64_RE = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")
HEX_BLOB_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}[\s:]?){24,}\b")

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Anything at or above this severity is blocked outright and never projected to a model.
BLOCK_AT = "high"


# --- normalization -------------------------------------------------------------------


def strip_invisibles(text: str) -> tuple[str, int]:
    removed = sum(text.count(ch) for ch in ZERO_WIDTH + BIDI_OVERRIDE)
    table = str.maketrans("", "", ZERO_WIDTH + BIDI_OVERRIDE)
    return text.translate(table), removed


def fold_homoglyphs(text: str) -> tuple[str, int]:
    swapped = 0
    out = []
    for ch in text:
        replacement = HOMOGLYPHS.get(ch)
        if replacement:
            swapped += 1
            out.append(replacement)
        else:
            out.append(ch)
    return "".join(out), swapped


def canonicalize(text: str) -> str:
    """What the rules actually run against.

    Unicode-normalized, invisibles stripped, homoglyphs folded, whitespace collapsed. An
    attacker who splits `i g n o r e` across spaces still has to survive this.
    """
    text = unicodedata.normalize("NFKC", text)
    text, _ = strip_invisibles(text)
    text, _ = fold_homoglyphs(text)
    # Collapse runs of separators used to break up keywords, but keep line structure.
    text = re.sub(r"[ \t ]{2,}", " ", text)
    text = re.sub(r"(?<=\b\w)[\s.\-_*]{1,2}(?=\w\b)", "", text)
    return text


def decode_embedded_blobs(text: str) -> list[tuple[str, str]]:
    """Return (encoding, decoded_text) for base64/hex blobs that decode to prose.

    An injection encoded as base64 is still an injection; a pattern matcher that only
    reads the surface misses it entirely.
    """
    found: list[tuple[str, str]] = []
    for match in BASE64_RE.finditer(text):
        blob = match.group(0)
        try:
            decoded = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False)
        except (binascii.Error, ValueError):
            continue
        try:
            as_text = decoded.decode("utf-8")
        except UnicodeDecodeError:
            continue
        printable = sum(1 for ch in as_text if ch.isprintable() or ch in "\n\r\t")
        if len(as_text) >= 12 and printable / max(1, len(as_text)) > 0.9:
            found.append(("base64", as_text))
    for match in HEX_BLOB_RE.finditer(text):
        raw = re.sub(r"[\s:]", "", match.group(0))
        if len(raw) % 2:
            raw = raw[:-1]
        try:
            as_text = bytes.fromhex(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if len(as_text) >= 12 and all(ch.isprintable() or ch in "\n\r\t" for ch in as_text):
            found.append(("hex", as_text))
    return found


# --- the screen ----------------------------------------------------------------------


def _apply_rules(haystack: str, layer: str, *, via: str = "") -> list[ScreenFinding]:
    findings: list[ScreenFinding] = []
    for rule in RULES:
        match = rule.pattern.search(haystack)
        if match:
            excerpt = " ".join(match.group(0).split())[:160]
            findings.append(
                ScreenFinding(
                    rule=rule.name if not via else f"{rule.name}@{via}",
                    severity=rule.severity,  # type: ignore[arg-type]
                    layer=layer,  # type: ignore[arg-type]
                    excerpt=excerpt,
                    note=rule.note + (f" (found inside {via}-encoded content)" if via else ""),
                )
            )
    return findings


def screen(
    text: str,
    *,
    layer: str = "text",
    context: dict[str, Any] | None = None,
) -> ScreenResult:
    """Screen one layer of an inbound message.

    Returns a `ScreenResult`. On BLOCK, `sanitized_text` is empty: there is deliberately
    nothing for a caller to pass onward, so a careless downstream f-string produces an
    empty fence rather than a live payload.
    """
    context = context or {}
    raw = text or ""
    canonical = canonicalize(raw)

    findings = _apply_rules(canonical, layer)

    # Encoding-evasion signals are findings in their own right, not just preprocessing.
    _, invisibles = strip_invisibles(unicodedata.normalize("NFKC", raw))
    if invisibles >= 4:
        findings.append(
            ScreenFinding(
                rule="encoding.invisible_characters",
                severity="medium",
                layer="encoding",
                excerpt=f"{invisibles} zero-width or bidi-control characters",
                note="Invisible characters used to break up keywords or reverse displayed text.",
            )
        )
    _, homoglyphs = fold_homoglyphs(raw)
    if homoglyphs >= 3:
        findings.append(
            ScreenFinding(
                rule="encoding.homoglyphs",
                severity="high",
                layer="encoding",
                excerpt=f"{homoglyphs} Latin-lookalike characters from other scripts",
                note="Homoglyph substitution - a keyword that renders normally but matches nothing.",
            )
        )

    for encoding, decoded in decode_embedded_blobs(raw):
        nested = _apply_rules(canonicalize(decoded), layer, via=encoding)
        if nested:
            findings.extend(nested)
        elif len(decoded) > 200:
            findings.append(
                ScreenFinding(
                    rule=f"encoding.opaque_{encoding}_blob",
                    severity="low",
                    layer="encoding",
                    excerpt=decoded[:80],
                    note="Large encoded blob in ordinary correspondence.",
                )
            )

    # A scanned page whose OCR layer carries instruction-shaped content is the highest
    # signal in the whole system: paper does not talk to software.
    if layer == "ocr" and any(f.severity in {"high", "critical"} for f in findings):
        findings.append(
            ScreenFinding(
                rule="scan.injection_in_ocr_layer",
                severity="critical",
                layer="ocr",
                excerpt="",
                note="Instruction-shaped content recovered from the OCR text layer of a scanned page.",
            )
        )

    verdict = _verdict_for(findings)
    result = ScreenResult(
        verdict=verdict,
        findings=findings,
        sanitized_text="" if verdict is Verdict.BLOCK else sanitize(canonical),
        screened_by="pattern",
    )
    return result


def _verdict_for(findings: list[ScreenFinding]) -> Verdict:
    if not findings:
        return Verdict.ALLOW
    worst = max(SEVERITY_RANK[f.severity] for f in findings)
    if worst >= SEVERITY_RANK[BLOCK_AT]:
        return Verdict.BLOCK
    if worst >= SEVERITY_RANK["medium"]:
        return Verdict.SANITIZE
    return Verdict.ALLOW


def sanitize(text: str, *, max_chars: int = 6000) -> str:
    """Produce the only projection a model is ever allowed to see.

    Strips markup and hidden content, neutralizes fence-breaking delimiters, and caps
    length. This is defence in depth - a message that needed heavy sanitization was
    already blocked.
    """
    text = re.sub(r"<!--[\s\S]*?-->", " ", text)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]{1,200}>", " ", text)
    text = (
        text.replace(">>>", "> > >")
        .replace("<<<", "< < <")
        .replace("```", "'''")
        .replace("<|", "< |")
        .replace("|>", "| >")
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[truncated by inbound screen]"
    return text


FENCE_OPEN = "<<<UNTRUSTED_CORRESPONDENCE"
FENCE_CLOSE = ">>>END_UNTRUSTED_CORRESPONDENCE"


def fence(sanitized: str) -> str:
    """Wrap a sanitized projection for inclusion in a prompt.

    Callers must use this rather than interpolating text directly; `tests/test_policy.py`
    checks that no inbound field reaches an f-string in a prompt without it.
    """
    return f"{FENCE_OPEN}\n{sanitized}\n{FENCE_CLOSE}"


# --- layer extraction ----------------------------------------------------------------

LAYER_MARKERS = {
    "ocr": re.compile(r"\[OCR-TEXT-LAYER(?:[^\]]*)\]\s*(.*?)(?=\[/OCR-TEXT-LAYER\]|\Z)", re.DOTALL | re.IGNORECASE),
    "attachment": re.compile(r"\[ATTACHMENT(?:[^\]]*)\]\s*(.*?)(?=\[/ATTACHMENT\]|\Z)", re.DOTALL | re.IGNORECASE),
}


def split_layers(raw: str) -> dict[str, str]:
    """Separate a delivered message into the layers that get screened independently.

    The corpus marks a scanned page's OCR text with `[OCR-TEXT-LAYER]...[/OCR-TEXT-LAYER]`
    and extracted attachment text with `[ATTACHMENT]...[/ATTACHMENT]`, which is what a
    real pipeline would produce from a Vision OCR response and a document parser.
    """
    layers: dict[str, str] = {}
    body = raw
    for name, pattern in LAYER_MARKERS.items():
        chunks = [m.group(1).strip() for m in pattern.finditer(raw)]
        if chunks:
            layers[name] = "\n\n".join(chunks)
            body = pattern.sub(" ", body)
    body = re.sub(r"\[/?(?:OCR-TEXT-LAYER|ATTACHMENT)[^\]]*\]", " ", body)
    layers["text"] = body.strip()
    return layers


def screen_message(raw: str, *, context: dict[str, Any] | None = None) -> ScreenResult:
    """Screen every layer of a delivered message and merge the results.

    The merged verdict is the worst of the layers, which is the only safe merge: a clean
    body does not launder a poisoned scan.
    """
    layers = split_layers(raw)
    findings: list[ScreenFinding] = []
    projections: list[str] = []

    for name in ("text", "ocr", "attachment"):
        chunk = layers.get(name)
        if not chunk:
            continue
        result = screen(chunk, layer=name, context=context)
        findings.extend(result.findings)
        if result.verdict is not Verdict.BLOCK and result.sanitized_text:
            label = {"text": "", "ocr": "[scanned page, OCR text]\n", "attachment": "[attachment text]\n"}[name]
            projections.append(label + result.sanitized_text)

    verdict = _verdict_for(findings)
    return ScreenResult(
        verdict=verdict,
        findings=findings,
        sanitized_text="" if verdict is Verdict.BLOCK else "\n\n".join(projections),
        screened_by="pattern",
    )
