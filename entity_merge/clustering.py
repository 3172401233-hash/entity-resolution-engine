from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Mapping


def sorted_pair(id_a: str, id_b: str) -> tuple[str, str]:
    return tuple(sorted((id_a, id_b)))


@dataclass(frozen=True)
class ChainLink:
    """冲突链中的一段证据。"""

    pair: tuple[str, str]
    source: str
    score: float | None
    reason: str


@dataclass(frozen=True)
class Conflict:
    """一次被 cannot-link 拦下的合并尝试及其完整冲突链。"""

    rejected_pair: tuple[str, str]
    constraint_pair: tuple[str, str]
    rule: str
    chain: tuple[ChainLink, ...]


@dataclass
class Edge:
    """一对候选记录的完整决策记录：分数、代价、依据与最终决定。"""

    pair: tuple[str, str]
    score: float
    cost: float
    source: str
    decision: str
    block_keys: tuple[str, ...] = ()
    components: tuple = ()
    basis: tuple[str, ...] = ()
    best_component: str | None = None
    best_detail: str | None = None
    reason: str = ""


class _DSU:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, node: str) -> None:
        if node not in self.parent:
            self.parent[node] = node

    def find(self, node: str) -> str:
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != node:
            parent = self.parent[node]
            self.parent[node] = root
            node = parent
        return root

    def union(self, node_a: str, node_b: str) -> None:
        root_a = self.find(node_a)
        root_b = self.find(node_b)
        if root_a == root_b:
            return
        # 字典序小的根胜出，保证不同输入顺序得到完全一致的组件。
        if root_a < root_b:
            self.parent[root_b] = root_a
        else:
            self.parent[root_a] = root_b


class MergeEngine:
    """基于并查集的合并引擎，强制约束优先于相似度证据。

    确定性规则：
    1. cannot-link 是硬约束，任何合并只要跨越受限制对都拒绝；
    2. 拒绝时记录从合并两端到 cannot-link 两端的完整证据链；
    3. 并查集根节点按字典序稳定，合并结果与输入顺序无关。
    """

    def __init__(self, all_ids: set[str], cannot_links: set[tuple[str, str]]):
        self.dsu = _DSU()
        for node_id in sorted(all_ids):
            self.dsu.add(node_id)
        self.cannot_links = {sorted_pair(a, b) for a, b in cannot_links}
        self.adjacency: dict[str, set[str]] = {node_id: set() for node_id in all_ids}
        self.edge_index: dict[tuple[str, str], Edge] = {}
        self.conflicts: list[Conflict] = []

    def _crossing_cannot_link(self, id_a: str, id_b: str) -> tuple[str, str] | None:
        root_a = self.dsu.find(id_a)
        root_b = self.dsu.find(id_b)
        for restricted in sorted(self.cannot_links):
            left, right = restricted
            if {self.dsu.find(left), self.dsu.find(right)} == {root_a, root_b}:
                return restricted
        return None

    def try_merge(self, edge: Edge) -> Conflict | None:
        id_a, id_b = edge.pair
        self.edge_index[sorted_pair(id_a, id_b)] = edge
        restricted = self._crossing_cannot_link(id_a, id_b)
        if restricted is None:
            self.dsu.union(id_a, id_b)
            self.adjacency.setdefault(id_a, set()).add(id_b)
            self.adjacency.setdefault(id_b, set()).add(id_a)
            return None

        edge.decision = "blocked"
        edge.reason = f"命中 cannot-link 约束 {restricted}"
        conflict = self._build_conflict(edge.pair, restricted)
        self.conflicts.append(conflict)
        return conflict

    def _path_links(self, start: str, end: str) -> list[ChainLink]:
        if start == end:
            return []
        previous: dict[str, str] = {start: start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if current == end:
                break
            for neighbor in sorted(self.adjacency[current]):
                if neighbor not in previous:
                    previous[neighbor] = current
                    queue.append(neighbor)
        if end not in previous:
            return []

        links: list[ChainLink] = []
        current = end
        while current != start:
            parent = previous[current]
            pair = sorted_pair(parent, current)
            edge = self.edge_index[pair]
            links.append(
                ChainLink(
                    pair=pair,
                    source=edge.source,
                    score=edge.score,
                    reason=edge.reason or edge.best_detail or edge.decision,
                )
            )
            current = parent
        links.reverse()
        return links

    def _build_conflict(
        self,
        rejected_pair: tuple[str, str],
        restricted: tuple[str, str],
    ) -> Conflict:
        rejected_a, rejected_b = rejected_pair
        restricted_a, restricted_b = restricted
        chain: list[ChainLink] = []
        if rejected_pair != restricted:
            rejected_edge = self.edge_index[rejected_pair]
            chain.append(
                ChainLink(
                    pair=rejected_pair,
                    source=rejected_edge.source,
                    score=rejected_edge.score,
                    reason=rejected_edge.reason
                    or rejected_edge.best_detail
                    or rejected_edge.decision,
                )
            )
            # 若拒绝端与约束端不在同一组件，补充已接受图上的证据路径。
            chain.extend(self._path_links(rejected_a, restricted_a))
        chain.append(
            ChainLink(
                pair=restricted,
                source="cannot_link",
                score=None,
                reason="硬性 cannot-link 约束，禁止合并",
            )
        )
        if rejected_pair != restricted:
            chain.extend(self._path_links(restricted_b, rejected_b))

        return Conflict(
            rejected_pair=rejected_pair,
            constraint_pair=restricted,
            rule="cannot_link_overrides",
            chain=tuple(chain),
        )

    def clusters(self) -> list[Cluster]:
        grouped: dict[str, list[str]] = {}
        for node_id in sorted(self.adjacency):
            grouped.setdefault(self.dsu.find(node_id), []).append(node_id)

        clusters: list[Cluster] = []
        for root in sorted(grouped):
            member_ids = sorted(grouped[root])
            member_set = set(member_ids)
            evidence = sorted(
                (
                    edge
                    for pair, edge in self.edge_index.items()
                    if pair[0] in member_set and pair[1] in member_set
                ),
                key=lambda edge: edge.pair,
            )
            clusters.append(
                Cluster(
                    id=f"cluster-{len(clusters) + 1:03d}",
                    record_ids=tuple(member_ids),
                    evidence_edges=tuple(edge.pair for edge in evidence),
                )
            )
        return clusters


@dataclass(frozen=True)
class Cluster:
    """并查集输出的一个连通组件。"""

    id: str
    record_ids: tuple[str, ...]
    evidence_edges: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Resolution:
    """一次归并的完整结果，所有集合均按确定顺序排列。"""

    clusters: tuple[Cluster, ...]
    edges: tuple[Edge, ...]
    conflicts: tuple[Conflict, ...]
    review_queue: tuple[Edge, ...]
    cluster_by_record: Mapping[str, str] = field(default_factory=dict)
