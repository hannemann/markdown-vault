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
        payload = g.to_payload(gr, g.vault_palette(["/v"]), center="/v/a.md")
        centre = [n for n in payload["nodes"] if n["center"]]
        self.assertEqual(len(centre), 1)
        self.assertEqual(centre[0]["id"], "/v/a.md")
        self.assertEqual(centre[0]["color"], g.color_for_vault("/v"))
        self.assertEqual(centre[0]["tags"], ["work"])
        self.assertEqual(payload["edges"], [{"source": "/v/a.md", "target": "/v/b.md"}])


if __name__ == "__main__":
    unittest.main()
