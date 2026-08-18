#!/usr/bin/env python3
"""Count how many of a module's own methods are handed to other objects.

This measures **coupling**, not size: a class that passes 111 of its methods to
other objects is the wiring hub of the app no matter how many lines it has, and
moving code between files does not change the number. Extracting *state and
responsibility* does. That makes it the acceptance criterion for a split, where a
line count or a graph edge count is not:

* a line count drops as soon as code moves, even if nothing was decoupled;
* the code graph's edge count is partly model-inferred (31 of MainWindow's 236
  edges are ``INFERRED``), so it can drift with the graph backend rather than the
  code.

**The rule, stated so a later run means the same thing** — a hand-over is
``self.<name>`` appearing as an *argument* to a call, where ``<name>`` is a method
defined in the file. Counted in two flavours, reported separately because both
readings are defensible and mixing them is how two people get two numbers:

``direct``
    ``other.connect("x", self._on_y)`` — the receiver gets the bound method
    itself and can call it whenever it likes.
``wrapped``
    ``other.connect("x", lambda *_: self._on_y())`` — an adapter reaches back in.
    Weaker (the receiver does not know the method) but still a line into this
    object.

A hand-over counts wherever it sits in the argument, including inside a dict,
list, tuple or set literal: ``TabOrchestrator(callbacks={"push_history":
self._push_history, …})`` hands over seventeen of them in one argument, and a
counter that only looks at top-level arguments misses precisely the manager
wiring a split is about.

Hand-overs to ``self`` (``self.connect("closed", self._flush)``) are internal
wiring, not coupling to another module, and are reported apart from both.

Usage:
    scripts/count_callbacks.py src/markdown_vault/app/app_window.py
    make callbacks FILE=src/markdown_vault/app/app_window.py
"""
import ast
import sys
from collections import defaultdict
from pathlib import Path


def _receiver_is_self(call: ast.Call) -> bool:
    """True for ``self.foo(...)`` — the object wiring itself up."""
    func = call.func
    return (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id == "self")


def _self_attrs(node: ast.AST):
    """Every ``self.<name>`` under *node*."""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"):
            yield sub


def analyse(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    out = {k: defaultdict(list) for k in ("direct", "wrapped", "internal")}

    def record(node, kind_when_direct):
        """Collect hand-overs in *node*, descending into literal containers."""
        if isinstance(node, ast.Lambda):
            for attr in _self_attrs(node.body):
                if attr.attr in methods:
                    out["internal" if kind_when_direct == "internal"
                        else "wrapped"][attr.attr].append(attr.lineno)
            return
        if isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
            values = node.values if isinstance(node, ast.Dict) else node.elts
            for value in values:
                record(value, kind_when_direct)
            return
        if isinstance(node, ast.Attribute):
            for attr in _self_attrs(node):
                if attr.attr in methods:
                    out[kind_when_direct][attr.attr].append(attr.lineno)

    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        kind = "internal" if _receiver_is_self(call) else "direct"
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            record(arg, kind)
    return out


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-3].strip(), file=sys.stderr)
        return 2
    for name in argv[1:]:
        path = Path(name)
        if not path.exists():
            print(f"no such file: {path}", file=sys.stderr)
            return 1
        res = analyse(path)
        handed = set(res["direct"]) | set(res["wrapped"])
        sites = sum(len(v) for k in ("direct", "wrapped") for v in res[k].values())
        print(f"{path}")
        print(f"  methods handed to other objects : {len(handed)}")
        print(f"  hand-over sites                 : {sites}"
              f"  (direct {sum(len(v) for v in res['direct'].values())},"
              f" wrapped {sum(len(v) for v in res['wrapped'].values())})")
        print(f"  self-wiring (not counted above) : "
              f"{sum(len(v) for v in res['internal'].values())}")
        top = sorted(handed, key=lambda m: -(len(res['direct'][m]) + len(res['wrapped'][m])))
        print("  most handed around              : " + ", ".join(top[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
