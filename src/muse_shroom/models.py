from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any


class ContractError(ValueError):
    """Raised when JSON supplied at an agent boundary is invalid."""


SEARCH_REQUEST_FIELDS = frozenset({
    "request", "problem_concepts", "mechanisms", "exploration_directions",
    "artifact_types", "constraints", "exclusions", "exploration_level",
})
SEARCH_REQUEST_LEGACY_FIELDS = frozenset({"core_concepts", "adjacent_concepts"})
SEARCH_REQUEST_CONSTRAINT_FIELDS = frozenset({
    "language", "pushed_after", "include_archived", "min_stars", "max_stars",
})
CONCEPT_OBJECT_FIELDS = frozenset({"term", "weight", "aliases"})
EXPLORATION_ADDITION_FIELDS = frozenset({
    "term", "reason", "evidence", "source_iteration", "request_anchor",
})
HYPOTHESIS_FIELDS = frozenset({
    "decision", "target_direction", "target_mechanism", "concepts", "adjacent_concepts",
    "aliases", "negative_directions", "anchors", "seeds", "filenames", "exclude",
    "rejected_directions", "reason", "stop_reason", "remaining_unexplored_directions",
    "add_exploration_directions", "strategies", "promote_discovered_terms",
})
SELECTION_FIELDS = frozenset({
    "repo", "rationale", "mechanism_label", "source_term", "quote",
    "evidence_ids", "boundary_role",
})
RANK_PAYLOAD_FIELDS = frozenset({"selection"})
BOUNDARY_ROLES = ("anchor", "edge", "leap", "wildcard")
SEARCH_ARTIFACT_TYPES = (
    "application", "mcp", "skill", "mod", "plugin", "library",
)


def reject_unknown_fields(
    data: dict[str, Any],
    allowed: set[str],
    *,
    where: str,
    extra_hint: str = "",
) -> None:
    unknown = sorted(str(key) for key in data if key not in allowed)
    if not unknown:
        return
    message = (
        f"unknown {where} field(s): {', '.join(unknown)}. "
        f"Allowed fields: {', '.join(sorted(allowed))}."
    )
    if extra_hint:
        message = f"{message} {extra_hint}"
    raise ContractError(message)


def require_fields(
    data: dict[str, Any],
    required: tuple[str, ...],
    *,
    where: str,
    extra_hint: str = "",
) -> None:
    missing = [name for name in required if name not in data]
    if not missing:
        return
    message = f"missing required {where} field(s): {', '.join(missing)}."
    if extra_hint:
        message = f"{message} {extra_hint}"
    raise ContractError(message)


def _number(value: Any, name: str, *, strict: bool = False) -> float:
    if strict and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ContractError(f"{name} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must be a number") from exc


def _score(value: Any, name: str, *, strict: bool = False) -> float:
    result = _number(value, name, strict=strict)
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
    def from_value(cls, value: Any, *, strict: bool = False) -> "Concept":
        if isinstance(value, str):
            term = value.strip()
            if not term or len(term) > 160 or "\n" in term or "\r" in term:
                raise ContractError("concept terms must be non-empty single-line strings up to 160 characters")
            return cls(term)
        if not isinstance(value, dict):
            raise ContractError("concepts must be strings or objects containing term")
        if strict:
            reject_unknown_fields(value, CONCEPT_OBJECT_FIELDS, where="concept")
            if not isinstance(value.get("term"), str):
                raise ContractError("concept term must be a string")
        if not str(value.get("term", "")).strip():
            raise ContractError("concepts must be strings or objects containing term")
        term = str(value["term"]).strip()
        if len(term) > 160 or "\n" in term or "\r" in term:
            raise ContractError("concept terms must be single-line strings up to 160 characters")
        weight = _number(value.get("weight", 1.0), "concept weight", strict=strict)
        if not 0 <= weight <= 1:
            raise ContractError("concept weight must be from 0 to 1")
        aliases_raw = value.get("aliases", [])
        if aliases_raw is None:
            if strict:
                raise ContractError("concept aliases must be an array of strings")
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
    def from_dict(cls, data: dict[str, Any], *, strict: bool = False) -> "SearchRequest":
        if not isinstance(data, dict):
            raise ContractError("SearchRequest must be an object")
        if strict:
            reject_unknown_fields(
                data,
                SEARCH_REQUEST_FIELDS | SEARCH_REQUEST_LEGACY_FIELDS,
                where="SearchRequest",
                extra_hint=(
                    "v0.4 fields are request, problem_concepts, mechanisms, "
                    "exploration_directions, artifact_types, constraints, exclusions, "
                    "exploration_level. core_concepts and adjacent_concepts are deprecated "
                    "v0.3 compatibility aliases."
                ),
            )
            v04_present = any(name in data for name in (
                "problem_concepts", "mechanisms", "exploration_directions",
            ))
            legacy_present = any(name in data for name in SEARCH_REQUEST_LEGACY_FIELDS)
            if v04_present and legacy_present:
                mixed = ", ".join(sorted(name for name in SEARCH_REQUEST_LEGACY_FIELDS if name in data))
                raise ContractError(
                    f"legacy field(s) {mixed} cannot be combined with v0.4 fields; "
                    "use problem_concepts, mechanisms, and exploration_directions"
                )
        if strict and not isinstance(data.get("request"), str):
            raise ContractError("request must be a string")
        if not str(data.get("request", "")).strip():
            raise ContractError("request is required")
        new_fields = any(
            name in data for name in ("problem_concepts", "mechanisms", "exploration_directions")
        )
        problem_values = data.get("problem_concepts", data.get("core_concepts", []))
        mechanism_values = data.get("mechanisms", [])
        direction_values = data.get("exploration_directions", data.get("adjacent_concepts", []))
        if new_fields and "problem_concepts" not in data and "core_concepts" in data:
            problem_values = data["core_concepts"]
        if strict and not new_fields and "core_concepts" not in data:
            raise ContractError(
                "missing required SearchRequest field(s): problem_concepts. "
                "v0.4 requires problem_concepts (array of strings or "
                "{term, aliases, weight} objects). Deprecated v0.3 compatibility: "
                "core_concepts / adjacent_concepts."
            )
        for name, values in (
            ("problem_concepts", problem_values),
            ("mechanisms", mechanism_values),
            ("exploration_directions", direction_values),
        ):
            if not isinstance(values, list):
                raise ContractError(f"{name} must be an array")
        problem = [Concept.from_value(v, strict=strict) for v in problem_values]
        if not problem:
            name = "problem_concepts" if new_fields else "core_concepts"
            raise ContractError(f"{name} must contain at least one concept")
        exploration = _number(
            data.get("exploration_level", 0.35),
            "exploration_level",
            strict=strict,
        )
        if not 0 <= exploration <= 1:
            raise ContractError("exploration_level must be from 0 to 1")
        raw_constraints = data.get("constraints", {})
        if not isinstance(raw_constraints, dict):
            raise ContractError("constraints must be an object")
        if strict:
            reject_unknown_fields(
                raw_constraints, SEARCH_REQUEST_CONSTRAINT_FIELDS, where="constraints",
            )
        constraints = dict(raw_constraints)
        if strict:
            if "language" in constraints and not isinstance(constraints["language"], str):
                raise ContractError("constraints.language must be a string")
            if "include_archived" in constraints and not isinstance(constraints["include_archived"], bool):
                raise ContractError("constraints.include_archived must be a boolean")
            if "pushed_after" in constraints:
                pushed_after = constraints["pushed_after"]
                if not isinstance(pushed_after, str) or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", pushed_after,
                ):
                    raise ContractError("constraints.pushed_after must use YYYY-MM-DD")
        elif constraints.get("pushed_after") and not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", str(constraints["pushed_after"]),
        ):
            raise ContractError("constraints.pushed_after must use YYYY-MM-DD")
        for key in ("min_stars", "max_stars"):
            if key in constraints:
                if strict:
                    if isinstance(constraints[key], bool) or not isinstance(constraints[key], int):
                        raise ContractError(f"constraints.{key} must be a non-negative integer")
                else:
                    try:
                        constraints[key] = int(constraints[key])
                    except (TypeError, ValueError) as exc:
                        raise ContractError(f"constraints.{key} must be a non-negative integer") from exc
                if constraints[key] < 0:
                    raise ContractError(f"constraints.{key} must be a non-negative integer")
        if constraints.get("min_stars", 0) > constraints.get("max_stars", float("inf")):
            raise ContractError("constraints.min_stars cannot exceed max_stars")
        raw_artifact_types = data.get("artifact_types", [])
        raw_exclusions = data.get("exclusions", [])
        if strict:
            if not isinstance(raw_artifact_types, list) or any(
                not isinstance(item, str) for item in raw_artifact_types
            ):
                raise ContractError("artifact_types must be an array of strings")
            invalid_artifact_types = [
                item for item in raw_artifact_types if item not in SEARCH_ARTIFACT_TYPES
            ]
            if invalid_artifact_types:
                raise ContractError(
                    "artifact_types entries must be application, mcp, skill, mod, plugin, or library"
                )
            if not isinstance(raw_exclusions, list) or any(
                not isinstance(item, str) for item in raw_exclusions
            ):
                raise ContractError("exclusions must be an array of strings")
        return cls(
            request=str(data["request"]).strip(),
            problem_concepts=problem,
            mechanisms=[Concept.from_value(v, strict=strict) for v in mechanism_values],
            exploration_directions=[Concept.from_value(v, strict=strict) for v in direction_values],
            artifact_types=[str(v).strip().lower() for v in raw_artifact_types if str(v).strip()],
            constraints=constraints,
            exclusions=[str(v).strip() for v in raw_exclusions if str(v).strip()],
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
    discovered_term_evidence: list[dict[str, Any]] = field(default_factory=list)
    confirmation_queue: list[dict[str, Any]] = field(default_factory=list)
    mechanism_confirmations: list[dict[str, Any]] = field(default_factory=list)
    negative_directions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BoundaryDelta:
    new_mechanisms: list[str] = field(default_factory=list)
    new_mechanism_surfaces: list[str] = field(default_factory=list)
    new_presented_mechanisms: list[str] = field(default_factory=list)
    new_presented_mechanism_surfaces: list[str] = field(default_factory=list)
    mechanism_normalizations: list[dict[str, str]] = field(default_factory=list)
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
DEFAULT_QUICK_CANDIDATE_LIMIT = 100
DEFAULT_DEEP_CANDIDATE_LIMIT = 250
DEFAULT_CONSECUTIVE_NO_GAIN = 2
DEFAULT_CONFIRMATION_CANDIDATE_LIMIT = 2
DEFAULT_CONFIRMATION_CASE_LIMIT = 3
DEFAULT_CONFIRMATION_QUERY_LIMIT = 6
DEFAULT_CONFIRMATION_ENRICH_LIMIT = 8
DEFAULT_SEMANTIC_QUERY_BUDGET = 4
DEFAULT_SEMANTIC_HYPOTHESIS_LIMIT = 2
DEFAULT_SEMANTIC_CANDIDATE_CAP = 40
HOST_HYPOTHESIS_EVIDENCE = "host_hypothesis"
HARD_STOP_REASONS = (
    "agent_stop", "max_iterations", "query_budget_exhausted",
    "duplicate_queries", "consecutive_no_gain",
)


@dataclass(slots=True)
class ExplorationAddition:
    term: str
    reason: str = ""
    evidence: str = ""
    source_iteration: int | None = None
    request_anchor: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {"term": self.term}
        if self.reason:
            payload["reason"] = self.reason
        if self.evidence:
            payload["evidence"] = self.evidence
        if self.source_iteration is not None:
            payload["source_iteration"] = self.source_iteration
        if self.request_anchor:
            payload["request_anchor"] = self.request_anchor
        return payload

    @classmethod
    def from_value(cls, value: Any, *, strict: bool = False) -> "ExplorationAddition":
        if isinstance(value, str):
            term = value.strip()
            if not term or len(term) > 160 or "\n" in term or "\r" in term:
                raise ContractError("add_exploration_directions terms must be single-line strings up to 160 characters")
            return cls(term)
        if not isinstance(value, dict):
            raise ContractError("add_exploration_directions must be strings or objects containing term")
        if strict:
            reject_unknown_fields(
                value, EXPLORATION_ADDITION_FIELDS, where="add_exploration_directions item",
            )
            if not isinstance(value.get("term"), str):
                raise ContractError("add_exploration_directions term must be a string")
        if not str(value.get("term", "")).strip():
            raise ContractError("add_exploration_directions must be strings or objects containing term")
        term = str(value["term"]).strip()
        if len(term) > 160 or "\n" in term or "\r" in term:
            raise ContractError("add_exploration_directions terms must be single-line strings up to 160 characters")
        if strict:
            for name in ("reason", "evidence", "request_anchor"):
                if name in value and not isinstance(value[name], str):
                    raise ContractError(f"add_exploration_directions {name} must be a string")
        reason = str(value.get("reason") or "").strip()
        evidence = str(value.get("evidence") or "").strip()
        request_anchor = str(value.get("request_anchor") or "").strip()
        if len(reason) > 400 or len(evidence) > 160:
            raise ContractError("add_exploration_directions reason/evidence is too long")
        if len(request_anchor) > 160 or "\n" in request_anchor or "\r" in request_anchor:
            raise ContractError("add_exploration_directions request_anchor must be a single-line string up to 160 characters")
        source = value.get("source_iteration")
        if source is not None:
            if strict:
                if isinstance(source, bool) or not isinstance(source, int):
                    raise ContractError("add_exploration_directions source_iteration must be an integer")
            else:
                try:
                    source = int(source)
                except (TypeError, ValueError) as exc:
                    raise ContractError("add_exploration_directions source_iteration must be an integer") from exc
        elif strict and "source_iteration" in value:
            raise ContractError("add_exploration_directions source_iteration must be an integer")
        return cls(term, reason, evidence, source, request_anchor)


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
    def _optional_text(
        data: dict[str, Any], name: str, limit: int, *, strict: bool = False,
    ) -> str:
        if name not in data:
            return ""
        if data[name] is None:
            if strict:
                raise ContractError(f"hypothesis.{name} must be a string")
            return ""
        value = data[name]
        if not isinstance(value, str):
            raise ContractError(f"hypothesis.{name} must be a string")
        text = value.strip()
        if "\n" in text or "\r" in text or len(text) > limit:
            raise ContractError(f"hypothesis.{name} must be a single-line string up to {limit} characters")
        return text

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, strict: bool = False) -> "SearchHypothesis":
        if not isinstance(data, dict):
            raise ContractError("iteration hypothesis must be an object")
        if strict:
            reject_unknown_fields(
                data,
                HYPOTHESIS_FIELDS,
                where="SearchHypothesis",
                extra_hint=(
                    "decision must be continue or stop. Unofficial fields such as "
                    "mechanisms or rationale are rejected."
                ),
            )
        decision_raw = data.get("decision")
        if strict and not isinstance(decision_raw, str):
            raise ContractError("hypothesis.decision must be continue or stop")
        decision = str(decision_raw or "").strip()
        if not strict:
            decision = decision.casefold()
        if decision not in {"continue", "stop"}:
            raise ContractError("hypothesis.decision must be continue or stop")
        additions_raw = data.get("add_exploration_directions", [])
        if additions_raw is None:
            if strict:
                raise ContractError("hypothesis.add_exploration_directions must be an array")
            additions_raw = []
        if not isinstance(additions_raw, list):
            raise ContractError("hypothesis.add_exploration_directions must be an array")
        if len(additions_raw) > 5:
            raise ContractError("hypothesis.add_exploration_directions cannot contain more than 5 items")
        strategies_raw = data.get("strategies", [])
        if strategies_raw is None:
            if strict:
                raise ContractError("hypothesis.strategies must be an array of strings")
            strategies_raw = []
        if not isinstance(strategies_raw, list) or any(not isinstance(item, str) for item in strategies_raw):
            raise ContractError("hypothesis.strategies must be an array of strings")
        strategies = []
        for item in strategies_raw:
            name = item.strip()
            if not strict:
                name = name.casefold()
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
            target_direction=cls._optional_text(data, "target_direction", 160, strict=strict),
            target_mechanism=cls._optional_text(data, "target_mechanism", 160, strict=strict),
            concepts=cls._strings(data, "concepts", 10),
            adjacent_concepts=cls._strings(data, "adjacent_concepts", 10),
            aliases=cls._strings(data, "aliases", 8),
            negative_directions=cls._strings(data, "negative_directions", 12),
            anchors=cls._strings(data, "anchors", 10),
            seeds=cls._strings(data, "seeds", 8),
            filenames=cls._strings(data, "filenames", 5),
            exclude=cls._strings(data, "exclude", 10),
            rejected_directions=cls._strings(data, "rejected_directions", 10),
            reason=cls._optional_text(data, "reason", 500, strict=strict),
            # Matches `reason`: both now carry the round's rationale, including the
            # required cross-domain decision, so they get the same budget. A 200-char
            # cap discarded whole iterations over a few characters of explanation.
            stop_reason=cls._optional_text(data, "stop_reason", 500, strict=strict),
            remaining_unexplored_directions=cls._strings(data, "remaining_unexplored_directions", 10),
            add_exploration_directions=[
                ExplorationAddition.from_value(item, strict=strict) for item in additions_raw
            ],
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
class Selection:
    """One Agent-chosen repository in the exact order it should be displayed."""

    repo: str
    rationale: str
    mechanism_label: str
    source_term: str
    quote: str
    evidence_ids: list[str]
    boundary_role: str

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, strict: bool = False,
    ) -> "Selection":
        if not isinstance(data, dict):
            raise ContractError("each selection item must be an object")
        if strict:
            reject_unknown_fields(
                data, SELECTION_FIELDS, where="Selection",
            )
            require_fields(
                data, tuple(sorted(SELECTION_FIELDS)), where="Selection",
            )
        string_fields = (
            "repo", "rationale", "mechanism_label", "source_term", "quote", "boundary_role",
        )
        if strict and any(not isinstance(data.get(name), str) for name in string_fields):
            raise ContractError("selection text fields must be strings")
        repo = str(data.get("repo") or "").strip().lower()
        if "/" not in repo:
            raise ContractError("selection repo must be owner/name")
        text = {
            name: str(data.get(name) or "").strip()
            for name in ("rationale", "mechanism_label", "source_term", "quote")
        }
        if any(not value for value in text.values()):
            raise ContractError(
                "selection rationale, mechanism_label, source_term, and quote are required"
            )
        if any("\n" in value or "\r" in value for value in text.values()):
            raise ContractError("selection text fields must be single-line strings")
        if len(text["mechanism_label"]) > 160:
            raise ContractError("mechanism_label must be at most 160 characters")
        evidence_raw = data.get("evidence_ids")
        if not isinstance(evidence_raw, list) or not evidence_raw:
            raise ContractError("selection evidence_ids must be a non-empty array")
        if strict and any(not isinstance(value, str) for value in evidence_raw):
            raise ContractError("selection evidence_ids must contain strings")
        evidence_ids = [str(value).strip() for value in evidence_raw]
        if any(not value for value in evidence_ids):
            raise ContractError("selection evidence_ids must not contain empty values")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ContractError("selection evidence_ids must be unique")
        role = str(data.get("boundary_role") or "").strip().lower()
        if role not in BOUNDARY_ROLES:
            raise ContractError(
                "boundary_role must be anchor, edge, leap, or wildcard"
            )
        return cls(
            repo=repo,
            rationale=text["rationale"],
            mechanism_label=text["mechanism_label"],
            source_term=text["source_term"],
            quote=text["quote"],
            evidence_ids=evidence_ids,
            boundary_role=role,
        )


def repo_key(repo: dict[str, Any]) -> str:
    return str(repo.get("full_name", "")).lower()
