"""Passage BM25 over README paragraphs already fetched for one candidate pool.

This is an experiment. The default scorer does not call it. k1, b, and the
positive IDF formula are fixed comparison settings, not a tuned optimum.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable

from .text import normalize


K1 = 1.2
B = 0.75
CHUNK_TOKENS = 256

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def positive_idf(documents: int, document_frequency: int) -> float:
    """Lucene-style IDF. The added 1 keeps the value above zero when a term is common."""
    if documents <= 0:
        return 0.0
    return math.log(1.0 + (documents - document_frequency + 0.5) / (document_frequency + 0.5))


def bm25_tokens(text: str) -> list[str]:
    """English stays on normalized tokens.

    CJK is the fixed experiment setting: every character is indexed, and so is
    each overlapping bigram. A two-character word therefore contributes both
    characters and one bigram. This is not bigrams alone, and it is not a
    tokenizer.
    """
    tokens: list[str] = []
    for token in normalize(text).split():
        if _CJK_RE.search(token):
            chars = [char for char in token if _CJK_RE.fullmatch(char)]
            tokens.extend(chars)
            tokens.extend(chars[index] + chars[index + 1] for index in range(len(chars) - 1))
        else:
            tokens.append(token)
    return tokens


def split_paragraphs(text: str) -> list[str]:
    """Split on blank lines and ATX headings. Fenced blocks stay inside the current paragraph."""
    paragraphs: list[str] = []
    buffer: list[str] = []
    fenced = False

    def flush() -> None:
        joined = "\n".join(buffer).strip()
        buffer.clear()
        if joined:
            paragraphs.append(joined)

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            buffer.append(line)
            continue
        if fenced:
            buffer.append(line)
            continue
        if _HEADING_RE.match(line):
            flush()
            buffer.append(line)
            continue
        if not line.strip():
            flush()
            continue
        buffer.append(line)
    flush()
    return paragraphs


def paragraph_chunks(text: str) -> list[list[str]]:
    """Token lists for each paragraph. A paragraph longer than 256 tokens is cut in order."""
    chunks: list[list[str]] = []
    for paragraph in split_paragraphs(text):
        tokens = bm25_tokens(paragraph)
        if not tokens:
            continue
        if len(tokens) <= CHUNK_TOKENS:
            chunks.append(tokens)
            continue
        for start in range(0, len(tokens), CHUNK_TOKENS):
            piece = tokens[start:start + CHUNK_TOKENS]
            if piece:
                chunks.append(piece)
    return chunks


def _unique(tokens: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


class ReadmeBM25:
    """BM25 over one pool. Each paragraph chunk is a document. Scores are not summed."""

    def __init__(self, candidates: Iterable[dict[str, Any]]) -> None:
        self._chunks: dict[int, list[list[str]]] = {}
        all_chunks: list[list[str]] = []
        for candidate in candidates:
            readme = candidate.get("readme")
            chunks = paragraph_chunks(readme) if isinstance(readme, str) and readme.strip() else []
            self._chunks[id(candidate)] = chunks
            all_chunks.extend(chunks)
        self.documents = len(all_chunks)
        self._df: Counter[str] = Counter()
        total = 0
        for chunk in all_chunks:
            total += len(chunk)
            self._df.update(set(chunk))
        self.avgdl = (total / self.documents) if self.documents else 0.0

    def paragraph_scores(self, candidate: dict[str, Any], term: str) -> list[float]:
        if self.documents <= 0 or self.avgdl <= 0:
            return []
        query = _unique(bm25_tokens(term))
        if not query:
            return []
        scores: list[float] = []
        for chunk in self._chunks.get(id(candidate), []):
            scores.append(self._score_chunk(query, chunk))
        return scores

    def best_raw(self, candidate: dict[str, Any], term: str) -> float:
        scores = self.paragraph_scores(candidate, term)
        return max(scores, default=0.0)

    def _score_chunk(self, query: list[str], chunk: list[str]) -> float:
        frequencies = Counter(chunk)
        length = len(chunk)
        normalizer = K1 * (1.0 - B + B * length / self.avgdl)
        total = 0.0
        for token in query:
            frequency = frequencies.get(token, 0)
            if frequency <= 0:
                continue
            idf = positive_idf(self.documents, self._df.get(token, 0))
            total += idf * (frequency * (K1 + 1.0)) / (frequency + normalizer)
        return total
