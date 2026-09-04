# Muse-shroom search internals

CLI, MCP, and Explorer implementation notes. Agent Skills should follow `skills/muse-shroom/` instead of this file. MCP is a stdio adapter over the same Core; Explorer is a local read-only UI over SQLite. Session state stays in SQLite and `search_id` is always explicit. Explorer does not search, iterate, rank, or call GitHub.

- Quick mode: at most 12 controlled queries; aliases do not expand the API budget. RRF is capped per concept group.
- Search output is schema v2: `candidate_count` is full recall, `candidates` is the assessment shortlist of at most 12 rows.
- `boundary.recalled_mechanisms` covers the full pool; `presented_mechanisms` covers the shortlist or final ranking. Mechanism labels need description, Topics, or README evidence.
- `discovered_terms` stay unconfirmed until a hypothesis promotes them and later evidence matches.
- `negative_directions` are session-level wrong senses; `rejected_directions` are user refusals; new positive directions go to `add_exploration_directions`.
- `muse-shroom observe --search-id` restores observation without GitHub calls or writes. After rank, `next_action` is `done`; `can_iterate` is true only for deep mode with remaining budget and no hard stop.
- Deep mode: at most 3 iterates after search, 6 new queries per round, 30 session search queries, candidate pool 250 (quick 100). `stop.reasons` are hard stops; `stop.signals` are advisory.
- Rank uses a single Boundary-first composition for `items` / `display_order`. The popular, gems, and adjacent fields are compatibility projections computed afterward; `selection_order` is debug-only.
- Probe stage: at most 2 repos per owner. Deep shortlist uses a boundary lane so one mechanism cannot fill the list.
- Search JSON is capped at 30KB. Public evidence is at most 3 items per candidate; Release lives on `latest_release`.
- Relationship expansion runs only when the hypothesis selects `relationship`, `seed`, or `owner`.
