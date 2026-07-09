"""Guardrails — PII detection, redaction, and blocking at the model-call seams.

A deployment-wide policy (seeded from ``config.yml``, admin-editable live via the
``guardrails`` :mod:`app.app_config` section) that inspects content flowing **into**
providers (user messages, history, tool results) and **out of** them (streamed
completions), so PII and organization-specific identifiers never leave the deployment
unredacted. Applied at the shared choke points — the gateway handlers and the agent
harness — so interactive chat, ``/v1/chat/completions``, and the future agentic gateway
endpoint all enforce the same policy.

Semantics (mirrors the phlox-gw design):
- The policy has an **input action** and an **output action**: ``off`` | ``redact`` |
  ``block``. A direction set to ``off`` is not filtered at all.
- **Built-in patterns** (email/phone/SSN/credit card/API key) follow the direction's
  global action and carry their own replacement token (``[EMAIL]``, ``[SSN]``, …).
- **Custom patterns** carry their own ``action`` (redact | block) and replacement, so a
  deployment can redact PII globally but hard-block, say, internal project codenames.
- ``block`` always redacts too — a blocked request/response is never echoed verbatim.

Content is inspected in memory only and never stored by this module.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import NamedTuple

logger = logging.getLogger(__name__)

# --- built-in detectors -------------------------------------------------------
# Order matters: more specific detectors run before broader ones (SSN before phone,
# so "123-45-6789" isn't half-eaten by the phone pattern first).
BUILTIN_PATTERNS: dict[str, dict[str, str]] = {
    "email": {
        "label": "Email address",
        "regex": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "replacement": "[EMAIL]",
    },
    "ssn": {
        "label": "Social Security number",
        "regex": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
        "replacement": "[SSN]",
    },
    "credit_card": {
        "label": "Credit card number",
        # Major-brand prefixes (Visa/MC/Amex/Discover) + 13-16 digits with optional
        # space/dash grouping; boundary guards stop partial matches inside longer runs.
        "regex": r"(?<!\d)(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))(?:[ -]?\d{4}){2}(?:[ -]?\d{1,4})(?!\d)",
        "replacement": "[CREDIT_CARD]",
    },
    "phone": {
        "label": "Phone number",
        "regex": r"(?<![\w.-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\d)",
        "replacement": "[PHONE]",
    },
    "api_key": {
        "label": "API key or token",
        # Common secret shapes: OpenAI/Phlox sk- keys, GitHub tokens, AWS access key ids,
        # Slack tokens, and JWT-shaped three-part base64url blobs.
        "regex": (
            r"\b(?:(?:phlox-)?sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}"
            r"|github_pat_[A-Za-z0-9_]{22,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}"
            r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})\b"
        ),
        "replacement": "[API_KEY]",
    },
}

ACTIONS = ("off", "redact", "block")
PATTERN_ACTIONS = ("redact", "block")

# How much streamed text is held back from emission until we're sure no pattern is
# mid-match across a chunk boundary (a partial "jane@exa" must not be emitted before
# the "mple.com" arrives). Long secrets (JWTs) can exceed this; 256 covers the
# realistic cases without visible streaming lag.
STREAM_HOLDBACK = 256
# Safety valve: a pathological pattern that keeps spanning the emit boundary (e.g. one
# that matches unboundedly) must not buffer the whole response in memory.
MAX_STREAM_BUFFER = 16_384


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    action: str          # "redact" | "block"
    replacement: str


class ScrubResult(NamedTuple):
    text: str
    matched: tuple[str, ...]  # rule names that matched (for reporting/logs)
    blocked: bool             # a block-action rule matched


_BUILTIN_COMPILED: dict[str, re.Pattern] = {
    pid: re.compile(spec["regex"]) for pid, spec in BUILTIN_PATTERNS.items()
}


def get_rules(direction: str, cfg: dict | None = None) -> list[Rule]:
    """Compile the active rule list for one direction ("input" | "output").

    Returns ``[]`` when the policy is disabled or the direction's action is ``off`` —
    callers use the empty list as "nothing to do". Invalid custom regexes are skipped
    with a warning (they are also rejected at save time; this guards hand-edited YAML).
    """
    if cfg is None:
        from app.config import get_guardrails_config

        cfg = get_guardrails_config()
    if not cfg.get("enabled"):
        return []
    action = cfg.get(f"{direction}_action", "off")
    if action not in PATTERN_ACTIONS:
        return []

    default_replacement = str(cfg.get("redaction_text") or "[REDACTED]")
    enabled_builtin = cfg.get("builtin") or {}
    rules: list[Rule] = []
    for pid, spec in BUILTIN_PATTERNS.items():
        if enabled_builtin.get(pid, True):
            rules.append(Rule(spec["label"], _BUILTIN_COMPILED[pid], action, spec["replacement"]))

    for p in cfg.get("custom_patterns") or []:
        if not p.get("enabled", True):
            continue
        raw = str(p.get("regex") or "")
        if not raw:
            continue
        try:
            pattern = re.compile(raw)
        except re.error as e:
            logger.warning("Guardrails: skipping invalid custom pattern %r: %s", p.get("name"), e)
            continue
        p_action = p.get("action") if p.get("action") in PATTERN_ACTIONS else "redact"
        rules.append(
            Rule(str(p.get("name") or "custom"), pattern, p_action,
                 str(p.get("replacement") or default_replacement))
        )
    return rules


def apply_rules(text: str, rules: list[Rule]) -> ScrubResult:
    """Run every rule over ``text``. Block-action matches also redact, so blocked
    content is never echoed back verbatim in an error or preview."""
    if not text or not rules:
        return ScrubResult(text, (), False)
    matched: list[str] = []
    blocked = False
    for r in rules:
        if r.pattern.search(text):
            matched.append(r.name)
            blocked = blocked or r.action == "block"
            text = r.pattern.sub(r.replacement, text)
    return ScrubResult(text, tuple(matched), blocked)


def scrub_messages(messages: list[dict], rules: list[Rule]) -> tuple[list[dict], set[str], bool]:
    """Return a scrubbed copy of a canonical message list (originals untouched).

    Only string ``content`` fields are inspected — tool-call structures, ids, and image
    payloads pass through unchanged. Returns (messages, matched-rule-names, blocked).
    """
    if not rules:
        return messages, set(), False
    out: list[dict] = []
    matched: set[str] = set()
    blocked = False
    for m in messages:
        content = m.get("content")
        if isinstance(content, str) and content:
            res = apply_rules(content, rules)
            if res.matched:
                m = {**m, "content": res.text}
                matched.update(res.matched)
                blocked = blocked or res.blocked
        out.append(m)
    return out, matched, blocked


@dataclass
class StreamRedactor:
    """Redact a streamed text sequence without splitting matches across chunks.

    ``feed()`` returns the text that is safe to emit now; a tail of ``holdback`` chars
    is retained until enough context arrives to know it isn't the start of a match
    (a match can only be extended by contiguous characters, so any match ending at
    least ``holdback`` chars before the buffer end is final). ``flush()`` drains the
    tail at end of stream. ``blocked``/``matched`` accumulate across the stream so the
    caller can abort as soon as a block-action rule matches.
    """

    rules: list[Rule]
    holdback: int = STREAM_HOLDBACK
    max_buffer: int = MAX_STREAM_BUFFER
    matched: set[str] = field(default_factory=set)
    blocked: bool = False
    _buf: str = ""

    def _scrub(self, text: str) -> str:
        res = apply_rules(text, self.rules)
        self.matched.update(res.matched)
        self.blocked = self.blocked or res.blocked
        return res.text

    def feed(self, text: str) -> str:
        if not self.rules:
            return text
        self._buf += text or ""
        # Detect block-action matches over the whole buffer immediately, even while the
        # matched text itself is still held back — the caller wants to abort early.
        for r in self.rules:
            if r.action == "block" and r.pattern.search(self._buf):
                self.matched.add(r.name)
                self.blocked = True
        if len(self._buf) <= self.holdback:
            return ""
        cut = len(self._buf) - self.holdback
        # Never emit a partial match: if any match spans the cut, move the cut to its
        # start (re-checking until stable, since a lower cut can expose new spans).
        changed = True
        while changed and cut > 0:
            changed = False
            for r in self.rules:
                for m in r.pattern.finditer(self._buf):
                    if m.start() >= cut:
                        break
                    if m.end() > cut:
                        cut = m.start()
                        changed = True
        if cut <= 0:
            if len(self._buf) > self.max_buffer:
                out = self._scrub(self._buf)
                self._buf = ""
                return out
            return ""
        out = self._scrub(self._buf[:cut])
        self._buf = self._buf[cut:]
        return out

    def flush(self) -> str:
        out = self._scrub(self._buf)
        self._buf = ""
        return out
