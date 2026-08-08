"""Knowledge-graph data layer (pure logic, no GTK).

Builds a node/edge graph of the vault's wikilinks for the sidebar / browse
graph.  Nodes are files, edges are resolved file→file wikilinks.

Kept independent of the ``BacklinkIndex`` / resolver internals: the caller
supplies the file→vault map and the already-resolved ``(source, target)`` edges,
so this module is trivially testable.  Directed edges are preserved (outgoing
vs. backlink); the local neighbourhood is computed undirected.
"""

import colorsys
import hashlib
from dataclasses import dataclass


@dataclass
class Node:
    id: str            # absolute file path — the stable identity
    label: str         # display name (file stem)
    vault: str         # owning vault path (colour grouping)
    degree: int = 0    # total links in + out (node size)


@dataclass
class Edge:
    source: str
    target: str


@dataclass
class Graph:
    nodes: list        # list[Node]
    edges: list        # list[Edge]

    def node_ids(self) -> set:
        return {n.id for n in self.nodes}


def _stem(path: str) -> str:
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return base[:-3] if base.endswith(".md") else base


def build_graph(file_vaults: dict, edges) -> Graph:
    """Build the full graph.

    *file_vaults*: ``{file_path: vault_path}`` for every node.
    *edges*: iterable of ``(source_path, target_path)``.  Edges touching an
    unknown file, self-loops and duplicates are dropped; degree counts both ends.
    """
    nodes = {p: Node(id=p, label=_stem(p), vault=v)
             for p, v in file_vaults.items()}
    seen = set()
    kept = []
    for src, dst in edges:
        if src == dst or src not in nodes or dst not in nodes or (src, dst) in seen:
            continue
        seen.add((src, dst))
        kept.append(Edge(src, dst))
        nodes[src].degree += 1
        nodes[dst].degree += 1
    return Graph(list(nodes.values()), kept)


def edges_from_backlinks(source_to_targets: dict, key_to_file: dict):
    """Resolve the backlink index's canonical target keys to file→file edges.

    *source_to_targets*: ``{source_path: set(canonical_target_key)}`` — exactly
    ``BacklinkIndex.outgoing_targets()``.
    *key_to_file*: ``{canonical_target_key: file_path}`` — built from
    ``BacklinkIndex.canonical_key(f)`` over the known files.

    Yields ``(source_path, target_path)`` for every target that resolves to a
    known file; broken links (a key with no file) are skipped.
    """
    for source, keys in source_to_targets.items():
        for key in keys:
            target = key_to_file.get(key)
            if target is not None:
                yield (source, target)


def local_graph(graph: Graph, center: str, depth: int = 1) -> Graph:
    """Subgraph of *center* plus everything within *depth* hops (undirected).

    A ``center`` with no links yields just the lone centre node; a ``center`` not
    in the graph yields an empty graph.
    """
    if center not in graph.node_ids():
        return Graph([], [])
    adj: dict = {}
    for e in graph.edges:
        adj.setdefault(e.source, set()).add(e.target)
        adj.setdefault(e.target, set()).add(e.source)
    keep = {center}
    frontier = {center}
    for _ in range(max(0, depth)):
        nxt = set()
        for n in frontier:
            nxt |= adj.get(n, set())
        nxt -= keep
        if not nxt:
            break
        keep |= nxt
        frontier = nxt
    by_id = {n.id: n for n in graph.nodes}
    sub_nodes = [by_id[i] for i in keep if i in by_id]
    sub_edges = [e for e in graph.edges if e.source in keep and e.target in keep]
    return Graph(sub_nodes, sub_edges)


def color_for_vault(vault: str) -> str:
    """Deterministic colour for a vault, derived from its path so the *same*
    vault always gets the *same* colour — independent of order or of which other
    vaults exist.  The hue spreads over the wheel (distinct vaults → distinct
    colours); saturation/lightness are fixed for a consistent, vivid look.
    """
    digest = int(hashlib.md5(vault.encode("utf-8")).hexdigest(), 16)
    hue = (digest % 360) / 360.0
    # Vary lightness/saturation from other hash bits too, so two vaults that
    # happen to land on nearby hues still separate by brightness/vividness.
    light = 0.46 + ((digest >> 12) % 3) * 0.09        # 0.46 / 0.55 / 0.64
    sat = 0.55 + ((digest >> 20) % 2) * 0.20          # 0.55 / 0.75
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def vault_palette(vaults) -> dict:
    """Map each vault path to its stable per-vault colour (see color_for_vault)."""
    return {v: color_for_vault(v) for v in vaults}


def to_payload(graph: Graph, colors: dict, center: str = None) -> dict:
    """Serialise a graph for the renderer (JSON-friendly)."""
    return {
        "nodes": [
            {"id": n.id, "label": n.label, "vault": n.vault,
             "color": colors.get(n.vault, "#888888"),
             "degree": n.degree, "center": n.id == center}
            for n in graph.nodes
        ],
        "edges": [{"source": e.source, "target": e.target} for e in graph.edges],
    }
