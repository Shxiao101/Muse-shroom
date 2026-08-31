# Boundary Discovery audit

## Scope

This audit follows the runtime path:

```text
SearchRequest → query construction → GitHub recall/enrichment
→ mechanism annotation → boundary snapshot/observation
→ evidence-gated hypothesis → iterate → boundary-first ranking
→ Boundary Role presentation
```

## Findings

| Stage | Verified behavior | Gap found | Action |
| --- | --- | --- | --- |
| Request | Problem, mechanism, and exploration concepts are separate. | Initial directions still depend on the host Agent. | Keep this as the initial prior; require later additions to cite evidence. |
| Recall | Initial and iterative queries have fingerprints and iteration history. | A free-form hypothesis could introduce an unsupported direction. | Validate target/promoted/additional directions against request, observed evidence, or explicit user input. |
| Annotation | Mechanisms require description, Topics, or README matches and retain evidence IDs. | Newly discovered Topics were returned only as strings. | Add typed `discovered_term_evidence` with repository, source field, and evidence ID. |
| Boundary | Recalled and presented mechanisms are distinct; rejected and negative directions are session state. | Search and session state are serialized together, so ownership was hard to read. | Treat boundary snapshots as search facts and session state as decisions/budgets; expose a trace rather than duplicating fields again. |
| Iterate | Duplicate queries and bounded stopping already work. | Promotion did not prove that the term came from search evidence. | Reject unsupported promotions and high-priority new directions. |
| Ranking | Relevance/type gates, novelty, transferability, redundancy, and deterministic MMR already exist. | Historical positive feedback could boost same-topic repositories. | Limit feedback adjustment to exact-repository rejection/difficulty signals. |
| Presentation | Items have Anchor/Edge/Leap/Wildcard, new mechanisms, and transferability. | Compatibility buckets previously fed display composition. | Compose `items` directly from Boundary objectives; derive buckets afterward for compatibility. |
| Evaluation | All requested diagnostics existed, but were diagnostic-only. | No formal agentic-loop verdict or fixed mechanism-space cases. | Add a boundary evaluator and eight golden mechanism-space cases. |
| Explorer | Boundary, iterations, queries, roles, and results were available. | The source of a newly proposed direction was not visible. | Show discovered-term evidence and per-iteration queries/evidence sources. |

## State ownership

- **Search Boundary:** recalled/presented mechanisms, mechanism origins, discovered
  evidence, explored/unexplored directions, and deltas derived from candidates.
- **Session Boundary:** iteration budget, query history, negative/rejected directions,
  user-authorized additions, no-gain count, and stop reason.
- The persisted request may contain accepted exploration additions so later mechanism
  annotation can verify them. Their provenance remains in session
  `exploration_additions`; it is not inferred from the mutated request.

## Deliberately deferred

- Automatic free-form mechanism extraction from arbitrary README prose remains deferred.
  v0.4.2 adds only a bounded phrase lexicon over repository descriptions, relationship
  details, and curated README sections; badge text is not a strong signal.
- Long-term Personal Boundary remains out of scope.
- `popular/gems/adjacent` are derived compatibility fields and no longer feed the primary composition.
- Formal thresholds should be recalibrated after several recorded agentic-loop packs;
  the evaluator reports an insufficient-data verdict rather than passing single-pass
  runs.
