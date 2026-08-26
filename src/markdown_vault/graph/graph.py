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
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Node:
    id: str            # absolute file path — the stable identity
    label: str         # display name (file stem)
    vault: str         # owning vault path; the fallback colour source (see color_and_legend)
    degree: int = 0    # total links in + out (node size)
    tags: list = field(default_factory=list)  # frontmatter tags (for filtering)


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


def build_graph(file_vaults: dict, edges, file_tags: dict = None) -> Graph:
    """Build the full graph.

    *file_vaults*: ``{file_path: vault_path}`` for every node.
    *edges*: iterable of ``(source_path, target_path)``.  Edges touching an
    unknown file, self-loops and duplicates are dropped; degree counts both ends.
    *file_tags*: optional ``{file_path: [tag, …]}`` (frontmatter tags), attached
    to each node for tag filtering in the graph explorer.
    """
    file_tags = file_tags or {}
    nodes = {p: Node(id=p, label=_stem(p), vault=v, tags=list(file_tags.get(p, [])))
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
    # Not security-sensitive — just a stable hue seed; usedforsecurity=False keeps
    # it working on FIPS-enabled systems where plain md5() raises.
    digest = int(hashlib.md5(vault.encode("utf-8"), usedforsecurity=False).hexdigest(), 16)
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


def to_payload(graph: Graph, node_colors: dict, center: str = None, legend: list = None) -> dict:
    """Serialise a graph for the renderer (JSON-friendly).

    *node_colors* is a ``{node_id: hex}`` map — the colour policy (community vs. vault
    fallback) lives in :func:`color_and_legend`, not here.  The name says the key: a
    vault-keyed map (the old contract) would silently colour every node the fallback grey.
    *legend* is an optional ``[{"label", "color", "id"}]`` list the renderer displays as-is.
    """
    return {
        "nodes": [
            {"id": n.id, "label": n.label, "vault": n.vault,
             "color": node_colors.get(n.id, "#888888"),
             "degree": n.degree, "center": n.id == center, "tags": n.tags}
            for n in graph.nodes
        ],
        "edges": [{"source": e.source, "target": e.target} for e in graph.edges],
        "legend": legend or [],
    }


_LPA_MAX_ITERS = 20


def community_labels(graph: Graph) -> dict:
    """Deterministic label propagation over the graph's (undirected) edges.

    Returns ``{node_id: label}``; nodes sharing a label form a community.  A fixed
    node order (sorted ids) and a fixed tie-break (smallest label among the most
    frequent neighbour labels) make the same graph yield the same partition every
    run — no randomness, so the colours are reproducible.
    """
    adj: dict = {n.id: set() for n in graph.nodes}
    for e in graph.edges:
        if e.source in adj and e.target in adj:
            adj[e.source].add(e.target)
            adj[e.target].add(e.source)
    labels = {nid: nid for nid in adj}
    order = sorted(adj)
    for _ in range(_LPA_MAX_ITERS):
        changed = False
        for nid in order:
            nbrs = adj[nid]
            if not nbrs:
                continue
            counts: dict = {}
            for m in nbrs:
                counts[labels[m]] = counts.get(labels[m], 0) + 1
            top = max(counts.values())
            winner = min(lbl for lbl, c in counts.items() if c == top)
            if winner != labels[nid]:
                labels[nid] = winner
                changed = True
        if not changed:
            break
    return labels


def _degenerate(labels: dict) -> bool:
    """Whether a partition says nothing worth colouring: fewer than two communities,
    or the largest covers more than 70% of the nodes (a densely-linked vault
    collapses to a single label — see the Graph community-colouring ticket)."""
    if not labels:
        return True
    sizes: dict = {}
    for lbl in labels.values():
        sizes[lbl] = sizes.get(lbl, 0) + 1
    if len(sizes) < 2:
        return True
    return max(sizes.values()) > 0.70 * len(labels)


def _community_palette(labels: dict) -> dict:
    """A distinct colour per community. Consecutive communities are spread far apart on
    the hue wheel by a golden-ratio step (so many categories stay distinguishable), and
    lightness/saturation are cycled too, so communities on nearby hues still separate by
    brightness and vividness instead of blurring together."""
    comms = sorted(set(labels.values()))
    palette = {}
    for i, c in enumerate(comms):
        hue = (i * 0.61803398875) % 1.0
        light = 0.50 + (i % 3) * 0.08          # 0.50 / 0.58 / 0.66
        sat = 0.65 + (i % 2) * 0.20            # 0.65 / 0.85
        r, gr, b = colorsys.hls_to_rgb(hue, light, sat)
        palette[c] = "#{:02x}{:02x}{:02x}".format(int(r * 255), int(gr * 255), int(b * 255))
    return palette


def _base(path: str) -> str:
    p = path.replace("\\", "/").rstrip("/")
    return p.rsplit("/", 1)[-1] or path


def _representative(nodes: list):
    """The most important node of a group: highest degree, ties broken by the smallest
    id — the node a legend entry is named after and rings on focus. One rule, one place
    (the renderer takes the id from the legend, it never recomputes it)."""
    return min(nodes, key=lambda n: (-n.degree, n.id))


def _vault_legend(graph: Graph, vault_colors: dict) -> list:
    """One entry per vault (the fallback grouping): its colour and the id of the vault's
    highest-degree node, so a legend click rings it — same shape as the community legend."""
    members: dict = {}
    for n in graph.nodes:
        members.setdefault(n.vault, []).append(n)
    return [{"label": _base(v), "color": vault_colors.get(v, "#888888"),
             "id": _representative(ns).id} for v, ns in members.items()]


def _community_legend(graph: Graph, labels: dict, palette: dict) -> list:
    """One entry per community — the largest first — named by the community's
    highest-degree node (ties broken by id), since communities have no names."""
    members: dict = {}
    for n in graph.nodes:
        members.setdefault(labels[n.id], []).append(n)
    ordered = sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    legend = []
    for label, nodes in ordered:
        rep = _representative(nodes)
        legend.append({"label": rep.label, "color": palette[label], "id": rep.id})
    return legend


def color_and_legend(graph: Graph, vault_colors: dict):
    """Decide each node's colour and the graph's legend.

    Colours by detected community; falls back to the node's *vault* colour when the
    partition is degenerate (see :func:`_degenerate`), so a densely-linked single-vault
    graph does not collapse to one colour.  Returns ``(colors: {node_id: hex},
    legend: [{"label", "color", "id"}])`` — keeping the policy here, out of the serialiser.
    """
    labels = community_labels(graph)
    if _degenerate(labels):
        logger.info("graph: community detection degenerate — colouring by vault")
        colors = {n.id: vault_colors.get(n.vault, "#888888") for n in graph.nodes}
        return colors, _vault_legend(graph, vault_colors)
    palette = _community_palette(labels)
    logger.info("graph: coloured by %d communities", len(palette))
    colors = {n.id: palette[labels[n.id]] for n in graph.nodes}
    return colors, _community_legend(graph, labels, palette)
