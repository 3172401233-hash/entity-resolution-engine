from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Mapping

from .records import Record, normalize_text

DEFAULT_WEIGHTS: Mapping[str, float] = {"name": 0.45, "model": 0.40}


@dataclass(frozen=True)
class ComponentScore:
    """单个相似度分量的打分明细。"""

    key: str
    score: float
    detail: str
    weight: float


def _tokens(text: str) -> list[str]:
    return [token for token in normalize_text(text).split() if token]


def _weighted_jaccard(tokens_a: list[str], tokens_b: list[str]) -> float:
    set_a, set_b = set(tokens_a), set(tokens_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def name_similarity(name_a: str, name_b: str) -> float:
    """名称相似度：字符序列相似度为主、token Jaccard 为辅。

    两个名称都为空时返回 0（缺失信息不能当作相似证据），
    单边为空时同样返回 0，避免空名称把无关机器并到一起。
    """
    compact_a = normalize_text(name_a).replace(" ", "")
    compact_b = normalize_text(name_b).replace(" ", "")
    if not compact_a or not compact_b:
        return 0.0
    sequence_ratio = SequenceMatcher(None, compact_a, compact_b).ratio()
    token_ratio = _weighted_jaccard(_tokens(name_a), _tokens(name_b))
    return max(0.0, min(1.0, 0.6 * sequence_ratio + 0.4 * token_ratio))


def model_similarity(model_a: str, model_b: str) -> float:
    """型号相似度：归一化精确匹配最高，同族前缀次之，泛包含和字符距离严格给分。"""
    compact_a = normalize_text(model_a).replace(" ", "")
    compact_b = normalize_text(model_b).replace(" ", "")
    if not compact_a or not compact_b:
        return 0.0
    if compact_a == compact_b:
        return 1.0
    if compact_a.startswith(compact_b) or compact_b.startswith(compact_a):
        return 0.6
    if compact_a in compact_b or compact_b in compact_a:
        return 0.35
    return min(0.5, SequenceMatcher(None, compact_a, compact_b).ratio())


def numeric_similarity(value_a: float, value_b: float, scale: float) -> float:
    """数值距离相似度：差距相对 scale 线性衰减，超过 scale 记 0。"""
    if scale <= 0:
        raise ValueError("numeric scale 必须为正数")
    return max(0.0, 1.0 - abs(value_a - value_b) / scale)


def score_pair(
    record_a: Record,
    record_b: Record,
    weights: Mapping[str, float],
    numeric_scales: Mapping[str, float],
) -> tuple[float, float, tuple[ComponentScore, ...]]:
    """计算一对记录的综合相似度、匹配代价和分量明细。

    缺失的分量直接剔除，剩余权重重新归一化，保证缺字段不会拉低或抬高总分。
    """
    components: list[ComponentScore] = []

    name_a = normalize_text(record_a.name)
    name_b = normalize_text(record_b.name)
    if name_a or name_b:
        name_score = name_similarity(record_a.name, record_b.name)
        components.append(
            ComponentScore(
                key="name",
                score=name_score,
                detail=f"name_similarity={name_score:.3f}",
                weight=float(weights.get("name", DEFAULT_WEIGHTS["name"])),
            )
        )

    model_a = normalize_text(record_a.model)
    model_b = normalize_text(record_b.model)
    if model_a or model_b:
        model_score = model_similarity(record_a.model, record_b.model)
        components.append(
            ComponentScore(
                key="model",
                score=model_score,
                detail=f"model_similarity={model_score:.3f}",
                weight=float(weights.get("model", DEFAULT_WEIGHTS["model"])),
            )
        )

    metric_names = sorted(set(record_a.metrics) | set(record_b.metrics))
    for metric in metric_names:
        if metric not in record_a.metrics or metric not in record_b.metrics:
            continue
        if metric not in numeric_scales:
            raise ValueError(f"数值字段 {metric!r} 缺少 numeric_scales 配置")
        scale = float(numeric_scales[metric])
        metric_score = numeric_similarity(
            float(record_a.metrics[metric]),
            float(record_b.metrics[metric]),
            scale,
        )
        components.append(
            ComponentScore(
                key=f"metric:{metric}",
                score=metric_score,
                detail=(
                    f"numeric_distance="
                    f"{abs(float(record_a.metrics[metric]) - float(record_b.metrics[metric])):.3f}"
                    f" scale={scale:.3f}"
                ),
                weight=float(weights.get(metric, 0.15)),
            )
        )

    total_weight = sum(component.weight for component in components)
    if total_weight <= 0:
        score = 0.0
    else:
        score = (
            sum(component.score * component.weight for component in components)
            / total_weight
        )
    score = round(max(0.0, min(1.0, score)), 6)
    return score, round(1.0 - score, 6), tuple(components)
