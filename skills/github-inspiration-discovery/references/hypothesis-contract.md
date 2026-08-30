# Search hypothesis contract

Pass a JSON object to MCP `muse_iterate` or `muse-shroom iterate --search-id SEARCH_ID --refinement FILE`. `decision` must be exactly `continue` or `stop`. MCP rejects unknown hypothesis fields such as `mechanisms` or `rationale`.

Each round, fill only the fields that this decision needs. Do not mechanically populate every array.

## Continue

```json
{
  "decision": "continue",
  "reason": "why this round is worth running",
  "target_direction": "unexplored boundary direction",
  "target_mechanism": "mechanism to verify",
  "concepts": ["reformulated search term"],
  "negative_directions": ["confirmed wrong sense, such as DOM focus"],
  "promote_discovered_terms": ["digital wellbeing"],
  "add_exploration_directions": [
    {"term": "commitment device", "reason": "related blocking strategy", "evidence": "discovered_term"}
  ],
  "strategies": ["keyword"]
}
```

## Stop

```json
{
  "decision": "stop",
  "stop_reason": "low expected boundary gain",
  "remaining_unexplored_directions": ["biofeedback"]
}
```

## Field roles

Prefer: `decision`, `reason`, `target_direction`, `target_mechanism`, `concepts`, `negative_directions`, `promote_discovered_terms`, `add_exploration_directions`, `strategies`.

Advanced, only when first-round evidence supports them: `aliases`, `anchors`, `seeds`, `filenames`, `exclude`, `rejected_directions`, `adjacent_concepts`.

Direction fields are not interchangeable:

- `rejected_directions`: the user explicitly does not want this direction (“不要 timer”).
- `negative_directions`: a wrong sense or ambiguity confirmed during search (“DOM focus 不是我要的”).
- `target_direction` / `add_exploration_directions`: a new positive direction the user wants (“更想看行为干预”).

Do not write a new positive preference into `negative_directions` or `rejected_directions`. Discovered terms are not searched and do not become mechanisms until listed in `promote_discovered_terms` or `add_exploration_directions` and later matched by description, Topics, or README evidence.

`promote_discovered_terms` must match a term in
`observation.discovered_term_evidence`. A new
`add_exploration_directions` item must cite `discovered_term`, a candidate
evidence ID, or `user_request` when the user explicitly introduced the
direction. Unsupported high-priority directions are rejected.

The initial deep search response contains the observation used to decide the first iteration. After every successful iteration whose `next_action` remains `iterate`, restore the session with MCP `muse_observe` or `muse-shroom observe --search-id SEARCH_ID` before preparing another hypothesis. Never chain two `muse_iterate` calls without an intervening `muse_observe`. Observe is read-only: no GitHub requests, no new iteration, no boundary writes. `next_action=done` means the default flow has finished and you must not continue automatically. `can_iterate=true` means a user request for more (还有吗 / 再找一些 / 换点不同的) may still `iterate` this `search_id`. `can_iterate` is true only for deep mode with remaining iterations, remaining queries, and no hard stop.

`strategies` may include `keyword`, `relationship`, `seed`, `code`, and `owner`. Omit it to run keyword reformulation only; supplying `seeds` or `filenames` also enables the matching retrieval strategy.

## Observation

Decide from `observation` first, in this order: `stop`, `unexplored_directions`, `boundary_delta`, `mechanism_distribution`, `ambiguity_signals`, `discovered_terms`, `remaining_budget`, `anchors`. Do not re-read the full candidate pool unless observation is insufficient.

If `stop.should_stop` is true, stop iterating. `stop.signals` (`no_new_mechanism`, `no_boundary_gain`, `directions_covered`) are advisory only. You may continue when remaining budget, discovered terms, or unexplored directions still look valuable.

Hard stops recorded in `stop.reasons`: `agent_stop`, `max_iterations`, `query_budget_exhausted`, `duplicate_queries`, `consecutive_no_gain`.

Default deep-mode budget: 3 iterations after the initial search, 6 keyword queries per iteration, 30 session search queries, 15 README enrichments per iteration, a 250-candidate pool. Quick mode stays at 100 candidates and does not iterate.

Follow `next_action` from the CLI: after deep `search` it is `iterate`; after iterate it is `iterate` or `rank`.

The CLI `expand` command remains as compatibility only. New Agent flows use `iterate`.
