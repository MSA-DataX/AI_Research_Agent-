from __future__ import annotations

import math
from typing import Iterable

from config import EMBEDDING_MODEL
from llm_client import client


def embed(text: str) -> list[float] | None:
    if not text or not text.strip():
        return None
    try:
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=[text[:4000]])
        return list(resp.data[0].embedding)
    except Exception:
        return None


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    av = list(a)
    bv = list(b)
    if not av or not bv or len(av) != len(bv):
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv))
    na = math.sqrt(sum(x * x for x in av))
    nb = math.sqrt(sum(y * y for y in bv))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
