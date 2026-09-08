"""Zero-dependency text normalization and matching primitives."""

from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")


def normalize(value: str) -> str:
    return " ".join(TOKEN_RE.findall(value.casefold().replace("_", " ")))


def contains_normalized(haystack: str, needle: str) -> bool:
    if not needle or not haystack:
        return False
    if re.search(r"[\u3400-\u9fff]", needle):
        return needle.replace(" ", "") in haystack.replace(" ", "")
    return f" {needle} " in f" {haystack} "


def contains(surface: str, term: str) -> bool:
    return contains_normalized(normalize(surface), normalize(term))


def token_overlap(left: str, right: str) -> float:
    left_tokens = set(normalize(left).split())
    right_tokens = set(normalize(right).split())
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))


def readme_match(lines: list[tuple[int, str, str]], term: str) -> tuple[str, int] | None:
    needle = normalize(term)
    for index, line, normalized in lines:
        if contains_normalized(normalized, needle):
            text = " ".join(line.strip().split())[:220]
            if text:
                return text, index
    return None


def normalized_lines(text: str) -> list[tuple[int, str, str]]:
    return [
        (index, line, normalize(line))
        for index, line in enumerate(text.splitlines(), 1)
    ]
