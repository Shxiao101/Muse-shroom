# Rank result contract

MCP `muse_rank` and `muse-shroom rank` validate and record the Agent's ordered
selection. Code does not compose or reorder it.

Read the primary interface:

```text
items
display_order
rejected_items
no_recommendation
boundary_role
mechanism_label
new_mechanisms
rationale
verification
coverage
stale
incomplete_phase
next_action
```

`items` contains accepted selections in their original order, and `display_order`
is the corresponding repository sequence. `rejected_items` reports an input index,
repository, and mechanical rejection reasons. A rejected item is omitted; accepted items
are never moved to fill a preferred lane or score.

The search response separates recall from assessment: `candidate_count` is the full
recall pool size, while `candidates` is the assessment shortlist and may omit recalled
candidates. Before rank, use `candidates --scope all` or `inspect` when an omitted
candidate may contain evidence needed for assessment. `selection` is the ordered list
submitted by the host Agent; after mechanical validation, `items` and `display_order`
preserve that order. The `popular`, `gems`, and `adjacent` fields are compatibility
projections and do not define an additional order.

Each accepted item includes the Agent fields plus raw facts: stars, `star_growth`, forks,
open issues, pushed-at, archived flag, license, primary language, topics, description,
evidence, and discovery paths. No aggregate usefulness score is returned.

`new_mechanisms` is only the ordered set difference between Agent labels and labels
previously presented in the session. It is not a judgement of novelty or quality.
`verification` names the cited evidence ID and recorded SHA that contain the exact
`source_term` and `quote`. The mechanism label is deliberately not text-matched.

Boundary roles are Agent assignments:

- `anchor`: mainstream, reliable reference
- `edge`: near the current approach, with a mechanism change
- `leap`: steps off the main solution path
- `wildcard`: not obviously on-topic, but the mechanism can transfer

Read `next_action` after rank. `done` means the rank is terminal. Do not call search, observe, inspect, or diagnostics afterward. Present accepted items in `display_order` and explain any rejected items when relevant. When `items` is empty, `next_action` is `done`, and `no_recommendation.reason` is present, present that reason: the Agent judged that no candidate was worth recommending, and the ranking was saved. That is not a crash and is not the same as every submitted item failing verification.

`rank` means at least one item failed mechanical verification, so the rank is not final: accepted items are returned but nothing was saved, and the session is still open. Read each `rejected_items` entry — `reasons` says what failed and `evidence_ids_checked` says which evidence was examined, which distinguishes citing the wrong evidence from quoting it wrongly. Fix the selection and call `muse_rank` again. Do not search again. When a quote cannot be corrected, resubmit only the passing items: a submission with zero rejections returns `done` and saves the ranking. An empty `selection` with `no_recommendation.reason` also returns `done` and saves `items=[]`. An empty `selection` without that reason is a contract error.

Do not append a second priority, recommendation, or best-first order.

For each item render `New mechanism: <comma-separated new_mechanisms>` when the array is
non-empty and `New mechanism: none` when it is empty.

Do not describe the number of returned projects as the number of distinct mechanisms; use `coverage.presented_mechanism_count`.
Disclose `boundary.unexplored_directions` and requested mechanisms absent from
`boundary.presented_mechanisms`. If `stale` is true or `incomplete_phase` is set, disclose
it briefly.

## explorer_url

`explorer_url` deep-links to the finished session in the local read-only Explorer.
`explorer_running` says whether it answered. Show the link only when it is present and
running; do not launch another browser or diagnostic command after rank.
