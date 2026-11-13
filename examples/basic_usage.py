"""最小可运行示例：两份设备清单的实体归并。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from entity_merge import EntityResolver, Record
from entity_merge.resolver import Constraint


inventory_2025 = [
    Record("old-1", "CNC  Mill  X200", "X200", group="2025", metrics={"power": 12.0}),
    Record("old-2", "Hydraulic Press", "HP-100", group="2025"),
    Record("old-3", "Grinder G10", "G10", group="2025"),
]

inventory_2026 = [
    Record("new-1", "cnc mill x200", "X-200", group="2026", metrics={"power": 12.4}),
    Record("new-2", "Hydraulic Press", "HP-300", group="2026"),
    Record("new-3", "Grinder G-10", "G10", group="2026"),
]

resolver = EntityResolver(
    auto_threshold=0.85,
    review_threshold=0.6,
    numeric_scales={"power": 2.0},
)

result = resolver.resolve(
    inventory_2025,
    inventory_2026,
    constraints=[
        Constraint("must_link", "old-1", "new-1", reason="资产标签确认同一台"),
        Constraint("cannot_link", "old-3", "new-3", reason="不同产线设备"),
    ],
)

for cluster in result.clusters:
    print(cluster.id, cluster.record_ids, "evidence:", cluster.evidence_edges)

for edge in result.review_queue:
    print("需要人工复核:", edge.pair, edge.score, edge.reason)

for conflict in result.conflicts:
    print("约束冲突:", conflict.rejected_pair, "被", conflict.constraint_pair, "拦截")
    for link in conflict.chain:
        print("  链路:", link.pair, link.source, link.reason)
