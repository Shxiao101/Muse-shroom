# Search hypothesis contract

Pass a JSON object to `muse-shroom iterate --search-id SEARCH_ID --refinement FILE`. `decision` must be exactly `continue` or `stop`.

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

`negative_directions` are session-level wrong senses. `rejected_directions` are user refusals. Discovered terms are not searched and do not become mechanisms until listed in `promote_discovered_terms` or `add_exploration_directions` and later matched by description, Topics, or README evidence.

`strategies` may include `keyword`, `relationship`, `seed`, `code`, and `owner`. Omit it to run keyword reformulation only; supplying `seeds` or `filenames` also enables the matching retrieval strategy.

## Observation

Decide from `observation` first, in this order: `stop`, `unexplored_directions`, `boundary_delta`, `mechanism_distribution`, `ambiguity_signals`, `discovered_terms`, `remaining_budget`, `anchors`. Do not re-read the full candidate pool unless observation is insufficient.

If `stop.should_stop` is true, stop iterating. `stop.signals` (`no_new_mechanism`, `no_boundary_gain`, `directions_covered`) are advisory only. You may continue when remaining budget, discovered terms, or unexplored directions still look valuable.

Hard stops recorded in `stop.reasons`: `agent_stop`, `max_iterations`, `query_budget_exhausted`, `duplicate_queries`, `consecutive_no_gain`.

Default deep-mode budget: 3 iterations after the initial search, 6 keyword queries per iteration, 30 session search queries, 15 README enrichments per iteration, a 250-candidate pool. Quick mode stays at 100 candidates and does not iterate.

Follow `next_action` from the CLI: after deep `search` it is `iterate`; after iterate it is `iterate` or `rank`.

The CLI `expand` command remains as compatibility only. New Agent flows use `iterate`.
