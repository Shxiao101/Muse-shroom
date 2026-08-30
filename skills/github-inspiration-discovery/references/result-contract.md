# Rank result contract

MCP `muse_rank` and `muse-shroom rank` return the same display interface. Do not re-sort it.

Read:

```text
buckets
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

`display_order` is the order to present repositories: popular, then gems, then adjacent. `boundary_role`, `new_mechanisms`, and `why_different` are computed along that order. `selection_order` is internal pick order for debug only; do not show it to the user.

`next_action` is `done` after a successful rank.

`boundary_role` meanings:

- `anchor`: mainstream, reliable reference
- `edge`: near the current approach, with a mechanism change
- `leap`: steps off the main solution path
- `wildcard`: not obviously on-topic, but the mechanism can transfer

If `stale` is true or `incomplete_phase` is set, disclose that briefly and still present reliable rows.

Present each item as: name, one-line use, boundary role, why it is worth looking at, and a visible new-mechanism field. Render `New mechanism: <comma-separated new_mechanisms>` when non-empty and `New mechanism: none` when empty, translated to the user's language when appropriate. Copy only the item's computed `new_mechanisms`; do not infer one from its description, category, or `why_different`.

Project count and mechanism count are different quantities. Do not describe the number of returned projects as the number of distinct mechanisms. Use `newly_presented_mechanisms` or `coverage.presented_mechanism_count` for mechanism-diversity summaries, and keep items with empty `new_mechanisms` explicit.
