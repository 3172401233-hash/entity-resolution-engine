"""纯 Python 实体归并库：分块、相似度打分、约束聚类与可解释输出。"""

from .blocking import (
    BlockingEngine,
    model_blocker,
    name_prefix_blocker,
    name_token_blocker,
)
from .clustering import Cluster, Conflict, Edge, MergeEngine, Resolution
from .records import Record, normalize_text
from .resolver import EntityResolver
from .resolver import Constraint
from .similarity import ComponentScore, score_pair

__all__ = [
    "Block",
    "BlockingEngine",
    "Cluster",
    "ComponentScore",
    "Constraint",
    "Conflict",
    "Edge",
    "EntityResolver",
    "MergeEngine",
    "Record",
    "Resolution",
    "model_blocker",
    "name_prefix_blocker",
    "name_token_blocker",
    "normalize_text",
    "score_pair",
]

Block = str
