from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
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


MAX_CONCEPT_ALIASES = 4


@dataclass(slots=True)
class Concept:
    term: str
    weight: float = 1.0
    aliases: list[str] = field(default_factory=list)

    def terms(self) -> list[str]:
        """Unique surface term plus aliases; one group is one scoring identity."""
        values: list[str] = []
        seen: set[str] = set()
        for raw in [self.term, *self.aliases]:
            term = str(raw).strip()
            if not term:
                continue
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(term)
        return values

    @classmethod
    def from_value(cls, value: Any) -> "Concept":
        if isinstance(value, str):
            term = value.strip()
            if not term or len(term) > 160 or "\n" in term or "\r" in term:
                raise ContractError("concept terms must be non-empty single-line strings up to 160 characters")
            return cls(term)
        if not isinstance(value, dict) or not str(value.get("term", "")).strip():
            raise ContractError("concepts must be strings or objects containing term")
        term = str(value["term"]).strip()
        if len(term) > 160 or "\n" in term or "\r" in term:
            raise ContractError("concept terms must be single-line strings up to 160 characters")
        weight = float(value.get("weight", 1.0))
        if not 0 <= weight <= 1:
            raise ContractError("concept weight must be from 0 to 1")
        aliases_raw = value.get("aliases", [])
        if aliases_raw is None:
            aliases_raw = []
        if not isinstance(aliases_raw, list):
            raise ContractError("concept aliases must be an array of strings")
        aliases: list[str] = []
        for item in aliases_raw:
            if (
                not isinstance(item, str) or not item.strip() or len(item.strip()) > 160
                or "\n" in item or "\r" in item
            ):
                raise ContractError("concept aliases must be single-line strings up to 160 characters")
            aliases.append(item.strip())
        if len(aliases) > MAX_CONCEPT_ALIASES:
            raise ContractError(f"each concept may have at most {MAX_CONCEPT_ALIASES} aliases")
        return cls(term, weight, aliases)


@dataclass(slots=True)
class SearchRequest:
    request: str
    problem_concepts: list[Concept]
    mechanisms: list[Concept] = field(default_factory=list)
    exploration_directions: list[Concept] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    exclusions: list[str] = field(default_factory=list)
    exploration_level: float = 0.35
    legacy_schema: bool = field(default=False, repr=False)

    @property
    def core_concepts(self) -> list[Concept]:
        """v0.3 compatibility view used by the unchanged selection formula."""
        return self.problem_concepts + self.mechanisms

    @property
    def adjacent_concepts(self) -> list[Concept]:
        """v0.3 compatibility view used by the unchanged adjacent lane."""
        return self.exploration_directions

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchRequest":
        if not isinstance(data, dict) or not str(data.get("request", "")).strip():
            raise ContractError("request is required")
        new_fields = any(
            name in data for name in ("problem_concepts", "mechanisms", "exploration_directions")
        )
        problem_values = data.get("problem_concepts", data.get("core_concepts", []))
        mechanism_values = data.get("mechanisms", [])
        direction_values = data.get("exploration_directions", data.get("adjacent_concepts", []))
        if new_fields and "problem_concepts" not in data and "core_concepts" in data:
            problem_values = data["core_concepts"]
        for name, values in (
            ("problem_concepts", problem_values),
            ("mechanisms", mechanism_values),
            ("exploration_directions", direction_values),
        ):
            if not isinstance(values, list):
                raise ContractError(f"{name} must be an array")
        problem = [Concept.from_value(v) for v in problem_values]
        if not problem:
            name = "problem_concepts" if new_fields else "core_concepts"
            raise ContractError(f"{name} must contain at least one concept")
        exploration = float(data.get("exploration_level", 0.35))
        if not 0 <= exploration <= 1:
            raise ContractError("exploration_level must be from 0 to 1")
        raw_constraints = data.get("constraints", {})
        if not isinstance(raw_constraints, dict):
            raise ContractError("constraints must be an object")
        constraints = dict(raw_constraints)
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
            problem_concepts=problem,
            mechanisms=[Concept.from_value(v) for v in mechanism_values],
            exploration_directions=[Concept.from_value(v) for v in direction_values],
            artifact_types=[str(v).strip().lower() for v in data.get("artifact_types", []) if str(v).strip()],
            constraints=constraints,
            exclusions=[str(v).strip() for v in data.get("exclusions", []) if str(v).strip()],
            exploration_level=exploration,
            legacy_schema=not new_fields,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("legacy_schema", None)
        return payload

    def fingerprint(self, mode: str) -> str:
        payload = {"mode": mode, "request": self.to_dict()}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class SearchBoundary:
    recalled_mechanisms: list[str] = field(default_factory=list)
    presented_mechanisms: list[str] = field(default_factory=list)
    mechanism_origins: dict[str, list[str]] = field(default_factory=dict)
    explored_directions: list[str] = field(default_factory=list)
    unexplored_directions: list[str] = field(default_factory=list)
    rejected_directions: list[str] = field(default_factory=list)
    discovered_terms: list[str] = field(default_factory=list)
    negative_directions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BoundaryDelta:
    new_mechanisms: list[str] = field(default_factory=list)
    new_presented_mechanisms: list[str] = field(default_factory=list)
    new_directions: list[str] = field(default_factory=list)
    new_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Refinement:
    concepts: list[str] = field(default_factory=list)
    adjacent_concepts: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    rejected_directions: list[str] = field(default_factory=list)

    @staticmethod
    def _strings(data: dict[str, Any], name: str, limit: int) -> list[str]:
        value = data.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ContractError(f"refinement.{name} must be an array of strings")
        result = [item.strip() for item in value if item.strip()]
        if len(result) > limit:
            raise ContractError(f"refinement.{name} cannot contain more than {limit} items")
        if any(len(item) > 160 or "\n" in item or "\r" in item for item in result):
            raise ContractError(f"refinement.{name} contains an invalid value")
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Refinement":
        if not isinstance(data, dict):
            raise ContractError("refinement must be an object")
        result = cls(
            concepts=cls._strings(data, "concepts", 10),
            adjacent_concepts=cls._strings(data, "adjacent_concepts", 10),
            anchors=cls._strings(data, "anchors", 10),
            seeds=cls._strings(data, "seeds", 8),
            filenames=cls._strings(data, "filenames", 5),
            exclude=cls._strings(data, "exclude", 10),
            rejected_directions=cls._strings(data, "rejected_directions", 10),
        )
        repo_pattern = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
        if any(not repo_pattern.fullmatch(seed) for seed in result.seeds):
            raise ContractError("refinement.seeds must use owner/repo names")
        filename_pattern = re.compile(r"[A-Za-z0-9_.+@-]{1,100}")
        if any(not filename_pattern.fullmatch(filename) or filename in {".", ".."} for filename in result.filenames):
            raise ContractError("refinement.filenames must contain safe basenames")
        return result

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ITERATION_STRATEGIES = ("keyword", "relationship", "seed", "code", "owner")
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_QUERIES_PER_ITERATION = 6
DEFAULT_SESSION_QUERY_BUDGET = 30
DEFAULT_README_ENRICH_PER_ITERATION = 15


@dataclass(slots=True)
class ExplorationAddition:
    term: str
    reason: str = ""
    evidence: str = ""
    source_iteration: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"term": self.term}
        if self.reason:
            payload["reason"] = self.reason
        if self.evidence:
            payload["evidence"] = self.evidence
        if self.source_iteration is not None:
            payload["source_iteration"] = self.source_iteration
        return payload

    @classmethod
    def from_value(cls, value: Any) -> "ExplorationAddition":
        if isinstance(value, str):
            term = value.strip()
            if not term or len(term) > 160 or "\n" in term or "\r" in term:
                raise ContractError("add_exploration_directions terms must be single-line strings up to 160 characters")
            return cls(term)
        if not isinstance(value, dict) or not str(value.get("term", "")).strip():
            raise ContractError("add_exploration_directions must be strings or objects containing term")
        term = str(value["term"]).strip()
        if len(term) > 160 or "\n" in term or "\r" in term:
            raise ContractError("add_exploration_directions terms must be single-line strings up to 160 characters")
        reason = str(value.get("reason") or "").strip()
        evidence = str(value.get("evidence") or "").strip()
        if len(reason) > 400 or len(evidence) > 160:
            raise ContractError("add_exploration_directions reason/evidence is too long")
        source = value.get("source_iteration")
        if source is not None:
            try:
                source = int(source)
            except (TypeError, ValueError) as exc:
                raise ContractError("add_exploration_directions source_iteration must be an integer") from exc
        return cls(term, reason, evidence, source)


@dataclass(slots=True)
class SearchHypothesis:
    decision: str
    target_direction: str = ""
    target_mechanism: str = ""
    concepts: list[str] = field(default_factory=list)
    adjacent_concepts: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    negative_directions: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    rejected_directions: list[str] = field(default_factory=list)
    reason: str = ""
    stop_reason: str = ""
    remaining_unexplored_directions: list[str] = field(default_factory=list)
    add_exploration_directions: list[ExplorationAddition] = field(default_factory=list)
    strategies: list[str] = field(default_factory=list)
    promote_discovered_terms: list[str] = field(default_factory=list)

    @staticmethod
    def _strings(data: dict[str, Any], name: str, limit: int) -> list[str]:
        return Refinement._strings(data, name, limit)

    @staticmethod
    def _optional_text(data: dict[str, Any], name: str, limit: int) -> str:
        if name not in data or data[name] is None:
            return ""
        value = data[name]
        if not isinstance(value, str):
            raise ContractError(f"hypothesis.{name} must be a string")
        text = value.strip()
        if "\n" in text or "\r" in text or len(text) > limit:
            raise ContractError(f"hypothesis.{name} must be a single-line string up to {limit} characters")
        return text

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchHypothesis":
        if not isinstance(data, dict):
            raise ContractError("iteration hypothesis must be an object")
        decision = str(data.get("decision") or "").strip().casefold()
        if decision not in {"continue", "stop"}:
            raise ContractError("hypothesis.decision must be continue or stop")
        additions_raw = data.get("add_exploration_directions", [])
        if additions_raw is None:
            additions_raw = []
        if not isinstance(additions_raw, list):
            raise ContractError("hypothesis.add_exploration_directions must be an array")
        if len(additions_raw) > 5:
            raise ContractError("hypothesis.add_exploration_directions cannot contain more than 5 items")
        strategies_raw = data.get("strategies", [])
        if strategies_raw is None:
            strategies_raw = []
        if not isinstance(strategies_raw, list) or any(not isinstance(item, str) for item in strategies_raw):
            raise ContractError("hypothesis.strategies must be an array of strings")
        strategies = []
        for item in strategies_raw:
            name = item.strip().casefold()
            if not name:
                continue
            if name not in ITERATION_STRATEGIES:
                raise ContractError(
                    "hypothesis.strategies must be keyword, relationship, seed, code, or owner"
                )
            if name not in strategies:
                strategies.append(name)
        result = cls(
            decision=decision,
            target_direction=cls._optional_text(data, "target_direction", 160),
            target_mechanism=cls._optional_text(data, "target_mechanism", 160),
            concepts=cls._strings(data, "concepts", 10),
            adjacent_concepts=cls._strings(data, "adjacent_concepts", 10),
            aliases=cls._strings(data, "aliases", 8),
            negative_directions=cls._strings(data, "negative_directions", 12),
            anchors=cls._strings(data, "anchors", 10),
            seeds=cls._strings(data, "seeds", 8),
            filenames=cls._strings(data, "filenames", 5),
            exclude=cls._strings(data, "exclude", 10),
            rejected_directions=cls._strings(data, "rejected_directions", 10),
            reason=cls._optional_text(data, "reason", 500),
            stop_reason=cls._optional_text(data, "stop_reason", 200),
            remaining_unexplored_directions=cls._strings(data, "remaining_unexplored_directions", 10),
            add_exploration_directions=[ExplorationAddition.from_value(item) for item in additions_raw],
            strategies=strategies,
            promote_discovered_terms=cls._strings(data, "promote_discovered_terms", 5),
        )
        repo_pattern = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
        if any(not repo_pattern.fullmatch(seed) for seed in result.seeds):
            raise ContractError("hypothesis.seeds must use owner/repo names")
        filename_pattern = re.compile(r"[A-Za-z0-9_.+@-]{1,100}")
        if any(not filename_pattern.fullmatch(filename) or filename in {".", ".."} for filename in result.filenames):
            raise ContractError("hypothesis.filenames must contain safe basenames")
        if result.decision == "stop" and not result.stop_reason:
            raise ContractError("stop requires hypothesis.stop_reason")
        if result.decision == "continue" and not result.search_terms() and not result.strategies:
            raise ContractError("continue requires a search hypothesis")
        return result

    @classmethod
    def from_refinement(cls, refinement: Refinement) -> "SearchHypothesis":
        return cls(
            decision="continue",
            concepts=list(refinement.concepts),
            adjacent_concepts=list(refinement.adjacent_concepts),
            anchors=list(refinement.anchors),
            seeds=list(refinement.seeds),
            filenames=list(refinement.filenames),
            exclude=list(refinement.exclude),
            rejected_directions=list(refinement.rejected_directions),
            strategies=["keyword", "relationship", "code"],
            reason="compat expand",
        )

    def search_terms(self) -> list[str]:
        values = [
            self.target_direction, self.target_mechanism,
            *self.concepts, *self.adjacent_concepts, *self.aliases,
            *self.promote_discovered_terms,
            *(item.term for item in self.add_exploration_directions),
            *self.anchors, *self.seeds, *self.filenames,
        ]
        return [value for value in values if value.strip()]

    def resolved_strategies(self) -> list[str]:
        strategies = list(self.strategies) or (["keyword"] if self.decision == "continue" else [])
        if self.filenames and "code" not in strategies:
            strategies.append("code")
        if self.seeds and "relationship" not in strategies and "seed" not in strategies:
            strategies.append("seed")
        return strategies

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["add_exploration_directions"] = [item.to_dict() for item in self.add_exploration_directions]
        return payload

    def to_refinement(self) -> Refinement:
        return Refinement(
            concepts=list(self.concepts),
            adjacent_concepts=list(self.adjacent_concepts),
            anchors=list(self.anchors),
            seeds=list(self.seeds),
            filenames=list(self.filenames),
            exclude=list(self.exclude),
            rejected_directions=list(self.rejected_directions),
        )


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
    def from_dict(cls, data: dict[str, Any], evidence: set[str] | dict[str, str]) -> "Assessment":
        repo = str(data.get("repo", "")).strip().lower()
        if "/" not in repo:
            raise ContractError("assessment repo must be owner/name")
        difficulty = str(data.get("difficulty", "unknown")).lower()
        if difficulty not in {"easy", "medium", "hard", "unknown"}:
            raise ContractError("difficulty must be easy, medium, hard, or unknown")
        reasons = list(data.get("reasons", []))
        risks = list(data.get("risks", []))
        evidence_ids = set(evidence)
        for item in reasons + risks:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                raise ContractError("each reason/risk needs text")
            cited = item.get("evidence_ids", [])
            if not cited:
                raise ContractError("each reason/risk must cite at least one evidence id")
            unknown = set(map(str, cited)) - evidence_ids
            if unknown:
                raise ContractError(f"unknown evidence ids for {repo}: {sorted(unknown)}")
        use_case = str(data.get("use_case", "unknown")).strip() or "unknown"
        if use_case.casefold() != "unknown" and isinstance(evidence, dict):
            cited_reason_ids = {
                str(evidence_id) for item in reasons for evidence_id in item.get("evidence_ids", [])
            }
            if not any(evidence.get(evidence_id) == "readme_excerpt" for evidence_id in cited_reason_ids):
                raise ContractError(
                    f"verified use_case for {repo} must cite at least one readme excerpt"
                )
        return cls(
            repo=repo,
            relevance=_score(data.get("relevance"), "relevance"),
            uniqueness=_score(data.get("uniqueness"), "uniqueness"),
            usability=_score(data.get("usability"), "usability"),
            difficulty=difficulty,
            use_case=use_case,
            category=str(data.get("category", "uncategorized")).strip() or "uncategorized",
            artifact_type=str(data.get("artifact_type", "unknown")).strip().lower() or "unknown",
            reasons=reasons,
            risks=risks,
        )


def repo_key(repo: dict[str, Any]) -> str:
    return str(repo.get("full_name", "")).lower()
