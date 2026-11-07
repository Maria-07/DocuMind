"""Retrieval metrics (binary relevance unless grades are given). Pure functions, unit-tested."""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping, Sequence
from collections.abc import Set as AbstractSet


def precision_at_k(retrieved: Sequence[Hashable], relevant: AbstractSet[Hashable], k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    top = retrieved[:k]
    return sum(1 for r in top if r in relevant) / k


def recall_at_k(retrieved: Sequence[Hashable], relevant: AbstractSet[Hashable], k: int) -> float:
    if not relevant:
        return 1.0  # nothing to find (e.g. an unanswerable question)
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def reciprocal_rank(retrieved: Sequence[Hashable], relevant: AbstractSet[Hashable]) -> float:
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved: Sequence[Hashable],
    relevant: AbstractSet[Hashable] | Mapping[Hashable, float],
    k: int,
) -> float:
    """NDCG with graded relevance (mapping item → gain) or binary relevance (set)."""
    gains: Mapping[Hashable, float] = relevant if isinstance(relevant, Mapping) else dict.fromkeys(relevant, 1.0)
    if not gains:
        return 1.0
    dcg = sum(gains.get(item, 0.0) / math.log2(rank + 1) for rank, item in enumerate(retrieved[:k], start=1))
    ideal = sorted(gains.values(), reverse=True)[:k]
    idcg = sum(g / math.log2(rank + 1) for rank, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg > 0 else 0.0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
