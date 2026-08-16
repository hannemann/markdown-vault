#!/usr/bin/env python3
"""Unwrap a `gdbus call` reply on stdin to its raw payload.

`gdbus call` prints the D-Bus reply as a GVariant text tuple: a string comes back
single-quoted with ``\\n`` escapes, an array as ``[...]``, and — because it calls
``g_variant_print(reply, TRUE)`` — a value whose type can't be inferred (an empty
array, a boolean) is prefixed with a ``@<signature>`` annotation. That is
unreadable for a human or an agent expecting the Markdown / JSON / paths the
method returns. Read that tuple and print the payload instead:

- a string  -> printed with real newlines (raw Markdown; a JSON string is now
  valid JSON, pipeable to `jq`)
- a list    -> one item per line (an empty list prints nothing)
- a boolean -> ``True`` / ``False``
- anything still unparsable -> printed unchanged

Empty stdin (e.g. `gdbus` failed and wrote only to stderr) is an error, not an
empty answer — exit non-zero so a caller can tell "no result" from "call failed".
"""

import ast
import re
import sys

# A quoted GVariant string, honouring backslash escapes, matched so its contents
# are left untouched by the normalisation below. g_variant_print picks the quote
# character from the payload — a string containing an apostrophe is printed
# double-quoted — so BOTH forms must be recognised (R121.1).
_QUOTED = re.compile(
    r"'(?:[^'\\]|\\.)*'"        # single-quoted
    r'|"(?:[^"\\]|\\.)*"',      # double-quoted (used when the payload has a ')
    re.DOTALL,
)
# A GVariant type annotation: `@` + a run of signature characters (basic types
# plus the `a m {} ()` containers), e.g. `@as`, `@a{sv}`, `@ay`.
_TYPE_ANNOTATION = re.compile(r"@[a-z{}()]+\s*")


def _normalize_outside(segment: str) -> str:
    """Turn GVariant text that is OUTSIDE any quoted string into Python-literal
    text: drop `@<sig>` annotations and map the GVariant keywords."""
    segment = _TYPE_ANNOTATION.sub("", segment)
    segment = re.sub(r"\btrue\b", "True", segment)
    segment = re.sub(r"\bfalse\b", "False", segment)
    segment = re.sub(r"\bnothing\b", "None", segment)
    return segment


def _normalize(text: str) -> str:
    """Normalize *text* to a Python literal, leaving quoted strings verbatim (so
    an `@` or `false` inside a string is never rewritten)."""
    out, last = [], 0
    for m in _QUOTED.finditer(text):
        out.append(_normalize_outside(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(_normalize_outside(text[last:]))
    return "".join(out)


def unwrap(text: str) -> str:
    text = text.strip()
    try:
        value = ast.literal_eval(_normalize(text))
    except (ValueError, SyntaxError):
        return text                      # unknown shape: show it verbatim
    if isinstance(value, tuple) and len(value) == 1:
        value = value[0]
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return str(value)


if __name__ == "__main__":
    data = sys.stdin.read()
    if not data.strip():
        sys.stderr.write("dbus-unwrap: empty reply (gdbus produced no output)\n")
        sys.exit(1)
    print(unwrap(data))
