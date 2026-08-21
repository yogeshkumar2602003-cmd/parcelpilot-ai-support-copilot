"""Lightweight, inspectable BM25 retrieval over the document_chunks table.

Why BM25 instead of a vector database: the entire corpus is six short
documents (~20 chunks total). A vector DB would add deployment complexity
(embedding calls, an external index, latency) with no meaningful recall
benefit at this scale, and would make relevance harder to audit. BM25 is
deterministic, needs no external service or API key, and its term-overlap
scoring is trivial to explain in an evidence chip. See docs/ARCHITECTURE.md.

Access control is enforced HERE, in the retrieval function itself, not
just filtered afterwards by the caller -- a customer principal's query
never even scores chunks outside their permitted scope.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app.domain.models import Principal

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


@dataclass
class ChunkResult:
    chunk_id: str
    file_name: str
    doc_type: str
    status: str
    authority_category: str
    effective_date: str | None
    updated_date: str | None
    customer_scope: str | None
    section: str | None
    page: int
    text: str
    score: float


class DocumentIndex:
    def __init__(self, rows: list[sqlite3.Row]):
        self.rows = rows
        self._corpus_tokens = [tokenize(r["text"]) for r in rows]
        self._bm25 = BM25Okapi(self._corpus_tokens) if rows else None

    def _accessible_row_indices(self, principal: Principal, doc_type: str | None, include_historical: bool) -> set[int]:
        idxs = set()
        for i, row in enumerate(self.rows):
            if doc_type and row["doc_type"] != doc_type:
                continue
            if row["status"] == "historical_only":
                if not (principal.is_internal() and include_historical):
                    continue
            scope = row["customer_scope"]
            if scope is not None:
                if principal.is_internal():
                    pass  # internal users may read any account's agreement
                elif scope != principal.account_id:
                    continue
            idxs.add(i)
        return idxs

    def search(
        self, principal: Principal, query: str, doc_type: str | None = None,
        include_historical: bool = False, top_k: int = 5,
    ) -> list[ChunkResult]:
        if not self.rows or self._bm25 is None:
            return []
        allowed = self._accessible_row_indices(principal, doc_type, include_historical)
        if not allowed:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            scores = [0.0] * len(self.rows)
        else:
            scores = self._bm25.get_scores(query_tokens)
        ranked = sorted(
            (i for i in allowed), key=lambda i: scores[i], reverse=True,
        )
        results = []
        for i in ranked[: top_k * 3]:
            if scores[i] <= 0 and len(results) >= 1:
                # Keep at least one hit even at score 0 for tiny/no-keyword-overlap
                # queries, but don't pad further with irrelevant zero-score chunks.
                continue
            row = self.rows[i]
            results.append(
                ChunkResult(
                    chunk_id=row["chunk_id"], file_name=row["file_name"], doc_type=row["doc_type"],
                    status=row["status"], authority_category=row["authority_category"],
                    effective_date=row["effective_date"], updated_date=row["updated_date"],
                    customer_scope=row["customer_scope"], section=row["section"], page=row["page"],
                    text=row["text"], score=float(scores[i]),
                )
            )
            if len(results) >= top_k:
                break
        return results


_index: DocumentIndex | None = None


def build_index(conn: sqlite3.Connection) -> DocumentIndex:
    global _index
    rows = conn.execute("SELECT * FROM document_chunks").fetchall()
    _index = DocumentIndex(rows)
    return _index


def get_index() -> DocumentIndex:
    if _index is None:
        raise RuntimeError("Document index has not been built yet. Run ingestion first.")
    return _index
