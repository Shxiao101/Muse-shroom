# Search request contract

Pass a JSON object to `muse-shroom search --request FILE --mode quick|deep`.

```json
{
  "request": "the user's original request",
  "problem_concepts": [
    {"term": "problem to solve", "aliases": ["github-common alias"], "weight": 1.0}
  ],
  "mechanisms": [
    {"term": "concrete solution mechanism", "aliases": ["mechanism alias"], "weight": 0.8}
  ],
  "exploration_directions": [{"term": "not-yet-covered direction", "weight": 0.6}],
  "artifact_types": ["application", "mcp", "skill", "mod", "plugin", "library"],
  "constraints": {
    "language": "Python",
    "pushed_after": "2025-01-01",
    "include_archived": false
  },
  "exclusions": ["course", "awesome list"],
  "exploration_level": 0.5
}
```

`request` and at least one `problem_concepts` entry are required. Weights and `exploration_level` range from 0 to 1. Omit constraints the user did not state; do not invent minimum Star counts because low exposure is part of hidden-gem discovery. The v0.3 `core_concepts` and `adjacent_concepts` fields remain accepted and are converted to `problem_concepts` and `exploration_directions`, but new requests should use the v0.4 fields.

`term` is the concept the user understands. Terms and aliases are single-line strings up to 160 characters. `aliases` are GitHub-common expressions, English terms, or domain words for the same concept; at most four per concept. Aliases in one group count as one concept and must not be used to stack scores. Mechanism aliases receive their own bounded recall opportunities. Keep generic artifact words such as `skill`, `tool`, `AI`, and `agent` out of problem/mechanism concepts and aliases when a specific term is available. Put the desired form in `artifact_types`. The CLI ignores standalone generic problem terms and will not emit an isolated `"Skill"` query.

Preserve concise Chinese capability phrases verbatim, for example `正文配图`, `文章配图`, `专注管理`, and `自控训练`; do not split them by character, whitespace, or Latin-word rules, and do not add particle special cases. Keep English concepts to one to three meaningful words. For a non-English need, add at least one GitHub-common English expression as an alias. Do not use a fixed translation dictionary; choose the alias from the confirmed interpretation.

For a deep-mode iteration, pass a search hypothesis to `muse-shroom iterate --search-id SEARCH_ID --refinement FILE`. `decision` must be exactly `continue` or `stop`.

```json
{
  "decision": "continue",
  "reason": "why this round is worth running",
  "target_direction": "unexplored boundary direction",
  "target_mechanism": "mechanism to verify",
  "concepts": ["reformulated search term"],
  "aliases": ["GitHub-common wording"],
  "negative_directions": ["confirmed wrong sense, such as DOM focus"],
  "anchors": ["term observed in evidence"],
  "seeds": ["owner/repo selected from candidates"],
  "filenames": ["distinctive filename observed in evidence"],
  "exclude": ["newly observed irrelevant meaning"],
  "rejected_directions": ["direction the user explicitly rejected"],
  "promote_discovered_terms": ["digital wellbeing"],
  "add_exploration_directions": [
    {"term": "commitment device", "reason": "related blocking strategy", "evidence": "discovered_term"}
  ],
  "strategies": ["keyword"]
}
```

To stop, pass:

```json
{
  "decision": "stop",
  "stop_reason": "low expected boundary gain",
  "remaining_unexplored_directions": ["biofeedback"]
}
```

`negative_directions` are session-level wrong senses produced by the Agent. They are not `rejected_directions`, which are user refusals. Discovered terms are not searched and do not become mechanisms until listed in `promote_discovered_terms` or `add_exploration_directions` and later matched by description, Topics, or README evidence.

`strategies` may include `keyword`, `relationship`, `seed`, `code`, and `owner`. Omit it to run keyword reformulation only; supplying `seeds` or `filenames` also enables the matching retrieval strategy. The CLI skips queries that match history after normalizing case and token order, and it skips terms covered by `negative_directions`.

Default deep-mode budget: 3 iterations after the initial search, 6 keyword queries per iteration, 30 session search queries, 15 README enrichments per iteration, and the existing relationship-call budget. Observation includes `remaining_budget` and `stop.reasons`. Hard stops are `max_iterations`, `query_budget_exhausted`, `duplicate_queries`, and `agent_stop`.

The older `expand` command still accepts:

```json
{
  "concepts": ["domain term or alias"],
  "adjacent_concepts": ["supported nearby direction"],
  "anchors": ["term observed in first-round evidence"],
  "seeds": ["owner/repo selected from first-round candidates"],
  "filenames": ["distinctive filename observed in first-round evidence"],
  "exclude": ["newly observed irrelevant meaning"],
  "rejected_directions": ["direction explicitly rejected by the user"]
}
```

Keep all arrays short and evidence-driven. Muse-shroom generates and validates GitHub syntax.

All hypothesis and refinement values must be arrays of strings except `decision`, `reason`, `stop_reason`, `target_direction`, `target_mechanism`, and `add_exploration_directions` objects. Seeds use `owner/repo`; filenames must be basenames such as `SKILL.md`, never paths or query fragments. Original search constraints remain active during iteration.
