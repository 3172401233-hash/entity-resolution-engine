from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[_\-/.,()\[\]{}]+")


def normalize_text(text: str | None) -> str:
    """归一化文本：小写、去首尾空白、标点转空格、压缩连续空白。"""
    if text is None:
        return ""
    normalized = text.strip().lower()
    normalized = _PUNCTUATION_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


@dataclass(frozen=True)
class Record:
    """一条待归并记录。

    id 为调用方提供的稳定 ID；metrics 为可选数值字段，键名在两组记录间共用。
    group 用于标识记录来自哪一组清单，分块默认只在不同组之间生成候选对。
    """

    id: str
    name: str = ""
    model: str = ""
    group: str | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
