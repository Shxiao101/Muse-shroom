# Rank result contract

MCP `muse_rank` and `muse-shroom rank` return the same display interface. Do not re-sort it.

Read the primary interface:

```text
items
display_order
boundary_role
new_mechanisms
why_different
boundary_summary
newly_presented_mechanisms
coverage
stale
incomplete_phase
next_action
```

`items` is the only recommendation list and is already in `display_order`. The composer directly balances a mainstream anchor, new-mechanism coverage, cross-mechanism leaps, transferable wildcards, and mechanism repetition. `boundary_role`, `new_mechanisms`, and `why_different` are computed along that order. `selection_order` is internal debug information; do not show it to the user.

`buckets.popular`, `buckets.gems`, and `buckets.adjacent` are compatibility/debug projections created after `items` is final. They may be omitted by a future consumer without changing the primary list. Never concatenate or reorder them to construct a presentation.

`next_action` is `done` after a successful rank and is terminal. Do not call observe, inspect, search, iterate, shell, or other diagnostic tools after that successful rank; present the returned result directly.

`semantic_hypotheses` is additive audit state. Status values are `proposed`, `searched`, `evidence_found`, `validated`, `rejected`, and `inconclusive`. Only validated hypotheses that appear in `items` may be presented as formal new mechanisms. Explorer retains the complete sidecar history.

`boundary_role` meanings:

- `anchor`: mainstream, reliable reference
- `edge`: near the current approach, with a mechanism change
- `leap`: steps off the main solution path
- `wildcard`: not obviously on-topic, but the mechanism can transfer

If `stale` is true or `incomplete_phase` is set, disclose that briefly and still present reliable rows.

Present each item as: name, one-line use, boundary role, why it is worth looking at, and a visible new-mechanism field. Render `New mechanism: <comma-separated new_mechanisms>` when non-empty and `New mechanism: none` when empty, translated to the user's language when appropriate. Copy only the item's computed `new_mechanisms`; do not infer one from its description, category, or `why_different`.

Use `display_order` as the only ranked order. Do not append a second priority, recommendation, or best-first order. Scenario guidance may point different needs to different items without changing or implying another ranking.

For deep results, disclose non-empty `boundary.unexplored_directions` and requested mechanisms absent from `boundary.presented_mechanisms`. Do not claim complete coverage when those gaps remain.

Project count and mechanism count are different quantities. Do not describe the number of returned projects as the number of distinct mechanisms. Use `newly_presented_mechanisms` or `coverage.presented_mechanism_count` for mechanism-diversity summaries, and keep items with empty `new_mechanisms` explicit.

## explorer_url

`explorer_url` deep-links to the finished session in the local read-only Explorer
(`http://127.0.0.1:8765/#/s/<search_id>/results`). `rank` starts that server in the
background when nothing is already answering on the port, and the server stops itself
after an idle period, so the link is live without leaving a process running forever.

`explorer_running` says whether an Explorer actually answered. Show the link when it is
true. Both fields are absent when the caller opted out with `--no-explore` or
`MUSE_SHROOM_NO_EXPLORER=1`. Surfacing the link requires no tool call and does not make a
terminal rank non-terminal; do not launch a browser or a shell command for it.
