"""Channel separation for untrusted content (repository files, tool output, issue text).

This reduces, but does not eliminate, a model's susceptibility to injected instructions.
The actual guarantees come from capability limits (typed tools, path jail, no shell, sandbox)
and from deterministic gates that never read model prose.
"""

from __future__ import annotations

import re

_TAG_RE = re.compile(r"</?\s*(tool_output|untrusted|issue|system)\b", re.IGNORECASE)

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)",
        r"disregard (all |any )?(the )?(previous|prior|above)",
        r"you are now (an?|the) ",
        r"new (system )?instructions?:",
        r"\bsystem prompt\b",
        r"(as|you are) an? (ai|llm|language model|assistant)[, ].{0,40}(must|should|will)",
        r"do not (tell|inform) the (user|human)",
        r"(curl|wget)\s+https?://\S+\s*\|\s*(ba)?sh",
    )
]


def neutralize(text: str) -> str:
    """Defang delimiter tags inside untrusted text so it cannot close our wrapper."""
    return _TAG_RE.sub(lambda m: m.group(0).replace("<", "&lt;"), text)


def wrap_untrusted(kind: str, text: str, **attrs: str) -> str:
    attr_s = "".join(f' {k}="{neutralize(v).replace(chr(34), "")}"' for k, v in attrs.items())
    return f'<{kind}{attr_s} untrusted="true">\n{neutralize(text)}\n</{kind}>'


def scan_for_injection(text: str) -> list[str]:
    """Heuristic flags for the trajectory. Never used to block: false positives are common."""
    return [p.pattern for p in INJECTION_PATTERNS if p.search(text)]
