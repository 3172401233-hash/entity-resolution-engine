from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable

from .records import Record, normalize_text

BlockKey = str
Blocker = Callable[[Record], tuple[BlockKey, ...]]


def name_prefix_blocker(prefix_len: int = 4) -> Blocker:
    """名称去空格后的前缀分块，能挡住大部分拼写差异造成的候选膨胀。"""

    def block(record: Record) -> tuple[BlockKey, ...]:
        compact = normalize_text(record.name).replace(" ", "")
        if not compact:
            return ()
        return (f"name_prefix:{compact[:prefix_len]}",)

    block.name = f"name_prefix[{prefix_len}]"  # type: ignore[attr-defined]
    return block


def name_token_blocker(min_token_len: int = 2) -> Blocker:
    """名称 token 集合分块，对词序变化和额外后缀更宽容。"""

    def block(record: Record) -> tuple[BlockKey, ...]:
        tokens = normalize_text(record.name).split()
        keys = {
            f"name_token:{token}"
            for token in tokens
            if len(token) >= min_token_len
        }
        return tuple(sorted(keys))

    block.name = "name_token"  # type: ignore[attr-defined]
    return block


def model_blocker() -> Blocker:
    """型号归一化后精确分块。"""

    def block(record: Record) -> tuple[BlockKey, ...]:
        compact = normalize_text(record.model).replace(" ", "")
        if not compact:
            return ()
        return (f"model:{compact}",)

    block.name = "model"  # type: ignore[attr-defined]
    return block


class BlockingEngine:
    """按一组 blocker 给记录建倒排索引并生成候选对。

    任一 blocker 命中即成为候选，同时保留命中的 block key 作为解释依据；
    blocker 返回空元组的记录不会进入任何分块（用于表达“该字段缺失”）。
    """

    def __init__(self, blockers: Iterable[Blocker]):
        self.blockers = list(blockers)
        if not self.blockers:
            raise ValueError("至少需要一个 blocker")

    def keys_for(self, record: Record) -> list[BlockKey]:
        keys: list[BlockKey] = []
        for blocker in self.blockers:
            keys.extend(blocker(record))
        return keys

    def candidate_pairs(
        self,
        records_a: Iterable[Record],
        records_b: Iterable[Record],
        include_same_group: bool = False,
    ) -> tuple[set[tuple[str, str]], dict[tuple[str, str], tuple[BlockKey, ...]]]:
        records_b = list(records_b)
        inverted: dict[BlockKey, list[str]] = defaultdict(list)
        for record in records_b:
            for key in set(self.keys_for(record)):
                inverted[key].append(record.id)

        pairs: set[tuple[str, str]] = set()
        pair_keys: dict[tuple[str, str], set[str]] = defaultdict(set)
        for record in records_a:
            if not include_same_group:
                eligible = {
                    other.id
                    for other in records_b
                    if other.group != record.group
                }
            else:
                eligible = {other.id for other in records_b}
            for key in set(self.keys_for(record)):
                for other_id in inverted.get(key, ()):
                    if other_id == record.id or other_id not in eligible:
                        continue
                    pair = tuple(sorted((record.id, other_id)))
                    pairs.add(pair)
                    pair_keys[pair].add(key)

        matched_keys = {
            pair: tuple(sorted(keys)) for pair, keys in pair_keys.items()
        }
        return pairs, matched_keys
