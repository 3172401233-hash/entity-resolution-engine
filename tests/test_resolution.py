import unittest

from entity_merge import (
    BlockingEngine,
    EntityResolver,
    Record,
    model_blocker,
    name_prefix_blocker,
)
from entity_merge.resolver import Constraint


def make_record(
    record_id, name="", model="", group="A", metrics=None
) -> Record:
    return Record(
        id=record_id,
        name=name,
        model=model,
        group=group,
        metrics=metrics or {},
    )


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.resolver = EntityResolver(
            auto_threshold=0.8, review_threshold=0.6
        )

    def test_spelling_variant_auto_merges(self):
        left = [make_record("a1", "CNC  Mill  X200", "X200")]
        right = [make_record("b1", "cnc mill x200", "X-200", group="B")]

        result = self.resolver.resolve(left, right)

        edge = next(edge for edge in result.edges if edge.pair == ("a1", "b1"))
        self.assertEqual(edge.decision, "auto")
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(result.clusters[0].record_ids, ("a1", "b1"))
        self.assertIn(("a1", "b1"), result.clusters[0].evidence_edges)

    def test_empty_name_uses_model_and_metrics_without_false_match(self):
        left = [make_record("a1", "", "X200", metrics={"power": 10.0})]
        right = [
            make_record("b1", "", "X200", group="B", metrics={"power": 10.2}),
            make_record("b2", "", "Z900", group="B", metrics={"power": 50.0}),
        ]
        resolver = EntityResolver(
            blockers=(model_blocker(),),
            numeric_scales={"power": 1.0},
            auto_threshold=0.8,
            review_threshold=0.6,
        )

        result = resolver.resolve(left, right)

        merged = result.cluster_by_record
        self.assertEqual(merged["a1"], merged["b1"])
        self.assertNotEqual(merged["a1"], merged["b2"])
        edge_b1 = next(edge for edge in result.edges if edge.pair == ("a1", "b1"))
        self.assertEqual(edge_b1.best_component, "model")

    def test_same_name_different_model_stays_separate_or_review(self):
        left = [make_record("a1", "Hydraulic Press", "HP-100")]
        right = [make_record("b1", "Hydraulic Press", "HP-300", group="B")]

        result = self.resolver.resolve(left, right)
        edge = next(edge for edge in result.edges if edge.pair == ("a1", "b1"))

        self.assertNotEqual(
            result.cluster_by_record["a1"],
            result.cluster_by_record["b1"],
        )
        self.assertIn(edge.decision, ("review", "reject"))
        if edge.decision == "review":
            self.assertIn(edge, result.review_queue)

    def test_chain_similarity_merges_transitively_with_full_evidence(self):
        records_a = [
            make_record("a1", "Lathe Alpha 500", "LA-500"),
            make_record("a2", "Lathe Alfa 500", "LA-500"),
        ]
        records_b = [
            make_record("b1", "Lathe Alfa 500 T", "LA-500", group="B"),
        ]

        result = self.resolver.resolve(records_a, records_b)

        self.assertEqual(len(result.clusters), 1)
        cluster = result.clusters[0]
        self.assertEqual(cluster.record_ids, ("a1", "a2", "b1"))
        self.assertEqual(len(cluster.evidence_edges), 2)
        for edge in result.edges:
            if edge.decision == "auto":
                self.assertGreaterEqual(edge.score, self.resolver.auto_threshold)

    def test_cannot_link_blocks_chain_merge_and_reports_conflict_chain(self):
        records_a = [
            make_record("a1", "Lathe Alpha 500", "LA-500"),
            make_record("a2", "Lathe Alfa 500", "LA-500"),
        ]
        records_b = [
            make_record("b1", "Lathe Alfa 500 T", "LA-500", group="B"),
        ]
        constraints = [
            Constraint("must_link", "a1", "a2"),
            Constraint("cannot_link", "a1", "b1"),
        ]

        result = self.resolver.resolve(records_a, records_b, constraints)

        self.assertNotEqual(
            result.cluster_by_record["a1"],
            result.cluster_by_record["b1"],
        )
        self.assertEqual(
            result.cluster_by_record["a1"],
            result.cluster_by_record["a2"],
        )
        self.assertGreaterEqual(len(result.conflicts), 1)
        conflict = result.conflicts[0]
        self.assertEqual(conflict.constraint_pair, ("a1", "b1"))
        chain_pairs = [link.pair for link in conflict.chain]
        self.assertIn(("a1", "b1"), chain_pairs)
        self.assertEqual(conflict.rule, "cannot_link_overrides")

    def test_must_and_cannot_direct_conflict_prefers_cannot_link(self):
        left = [make_record("a1", "Robot Arm R7", "R7")]
        right = [make_record("b1", "Robot Arm R7", "R7", group="B")]
        constraints = [
            Constraint("must_link", "a1", "b1"),
            Constraint("cannot_link", "a1", "b1"),
        ]

        result = self.resolver.resolve(left, right, constraints)

        self.assertNotEqual(
            result.cluster_by_record["a1"],
            result.cluster_by_record["b1"],
        )
        edge = next(edge for edge in result.edges if edge.pair == ("a1", "b1"))
        self.assertEqual(edge.decision, "blocked")
        self.assertTrue(result.conflicts)

    def test_deep_conflict_chain_includes_accepted_path(self):
        records_a = [
            make_record("A", "Pump Alpha 100", "P-100"),
            make_record("B", "Pump Alpha 100", "P-100"),
        ]
        records_b = [
            make_record("C", "Pump Alpha 100", "P-100", group="B"),
            make_record("D", "Pump Alpha 100", "P-100", group="B"),
        ]
        resolver = EntityResolver(
            auto_threshold=0.8,
            review_threshold=0.6,
            include_same_group=True,
        )
        result = resolver.resolve(
            records_a,
            records_b,
            [
                Constraint("must_link", "A", "B"),
                Constraint("must_link", "B", "D"),
                Constraint("cannot_link", "A", "C"),
            ],
        )

        # A、B、D 同组，C 因 cannot-link 被拆出去。
        self.assertEqual(
            result.cluster_by_record["A"],
            result.cluster_by_record["D"],
        )
        self.assertNotEqual(
            result.cluster_by_record["A"],
            result.cluster_by_record["C"],
        )

        deep = next(
            conflict
            for conflict in result.conflicts
            if conflict.rejected_pair == ("B", "C")
        )
        chain_pairs = [link.pair for link in deep.chain]
        self.assertEqual(
            chain_pairs, [("B", "C"), ("A", "B"), ("A", "C")]
        )
        sources = [link.source for link in deep.chain]
        self.assertEqual(sources, ["candidate", "must_link", "cannot_link"])

    def test_blocking_failure_produces_no_candidates_no_crash(self):
        strict_blocker = name_prefix_blocker(8)
        resolver = EntityResolver(blockers=(strict_blocker,))
        left = [make_record("a1", "Conveyor Belt A", "CB-1")]
        right = [make_record("b1", "Compressor B", "CP-9", group="B")]

        result = resolver.resolve(left, right)

        self.assertEqual(result.edges, ())
        self.assertEqual(len(result.clusters), 2)
        self.assertEqual(result.review_queue, ())

    def test_cannot_link_without_candidate_still_emits_synthetic_edge(self):
        strict_blocker = name_prefix_blocker(8)
        resolver = EntityResolver(blockers=(strict_blocker,))
        left = [make_record("a1", "Conveyor Belt A", "CB-1")]
        right = [make_record("b1", "Compressor B", "CP-9", group="B")]

        result = resolver.resolve(
            left, right, [Constraint("cannot_link", "a1", "b1")]
        )

        edge = next(edge for edge in result.edges if edge.pair == ("a1", "b1"))
        self.assertEqual(edge.decision, "blocked")
        self.assertEqual(edge.source, "cannot_link")
        self.assertEqual(edge.basis, ("cannot_link",))
        self.assertNotEqual(
            result.cluster_by_record["a1"],
            result.cluster_by_record["b1"],
        )

    def test_multiple_blockers_recover_candidate_with_block_key_evidence(self):
        resolver = EntityResolver(
            blockers=(name_prefix_blocker(6), model_blocker()),
            auto_threshold=0.8,
            review_threshold=0.6,
        )
        left = [make_record("a1", "Drill Press Pro", "DP-20")]
        right = [make_record("b1", "Different Label", "DP-20", group="B")]

        result = resolver.resolve(left, right)

        edge = next(edge for edge in result.edges if edge.pair == ("a1", "b1"))
        self.assertIn("model:dp20", edge.block_keys)
        self.assertIn("model:dp20", edge.basis)

    def test_input_order_does_not_change_result(self):
        records_a = [
            make_record("a1", "Lathe Alpha 500", "LA-500"),
            make_record("a2", "Grinder G10", "G10"),
            make_record("a3", "Lathe Alfa 500", "LA-500"),
        ]
        records_b = [
            make_record("b2", "Grinder G-10", "G10", group="B"),
            make_record("b1", "Lathe Alfa 500 T", "LA-500", group="B"),
        ]
        constraints = [Constraint("cannot_link", "a3", "b1")]

        first = self.resolver.resolve(
            list(records_a), list(records_b), constraints
        )
        second = self.resolver.resolve(
            list(reversed(records_a)),
            list(reversed(records_b)),
            list(reversed(constraints)),
        )

        def fingerprint(result):
            return (
                tuple(
                    (cluster.id, cluster.record_ids, cluster.evidence_edges)
                    for cluster in result.clusters
                ),
                tuple(
                    (edge.pair, edge.decision, edge.score)
                    for edge in result.edges
                ),
                tuple(
                    (conflict.rejected_pair, conflict.constraint_pair)
                    for conflict in result.conflicts
                ),
            )

        self.assertEqual(fingerprint(first), fingerprint(second))

    def test_grey_zone_pairs_never_auto_cluster(self):
        resolver = EntityResolver(
            auto_threshold=0.95, review_threshold=0.3
        )
        left = [make_record("a1", "CNC Mill X200", "X200")]
        right = [make_record("b1", "CNC Mll X200", "X200", group="B")]

        result = resolver.resolve(left, right)
        edge = next(edge for edge in result.edges if edge.pair == ("a1", "b1"))

        self.assertEqual(edge.decision, "review")
        self.assertEqual(len(result.clusters), 2)
        self.assertEqual([review.pair for review in result.review_queue], [("a1", "b1")])

    def test_clusters_are_explainable_via_edges_and_components(self):
        left = [
            make_record(
                "a1", "CNC Mill X200", "X200", metrics={"power": 12.0}
            )
        ]
        right = [
            make_record(
                "b1", "CNC Mill X200", "X200", group="B", metrics={"power": 12.5}
            )
        ]
        resolver = EntityResolver(
            numeric_scales={"power": 2.0},
            auto_threshold=0.8,
            review_threshold=0.6,
        )

        result = resolver.resolve(left, right)
        edge = result.edges[0]

        self.assertTrue(edge.components)
        component_keys = {component.key for component in edge.components}
        self.assertEqual(
            component_keys, {"name", "model", "metric:power"}
        )
        self.assertAlmostEqual(edge.cost, 1.0 - edge.score, places=6)
        self.assertTrue(edge.block_keys)
        self.assertTrue(edge.basis)

    def test_invalid_thresholds_and_constraint_ids_raise(self):
        with self.assertRaises(ValueError):
            EntityResolver(auto_threshold=0.4, review_threshold=0.6)
        with self.assertRaises(ValueError):
            BlockingEngine(blockers=[])

        left = [make_record("a1", "Mill", "M1")]
        right = [make_record("b1", "Mill", "M1", group="B")]
        with self.assertRaises(ValueError):
            self.resolver.resolve(
                left, right, [Constraint("must_link", "a1", "missing")]
            )

    def test_duplicate_ids_rejected(self):
        left = [make_record("a1", "Mill", "M1")]
        right = [make_record("a1", "Mill", "M1", group="B")]
        with self.assertRaises(ValueError):
            self.resolver.resolve(left, right)


if __name__ == "__main__":
    unittest.main()
