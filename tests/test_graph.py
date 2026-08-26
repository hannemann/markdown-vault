"""Tests for markdown_vault.graph.graph — knowledge-graph data layer (pure logic)."""

import unittest

from markdown_vault.graph import graph as g


class TestBuildGraph(unittest.TestCase):
    def _fv(self):
        return {
            "/v/a.md": "/v", "/v/b.md": "/v", "/v/c.md": "/v",
            "/w/d.md": "/w",
        }

    def test_nodes_labels_and_vaults(self):
        gr = g.build_graph(self._fv(), [])
        by_id = {n.id: n for n in gr.nodes}
        self.assertEqual(by_id["/v/a.md"].label, "a")
        self.assertEqual(by_id["/w/d.md"].vault, "/w")
        self.assertEqual(len(gr.nodes), 4)

    def test_edges_and_degree(self):
        gr = g.build_graph(self._fv(), [("/v/a.md", "/v/b.md"),
                                        ("/v/a.md", "/w/d.md")])
        by_id = {n.id: n for n in gr.nodes}
        self.assertEqual(len(gr.edges), 2)
        self.assertEqual(by_id["/v/a.md"].degree, 2)
        self.assertEqual(by_id["/v/b.md"].degree, 1)
        self.assertEqual(by_id["/v/c.md"].degree, 0)

    def test_tags_attached_to_nodes(self):
        gr = g.build_graph(self._fv(), [],
                           file_tags={"/v/a.md": ["work", "urgent"]})
        by_id = {n.id: n for n in gr.nodes}
        self.assertEqual(by_id["/v/a.md"].tags, ["work", "urgent"])
        self.assertEqual(by_id["/v/b.md"].tags, [])  # untagged → empty

    def test_drops_unknown_self_and_duplicate_edges(self):
        gr = g.build_graph(self._fv(), [
            ("/v/a.md", "/v/a.md"),        # self-loop
            ("/v/a.md", "/missing.md"),    # unknown target
            ("/v/a.md", "/v/b.md"),
            ("/v/a.md", "/v/b.md"),        # duplicate
        ])
        self.assertEqual(len(gr.edges), 1)
        self.assertEqual({n.id: n.degree for n in gr.nodes}["/v/a.md"], 1)


class TestLocalGraph(unittest.TestCase):
    def _graph(self):
        fv = {f"/v/{c}.md": "/v" for c in "abcde"}
        # a-b-c chain, a-d, e isolated
        edges = [("/v/a.md", "/v/b.md"), ("/v/b.md", "/v/c.md"),
                 ("/v/a.md", "/v/d.md")]
        return g.build_graph(fv, edges)

    def test_one_hop(self):
        sub = g.local_graph(self._graph(), "/v/a.md", depth=1)
        self.assertEqual(sub.node_ids(), {"/v/a.md", "/v/b.md", "/v/d.md"})

    def test_two_hops(self):
        sub = g.local_graph(self._graph(), "/v/a.md", depth=2)
        self.assertEqual(sub.node_ids(),
                         {"/v/a.md", "/v/b.md", "/v/c.md", "/v/d.md"})

    def test_isolated_center_is_lone_node(self):
        sub = g.local_graph(self._graph(), "/v/e.md", depth=1)
        self.assertEqual(sub.node_ids(), {"/v/e.md"})
        self.assertEqual(sub.edges, [])

    def test_unknown_center_is_empty(self):
        sub = g.local_graph(self._graph(), "/v/zzz.md", depth=1)
        self.assertEqual(sub.nodes, [])
        self.assertEqual(sub.edges, [])


class TestEdgesFromBacklinks(unittest.TestCase):
    def test_resolves_keys_to_files_and_skips_broken(self):
        source_to_targets = {
            "/v/a.md": {"V:b", "V:missing"},  # V:missing has no file → skipped
            "/v/b.md": {"V:a"},
        }
        key_to_file = {"V:a": "/v/a.md", "V:b": "/v/b.md"}
        edges = sorted(g.edges_from_backlinks(source_to_targets, key_to_file))
        self.assertEqual(edges, [("/v/a.md", "/v/b.md"), ("/v/b.md", "/v/a.md")])

    def test_feeds_build_graph(self):
        fv = {"/v/a.md": "/v", "/v/b.md": "/v"}
        edges = g.edges_from_backlinks({"/v/a.md": {"V:b"}}, {"V:b": "/v/b.md"})
        gr = g.build_graph(fv, edges)
        self.assertEqual(len(gr.edges), 1)
        self.assertEqual({n.id: n.degree for n in gr.nodes}["/v/b.md"], 1)


class TestPaletteAndPayload(unittest.TestCase):
    def test_color_is_stable_per_vault_regardless_of_order(self):
        # Same vault path → same colour, independent of order/other vaults.
        self.assertEqual(g.vault_palette(["/v", "/w"])["/v"],
                         g.vault_palette(["/w", "/x", "/v"])["/v"])
        self.assertEqual(g.color_for_vault("/v"), g.vault_palette(["/v"])["/v"])

    def test_distinct_vaults_get_distinct_colors(self):
        pal = g.vault_palette(["/alpha", "/beta", "/gamma"])
        self.assertEqual(len(set(pal.values())), 3)

    def test_color_format(self):
        self.assertRegex(g.color_for_vault("/v"), r"^#[0-9a-f]{6}$")

    def test_payload_shape_and_center(self):
        gr = g.build_graph({"/v/a.md": "/v", "/v/b.md": "/v"},
                           [("/v/a.md", "/v/b.md")],
                           file_tags={"/v/a.md": ["work"]})
        colors = {"/v/a.md": "#123456", "/v/b.md": "#123456"}   # keyed by node id now
        payload = g.to_payload(gr, colors, center="/v/a.md",
                               legend=[{"label": "a", "color": "#123456"}])
        centre = [n for n in payload["nodes"] if n["center"]]
        self.assertEqual(len(centre), 1)
        self.assertEqual(centre[0]["id"], "/v/a.md")
        self.assertEqual(centre[0]["color"], "#123456")
        self.assertEqual(centre[0]["tags"], ["work"])
        self.assertEqual(payload["edges"], [{"source": "/v/a.md", "target": "/v/b.md"}])
        self.assertEqual(payload["legend"], [{"label": "a", "color": "#123456"}])


class TestCommunityColoring(unittest.TestCase):
    def _clique(self, prefix, names):
        edges = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                edges.append((f"/v/{prefix}{names[i]}.md", f"/v/{prefix}{names[j]}.md"))
        return edges

    def _two_cliques(self):
        # Two disconnected K4 cliques {x1..x4}, {y1..y4} — two clean communities.
        fv = {f"/v/{p}{i}.md": "/v" for p in "xy" for i in "1234"}
        return g.build_graph(fv, self._clique("x", "1234") + self._clique("y", "1234"))

    def test_two_cliques_two_communities(self):
        labels = g.community_labels(self._two_cliques())
        xs = {labels[f"/v/x{i}.md"] for i in "1234"}
        ys = {labels[f"/v/y{i}.md"] for i in "1234"}
        self.assertEqual(len(xs), 1)           # each clique shares one label
        self.assertEqual(len(ys), 1)
        self.assertNotEqual(xs, ys)            # and the two cliques differ

    def test_deterministic(self):
        gr = self._two_cliques()
        self.assertEqual(g.community_labels(gr), g.community_labels(gr))

    def test_partition_independent_of_edge_order(self):
        gr1 = self._two_cliques()
        fv = {n.id: n.vault for n in gr1.nodes}
        gr2 = g.build_graph(fv, [(e.target, e.source) for e in reversed(gr1.edges)])

        def partition(gr):
            groups = {}
            for nid, lab in g.community_labels(gr).items():
                groups.setdefault(lab, set()).add(nid)
            return {frozenset(s) for s in groups.values()}

        self.assertEqual(partition(gr1), partition(gr2))

    def test_dense_graph_falls_back_to_vault(self):
        # One complete graph -> LPA collapses to a single community (degenerate) ->
        # colour by vault, not one useless single-colour "community".
        names = [str(i) for i in range(6)]
        gr = g.build_graph({f"/v/n{i}.md": "/v" for i in range(6)},
                           self._clique("n", names))
        vault_colors = g.vault_palette(["/v"])
        colors, legend = g.color_and_legend(gr, vault_colors)
        self.assertTrue(all(c == vault_colors["/v"] for c in colors.values()))
        self.assertEqual(legend, [{"label": "v", "color": vault_colors["/v"],
                                   "id": "/v/n0.md"}])

    def test_community_mode_distinct_colors(self):
        colors, _ = g.color_and_legend(self._two_cliques(), g.vault_palette(["/v"]))
        xcol = {colors[f"/v/x{i}.md"] for i in "1234"}
        ycol = {colors[f"/v/y{i}.md"] for i in "1234"}
        self.assertEqual(len(xcol), 1)         # one colour per community
        self.assertEqual(len(ycol), 1)
        self.assertNotEqual(xcol, ycol)

    def test_legend_names_communities_by_highest_degree_node(self):
        _, legend = g.color_and_legend(self._two_cliques(), g.vault_palette(["/v"]))
        self.assertEqual({e["label"] for e in legend}, {"x1", "y1"})
        self.assertEqual({e["id"] for e in legend}, {"/v/x1.md", "/v/y1.md"})

    def test_membership_stable_when_a_note_is_added(self):
        before = g.community_labels(self._two_cliques())
        gr2 = self._two_cliques()
        fv = {n.id: n.vault for n in gr2.nodes}
        fv["/v/z.md"] = "/v"
        edges = [(e.source, e.target) for e in gr2.edges] + [("/v/z.md", "/v/x2.md")]
        after = g.community_labels(g.build_graph(fv, edges))
        for grp in ("x", "y"):
            self.assertEqual(len({before[f"/v/{grp}{i}.md"] for i in "1234"}), 1)
            self.assertEqual(len({after[f"/v/{grp}{i}.md"] for i in "1234"}), 1)
        self.assertNotEqual(after["/v/x1.md"], after["/v/y1.md"])

    def test_single_node_falls_back(self):
        gr = g.build_graph({"/v/a.md": "/v"}, [])
        colors, _ = g.color_and_legend(gr, g.vault_palette(["/v"]))
        self.assertEqual(colors, {"/v/a.md": g.color_for_vault("/v")})


if __name__ == "__main__":
    unittest.main()
