# entity-resolution-engine

纯 Python（仅标准库）实体归并库：不接数据库、不做管理页面。输入两组设备
清单记录，通过可配置分块缩小候选，再综合名称、型号和可选数值字段计算匹配
代价，输出接近 union-find 的可解释 cluster，并保留每一条候选边、分数、
采用依据和约束冲突链。

## 适用场景

- 两份年度设备清单对齐，名称可能只差空格、大小写、拼写或一个后缀。
- 直接按名称合并会把同名不同型号的机器并在一起；只按稳定 ID 又会漏掉改名记录。
- 需要 must-link / cannot-link 约束，且约束在向整组传递时可能产生冲突。
- 灰区候选必须保留人工复核，不允许为了减少 cluster 数量强行聚类。

## 快速开始

```python
from entity_merge import EntityResolver, Record
from entity_merge.resolver import Constraint

list_a = [Record("a1", "CNC Mill X200", "X200", group="A")]
list_b = [Record("b1", "cnc mill x200", "X-200", group="B")]

resolver = EntityResolver(auto_threshold=0.85, review_threshold=0.6)
result = resolver.resolve(
    list_a, list_b,
    constraints=[Constraint("must_link", "a1", "b1")],
)

for cluster in result.clusters:
    print(cluster.record_ids, cluster.evidence_edges)
```

完整示例见 `examples/basic_usage.py`。

## 处理流程

1. **分块（blocking）**：默认组合名称前缀、名称 token、型号精确三个 blocker，
   任一命中即成为候选对，命中的 block key 会记录在边上作为依据。可传入自定义
   blocker，签名为 `Record -> tuple[str, ...]`，返回空元组表示该记录不进此分块。
2. **打分（scoring）**：对每个候选对计算名称相似度、型号相似度和数值距离相似度，
   按权重归一化得到 0~1 的综合 `score`，匹配代价 `cost = 1 - score`。
   缺失字段的分量直接剔除并重归一化，避免缺字段拉低或抬高总分。
3. **决策（decision）**：依据两个阈值把边分为自动合并、人工复核、拒绝三类。
4. **约束聚类（constrained clustering）**：并查集按确定性顺序合并；
   cannot-link 是硬约束，任何跨越受限制对的合并都会被拒绝并记录冲突链。

## 阈值与复核区

构造参数 `auto_threshold` 与 `review_threshold` 都作用于综合相似度，必须满足
`0 <= review_threshold <= auto_threshold <= 1`：

- `score >= auto_threshold`：标记为 `auto`，允许自动合并。
- `review_threshold <= score < auto_threshold`：标记为 `review`，进入
   `result.review_queue`，**绝不自动聚类**，只保留为人工复核候选。
- `score < review_threshold`：标记为 `reject`，不参与合并。

复核区的含义是“证据不够强、但又没弱到可以直接丢弃”。库里不会因为两条边都
落在复核区就把它们传递合并：A~B、B~C 相似不代表 A 与 C 可以无条件合并，
只有真正被自动边或 must-link 连通的记录才会进入同一个 cluster。

型号采用严格给分：归一化后精确匹配为 1.0，同族前缀（如 `X200`/`X200T`）
为 0.6，泛包含为 0.35，纯字符序列距离上限 0.5。因此同名但型号不同的记录
通常无法越过自动阈值，会停留在复核区或被拒绝，避免过度归并。

## 约束与冲突链

- `Constraint("must_link", id_a, id_b)`：强制合并，不受分块限制，优先于普通
  候选边处理。
- `Constraint("cannot_link", id_a, id_b)`：硬性禁止合并。
- 当一次合并会让某个 cannot-link 的两端落入同一并查集组件时，合并被拒绝，
  对应边标记为 `blocked`，并在 `result.conflicts` 中产出一条 `Conflict`：
  包含被拒 pair、命中的约束 pair、确定性规则名 `cannot_link_overrides`，以及
  由被拒边、cannot-link 和已接受证据路径组成的完整冲突链。
- 直接冲突（同一对既是 must-link 又是 cannot-link）时 cannot-link 优先。
- 所有处理顺序按分数降序、代价升序、ID 字典序确定，并查集根节点也按字典序
  稳定，因此相同输入无论排列如何，结果完全一致。

## 输出结构

`Resolution` 包含四部分：

- `clusters`：union-find 连通组件，每个 cluster 带有成员 ID 和全部证据边。
- `edges`：全量候选边，含 `score`、`cost`、`decision`、`block_keys`、
  分量明细 `components`、采用依据 `basis` 和最佳分量 `best_component`。
- `conflicts`：被 cannot-link 拦截的合并及冲突链。
- `review_queue`：灰区待人工复核的边。
- `cluster_by_record`：记录 ID 到 cluster ID 的映射，方便单点查询。

## 测试

```bash
python -m unittest
```

测试覆盖：拼写差异、空名称、同名不同型号、链式相似传递、must/cannot 约束
冲突、分块失效、多 blocker 召回、输入顺序不变性、灰区不自动聚类、cluster
可解释性以及非法输入校验。
