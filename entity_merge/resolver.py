from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .blocking import (
    Blocker,
    BlockingEngine,
    model_blocker,
    name_prefix_blocker,
    name_token_blocker,
)
from .clustering import Cluster, Conflict, Edge, MergeEngine, Resolution, sorted_pair
from .records import Record
from .similarity import DEFAULT_WEIGHTS, score_pair

DECISION_AUTO = "auto"
DECISION_REVIEW = "review"
DECISION_REJECT = "reject"
DECISION_BLOCKED = "blocked"


@dataclass(frozen=True)
class Constraint:
    """成对约束：kind 为 must_link 或 cannot_link。"""

    kind: str
    id_a: str
    id_b: str
    reason: str = ""


class EntityResolver:
    """实体归并主入口。

    流程：blocking 缩小候选 -> 逐对打分 -> 约束与自动边按确定性顺序
    喂给 MergeEngine -> 输出 union-find 风格的 cluster、全量边与复核队列。

    auto_threshold 与 review_threshold 均作用于综合相似度（0~1）：
    score >= auto_threshold 才允许自动合并；review_threshold <= score <
    auto_threshold 进入人工复核区，绝不自动聚类。
    """

    def __init__(
        self,
        blockers: Sequence[Blocker] | None = None,
        weights: Mapping[str, float] | None = None,
        numeric_scales: Mapping[str, float] | None = None,
        auto_threshold: float = 0.85,
        review_threshold: float = 0.6,
        include_same_group: bool = False,
    ):
        if not 0.0 <= review_threshold <= auto_threshold <= 1.0:
            raise ValueError(
                "需满足 0 <= review_threshold <= auto_threshold <= 1"
            )
        if blockers is None:
            blockers = (name_prefix_blocker(4), name_token_blocker(), model_blocker())
        self.blocking = BlockingEngine(blockers)
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self.numeric_scales = dict(numeric_scales or {})
        self.auto_threshold = auto_threshold
        self.review_threshold = review_threshold
        self.include_same_group = include_same_group

    def resolve(
        self,
        records_a: Iterable[Record],
        records_b: Iterable[Record],
        constraints: Iterable[Constraint] = (),
    ) -> Resolution:
        list_a = list(records_a)
        list_b = list(records_b)
        all_records = list_a + list_b
        self._validate_inputs(all_records, constraints)
        records_by_id = {record.id: record for record in all_records}
        all_ids = set(records_by_id)

        must_links = sorted(
            {
                sorted_pair(constraint.id_a, constraint.id_b)
                for constraint in constraints
                if constraint.kind == "must_link"
            }
        )
        cannot_links = sorted(
            {
                sorted_pair(constraint.id_a, constraint.id_b)
                for constraint in constraints
                if constraint.kind == "cannot_link"
            }
        )

        candidate_pairs, pair_block_keys = self.blocking.candidate_pairs(
            list_a, list_b, include_same_group=self.include_same_group
        )
        # must-link 不受分块限制，强制补回候选对。
        candidate_pairs.update(must_links)

        scored_edges: list[Edge] = []
        for pair in sorted(candidate_pairs):
            edge = self._score_edge(
                pair,
                records_by_id,
                pair_block_keys.get(pair, ()),
                forced=pair in must_links,
            )
            scored_edges.append(edge)

        engine = MergeEngine(all_ids, set(cannot_links))

        # 先处理 must-link，确定性优先；硬约束冲突会被记录而不是静默丢弃。
        must_pairs = set(must_links)
        cannot_pairs = set(cannot_links)
        for pair in must_links:
            edge = next(edge for edge in scored_edges if edge.pair == pair)
            engine.try_merge(edge)

        # 普通候选边按分数降序、代价升序、ID 字典序处理，顺序与输入排列无关。
        # 落在 cannot-link 上的候选边同样经过 try_merge，才能产出冲突链。
        candidate_edges = sorted(
            (
                edge
                for edge in scored_edges
                if edge.pair not in must_pairs
            ),
            key=lambda edge: (-edge.score, edge.cost, edge.pair),
        )
        for edge in candidate_edges:
            if edge.pair in cannot_pairs:
                engine.try_merge(edge)
                continue
            if edge.decision != DECISION_AUTO:
                continue
            engine.try_merge(edge)

        # 被拒绝/复核边也登记进 edge_index，保证 cluster 解释能引用全部证据。
        for edge in scored_edges:
            engine.edge_index.setdefault(edge.pair, edge)

        # cannot-link 优先复用已有的候选边并补充约束标记；没有候选边时才
        # 生成合成边，保证同一个 pair 在 edges 中只出现一次。
        all_edges = list(scored_edges)
        scored_pairs = {edge.pair for edge in scored_edges}
        for pair in cannot_links:
            if pair in scored_pairs:
                existing = next(edge for edge in all_edges if edge.pair == pair)
                if existing.decision != DECISION_BLOCKED:
                    existing.decision = DECISION_BLOCKED
                existing.source = "cannot_link"
                existing.basis = tuple(dict.fromkeys(existing.basis + ("cannot_link",)))
                existing.best_component = "constraint"
                existing.best_detail = "硬性 cannot-link 约束"
                if not existing.reason.startswith("命中 cannot-link"):
                    existing.reason = f"命中 cannot-link 约束 {pair}"
                continue
            all_edges.append(
                Edge(
                    pair=pair,
                    score=0.0,
                    cost=1.0,
                    source="cannot_link",
                    decision=DECISION_BLOCKED,
                    basis=("cannot_link",),
                    best_component="constraint",
                    best_detail="硬性 cannot-link 约束",
                    reason="禁止合并",
                )
            )

        clusters = engine.clusters()
        cluster_by_record = {
            record_id: cluster.id
            for cluster in clusters
            for record_id in cluster.record_ids
        }
        review_queue = tuple(
            sorted(
                (edge for edge in all_edges if edge.decision == DECISION_REVIEW),
                key=lambda edge: (-edge.score, edge.pair),
            )
        )
        return Resolution(
            clusters=tuple(clusters),
            edges=tuple(sorted(all_edges, key=lambda edge: edge.pair)),
            conflicts=tuple(
                sorted(
                    engine.conflicts,
                    key=lambda conflict: (
                        conflict.rejected_pair,
                        conflict.constraint_pair,
                    ),
                )
            ),
            review_queue=review_queue,
            cluster_by_record=cluster_by_record,
        )

    def _score_edge(
        self,
        pair: tuple[str, str],
        records_by_id: Mapping[str, Record],
        block_keys: tuple[str, ...],
        forced: bool,
    ) -> Edge:
        record_a = records_by_id[pair[0]]
        record_b = records_by_id[pair[1]]
        score, cost, components = score_pair(
            record_a, record_b, self.weights, self.numeric_scales
        )
        if components:
            best = max(components, key=lambda component: component.score)
            best_component = best.key
            best_detail = best.detail
        else:
            best_component = None
            best_detail = None

        if forced:
            decision = DECISION_AUTO
            reason = "must_link 强制合并"
            source = "must_link"
        elif score >= self.auto_threshold:
            decision = DECISION_AUTO
            reason = f"score {score:.3f} >= auto_threshold {self.auto_threshold:.3f}"
            source = "candidate"
        elif score >= self.review_threshold:
            decision = DECISION_REVIEW
            reason = (
                f"score {score:.3f} 落在复核区 "
                f"[{self.review_threshold:.3f}, {self.auto_threshold:.3f})"
            )
            source = "candidate"
        else:
            decision = DECISION_REJECT
            reason = f"score {score:.3f} < review_threshold {self.review_threshold:.3f}"
            source = "candidate"

        return Edge(
            pair=pair,
            score=score,
            cost=cost,
            source=source,
            decision=decision,
            block_keys=block_keys,
            components=components,
            basis=tuple(
                list(block_keys) + [component.key for component in components]
            ),
            best_component=best_component,
            best_detail=best_detail,
            reason=reason,
        )

    def _validate_inputs(
        self,
        records: Sequence[Record],
        constraints: Iterable[Constraint],
    ) -> None:
        seen: set[str] = set()
        for record in records:
            if record.id in seen:
                raise ValueError(f"重复的记录 ID: {record.id!r}")
            seen.add(record.id)
        for constraint in constraints:
            if constraint.kind not in ("must_link", "cannot_link"):
                raise ValueError(
                    f"未知约束类型 {constraint.kind!r}，"
                    "只支持 must_link / cannot_link"
                )
            if constraint.id_a not in seen or constraint.id_b not in seen:
                raise ValueError(
                    f"约束 {constraint.id_a!r}/{constraint.id_b!r} 引用了不存在的记录"
                )
            if constraint.id_a == constraint.id_b:
                raise ValueError("约束两端不能是同一条记录")
