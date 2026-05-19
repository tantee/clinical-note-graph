"""RAG (retrieval-augmented Q&A) + patient-search service.

This module hosts:
- parse_cited_indices(markdown) — extracts the set of [N] indices the LLM
  referenced in its answer.
- build_citations(chunks, answer) — pairs each retrieved chunk with a 1-based
  index and a `cited` flag indicating whether the LLM referenced it.
- ask(req) — top-level RAG orchestrator (added in Task 3).
- search_patients(q, limit) — patient-search orchestrator (added in Task 3).
"""
from __future__ import annotations

import re
from typing import Any

from app.schemas.rag import RagCitation


def parse_cited_indices(markdown: str) -> set[int]:
    """Return the set of [N] indices referenced in the LLM answer.

    Only matches contiguous digit runs inside square brackets. '[abc]' and
    '[12.5]' are NOT cited; '[1]' and '[42]' ARE.
    """
    return {int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", markdown or "")}


def build_citations(chunks: list[dict[str, Any]], answer: str) -> list[RagCitation]:
    """Pair each retrieved chunk with a 1-based citation number and a `cited`
    flag indicating whether the LLM's answer references it via [N]."""
    cited = parse_cited_indices(answer)
    out: list[RagCitation] = []
    for i, c in enumerate(chunks):
        n = i + 1
        content = (c.get("content") or "")
        out.append(RagCitation(
            n=n,
            refType=str(c.get("ref_type") or ""),
            refId=str(c.get("ref_id") or ""),
            content=content[:300],
            score=float(c.get("similarity") or 0.0),
            cited=(n in cited),
        ))
    return out
