#!/usr/bin/env python3
"""Unwrap a `gdbus call` reply on stdin to its raw payload.

`gdbus call` prints the D-Bus reply as a GVariant text tuple: a string comes back
single-quoted with ``\\n`` escapes, an array as ``[...]``. That is unreadable for
a human or an agent expecting the Markdown / JSON / paths the method actually
returns (the whole answer collapses onto one line; a JSON string is not valid
JSON until unquoted). Read that tuple and print the payload instead:

- a string  -> printed with real newlines (raw Markdown; a JSON string is now
  valid JSON, pipeable to `jq`)
- a list    -> one item per line
- anything not Python-parsable (e.g. a boolean ``(true,)``) -> printed unchanged
"""

import ast
import sys


def unwrap(text: str) -> str:
    text = text.strip()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text                      # e.g. "(true,)" — GVariant bool literal
    if isinstance(value, tuple) and len(value) == 1:
        value = value[0]
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return str(value)


if __name__ == "__main__":
    print(unwrap(sys.stdin.read()))
