from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


class ContractError(ValueError):
    """Raised when JSON supplied at an agent boundary is invalid."""


def _score(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must be a number from 0 to 100") from exc
    if not 0 <= result <= 100:
        raise ContractError(f"{name} must be from 0 to 100")
    return result


@dataclass(slots=True)
class Concept:
    term: str
    weight: float = 1.0

    @classmethod
    def from_value(cls, value: Any) -> "Concept":
        if isinstance(value, str):
            return cls(value.strip())
        if not isinstance(value, dict) or not str(value.get("term", "")).strip():
            raise ContractError("concepts must be strings or objects containing term")
        weight = float(value.get("weight", 1.0))
        if not 0 <= weight <= 1:
            raise ContractError("concept weight must be from 0 to 1")
        return cls(str(value["term"]).strip(), weight)


@dataclass(slots=True)
class SearchRequest:
    request: str
    core_concepts: list[Concept]
    adjacent_concepts: list[Concept] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    exclusions: list[str] = field(default_factory=list)
    exploration_level: float = 0.35

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchRequest":
        if not isinstance(data, dict) or not str(data.get("request", "")).strip():
            raise ContractError("request is required")
        core = [Concept.from_value(v) for v in data.get("core_concepts", [])]
        if not core:
            raise ContractError("core_concepts must contain at least one concept")
        exploration = float(data.get("exploration_level", 0.35))
        if not 0 <= exploration <= 1:
            raise ContractError("exploration_level must be from 0 to 1")
        constraints = data.get("constraints", {})
        if not isinstance(constraints, dict):
            raise ContractError("constraints must be an object")
        if constraints.get("pushed_after") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(constraints["pushed_after"])):
            raise ContractError("constraints.pushed_after must use YYYY-MM-DD")
        for key in ("min_stars", "max_stars"):
            if key in constraints:
                try:
                    constraints[key] = int(constraints[key])
                except (TypeError, ValueError) as exc:
                    raise ContractError(f"constraints.{key} must be a non-negative integer") from exc
                if constraints[key] < 0:
                    raise ContractError(f"constraints.{key} must be a non-negative integer")
        if constraints.get("min_stars", 0) > constraints.get("max_stars", float("inf")):
            raise ContractError("constraints.min_stars cannot exceed max_stars")
        return cls(
            request=str(data["request"]).strip(),
            core_concepts=core,
            adjacent_concepts=[Concept.from_value(v) for v in data.get("adjacent_concepts", [])],
            artifact_types=[str(v).strip().lower() for v in data.get("artifact_types", []) if str(v).strip()],
            constraints=dict(constraints),
            exclusions=[str(v).strip() for v in data.get("exclusions", []) if str(v).strip()],
            exploration_level=exploration,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Assessment:
    repo: str
    relevance: float
    uniqueness: float
    usability: float
    difficulty: str
    use_case: str
    category: str
    artifact_type: str
    reasons: list[dict[str, Any]] = field(default_factory=list)
    risks: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any], evidence_ids: set[str]) -> "Assessment":
        repo = str(data.get("repo", "")).strip().lower()
        if "/" not in repo:
            raise ContractError("assessment repo must be owner/name")
        difficulty = str(data.get("difficulty", "unknown")).lower()
        if difficulty not in {"easy", "medium", "hard", "unknown"}:
            raise ContractError("difficulty must be easy, medium, hard, or unknown")
        reasons = list(data.get("reasons", []))
        risks = list(data.get("risks", []))
        for item in reasons + risks:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                raise ContractError("each reason/risk needs text")
            cited = item.get("evidence_ids", [])
            if not cited:
                raise ContractError("each reason/risk must cite at least one evidence id")
            unknown = set(map(str, cited)) - evidence_ids
            if unknown:
                raise ContractError(f"unknown evidence ids for {repo}: {sorted(unknown)}")
        return cls(
            repo=repo,
            relevance=_score(data.get("relevance"), "relevance"),
            uniqueness=_score(data.get("uniqueness"), "uniqueness"),
            usability=_score(data.get("usability"), "usability"),
            difficulty=difficulty,
            use_case=str(data.get("use_case", "unknown")).strip() or "unknown",
            category=str(data.get("category", "uncategorized")).strip() or "uncategorized",
            artifact_type=str(data.get("artifact_type", "unknown")).strip().lower() or "unknown",
            reasons=reasons,
            risks=risks,
        )


def repo_key(repo: dict[str, Any]) -> str:
    return str(repo.get("full_name", "")).lower()
