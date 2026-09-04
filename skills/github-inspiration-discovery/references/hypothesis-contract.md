# Search hypothesis contract

Pass a JSON object to MCP `muse_iterate` or `muse-shroom iterate --search-id SEARCH_ID --refinement FILE`. `decision` must be exactly `continue` or `stop`. MCP rejects unknown hypothesis fields such as `mechanisms` or `rationale`.

Each round, fill only the fields that this decision needs. Do not mechanically populate every array.

## Continue

Evidence-derived refinement:

```json
{
  "decision": "continue",
  "reason": "why this round is worth running",
  "target_direction": "unexplored boundary direction",
  "target_mechanism": "mechanism to verify",
  "concepts": ["reformulated search term"],
  "negative_directions": ["confirmed wrong sense, such as DOM focus"],
  "promote_discovered_terms": ["observed-term-from-evidence"],
  "add_exploration_directions": [
    {"term": "observed-term-from-evidence", "reason": "related mechanism", "evidence": "discovered_term"}
  ],
  "strategies": ["keyword"]
}
```

Host world-knowledge hypothesis (semantic sidecar, iterations 1-2 only):

```json
{
  "decision": "continue",
  "reason": "Test a mechanism from a neighboring domain.",
  "add_exploration_directions": [
    {
      "term": "<hypothesized mechanism>",
      "request_anchor": "<existing problem concept or alias>",
      "reason": "This may transfer because <concise causal connection>.",
      "evidence": "host_hypothesis"
    }
  ],
  "strategies": ["keyword"]
}
```

Do not repeat the host term in `target_direction`, `target_mechanism`, `concepts`, or `adjacent_concepts`. Ordinary fields remain available for simultaneous evidence-driven refinement. The core routes `host_hypothesis` to a separate sidecar with its own query and README budgets.

## Stop

```json
{
  "decision": "stop",
  "stop_reason": "low expected boundary gain",
  "remaining_unexplored_directions": ["still-open direction"]
}
```

Stopping at the first iteration of a deep search still owes the cross-domain decision, so `stop_reason` carries it:

```json
{
  "decision": "stop",
  "stop_reason": "Covered the requested mechanisms; no cross-domain leap because <one short reason>.",
  "remaining_unexplored_directions": ["still-open direction"]
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
`add_exploration_directions` item must cite `discovered_term`, that term's
own candidate evidence ID, `user_request` when the user explicitly introduced the
direction, or `host_hypothesis` for a world-knowledge leap. `host_hypothesis`
requires `request_anchor` matching an original problem concept or alias. It is
provenance, not repository evidence. Unsupported high-priority directions are rejected.

At most two `host_hypothesis` additions are allowed in the whole session, and only during post-search iterations one and two. After iteration two, or after two host hypotheses have been submitted, all further refinements must be evidence-derived. Failed hypotheses do not create extra allowance.

The first iteration of a deep search must record a decision about the cross-domain leap: either a `host_hypothesis` addition, or a sentence in `reason` (or `stop_reason`, when this round stops) explaining why the request admits no transfer from a neighbouring domain. Both fields are single-line and capped at 500 characters. Zero hypotheses remains valid; an unstated decline does not. Do not manufacture one to satisfy this — a synonym of a mechanism already in the request, or one whose causal connection you cannot state, is worse than a stated decline.

The initial deep search response contains the observation used to decide the first iteration. After every successful iteration whose `next_action` remains `iterate`, restore the session with MCP `muse_observe` or `muse-shroom observe --search-id SEARCH_ID` before preparing another hypothesis. Never chain two `muse_iterate` calls without an intervening `muse_observe`. Observe is read-only: no GitHub requests, no new iteration, no boundary writes. `next_action=done` means the default flow has finished and you must not continue automatically. `can_iterate=true` means a user request for more (还有吗 / 再找一些 / 换点不同的) may still `iterate` this `search_id`. `can_iterate` is true only for deep mode with remaining iterations, remaining queries, and no hard stop.

`strategies` may include `keyword`, `relationship`, `seed`, `code`, and `owner`. Omit it to run keyword reformulation only; supplying `seeds` or `filenames` also enables the matching retrieval strategy.

## Observation

Decide from `observation` first, in this order: `stop`, `unexplored_directions`, `boundary_delta`, `mechanism_distribution`, `ambiguity_signals`, `discovered_terms`, `semantic_hypotheses`, `remaining_budget`, `anchors`. Do not re-read the full candidate pool unless observation is insufficient.

If `stop.should_stop` is true, stop iterating. `stop.signals` (`no_new_mechanism`, `no_boundary_gain`, `directions_covered`) are advisory only. You may continue when remaining budget, discovered terms, or unexplored directions still look valuable.

Hard stops recorded in `stop.reasons`: `agent_stop`, `max_iterations`, `query_budget_exhausted`, `duplicate_queries`, `consecutive_no_gain`.

Default deep-mode budget: 3 iterations after the initial search, 6 keyword queries per iteration, 30 session search queries, 15 README enrichments per iteration, a 250-candidate pool. The semantic sidecar is extra: up to 2 host hypotheses, 2 queries each, 4 README enrichments, and up to 2 extra assessment candidates. Quick mode stays at 100 candidates and does not iterate or invoke the sidecar.

Follow `next_action` from the CLI: after deep `search` it is `iterate`; after iterate it is `iterate` or `rank`.

The CLI `expand` command remains as compatibility only. New Agent flows use `iterate`.
