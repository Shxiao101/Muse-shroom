# Muse-shroom v0.6.0 Skill-First Sidecar Execution Plan

## Summary

v0.6.0 will add host-Agent semantic discovery as a bounded sidecar path. It must not consume capacity from the existing evidence-driven path until its value has been demonstrated.

The existing path retains:

- 6 refinement queries per iteration.
- 30 search queries per session.
- A 250-candidate deep-search pool.
- 15 README enrichments per iteration.
- A 12-candidate regular assessment shortlist.
- A 10-item final ranked result.

The semantic sidecar receives separate session-wide capacity:

- Up to 2 host hypotheses.
- 2 queries per hypothesis, 4 total.
- Up to 40 temporary recall candidates.
- Up to 2 README enrichments per hypothesis, 4 total.
- Up to 1 additional assessment candidate per hypothesis, 2 total.
- Up to 2 additional release lookups for those assessment candidates.

A full run therefore executes at most 34 search queries and exposes at most 14 assessment candidates. Final ranked output remains capped at 10.

## Skill Changes

### Ground the Initial Search

The initial `SearchRequest` may contain only:

- The user’s actual problem and constraints.
- Direct paraphrases and GitHub-common aliases.
- Mechanisms stated or tightly implied by the request.
- Exploration directions explicitly requested by the user.

The Agent must not place world-knowledge leaps in the initial request. Quick mode remains unchanged and never invokes the semantic sidecar.

Host evaluation will enforce an exact fixture-provided initial request. Production behavior remains governed by the Skill.

### Use a Two-Round Hypothesis Window

During the first two post-search iterations, the Agent may submit at most two host hypotheses across the entire session.

The Agent may submit both together or reserve one for the second observation. Failed hypotheses do not create extra allowance.

For each hypothesis, the Agent must:

- Propose a genuinely different mechanism, not a synonym.
- Anchor it to an existing problem concept or alias.
- Give a concise causal transfer rationale.
- Respect exclusions and negative directions.
- Avoid claiming that repository evidence already exists.
- Avoid naming a repository as the hypothesis.

Zero hypotheses remains valid.

After iteration two or after two hypotheses have been submitted, all further refinements must be evidence-derived.

### Reuse `add_exploration_directions`

Do not introduce a new top-level hypothesis field. Extend `ExplorationAddition` with `request_anchor`:

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

`host_hypothesis` is provenance, not evidence.

The Agent must not repeat the host term in ordinary `target_direction`, `target_mechanism`, `concepts`, or `adjacent_concepts`. The core routes the addition to the sidecar; ordinary hypothesis fields remain available for simultaneous evidence-driven refinement.

Production Skill examples must use placeholders or abstract examples. They must not contain development or holdout Golden phrases.

### Assess Semantic Candidates

When the sidecar returns mechanism evidence, the Agent should assess the supplied semantic assessment candidate.

The assessment must include:

- An exact evidence-backed `mechanism`.
- A reason citing the corresponding `mechanism_match`.
- A conservative README-backed use case.
- `transferability` and `boundary_value` when they can be judged.

The numeric scores remain ranking inputs and diagnostics. They are not pass thresholds.

### Present Results

The Skill must distinguish:

- `proposed`
- `searched`
- `evidence_found`
- `validated`
- `rejected`
- `inconclusive`

Only validated semantic mechanisms appearing in final ranked items may be presented as formal new mechanisms. Explorer retains the complete sidecar history.

## Public Contract and Provenance

- Add optional `request_anchor` to `ExplorationAddition`.
- Reserve `evidence: "host_hypothesis"` for Agent world-knowledge hypotheses.
- Require its anchor to match an original `problem_concepts` term or alias.
- Accept it only during post-search iterations one and two.
- Enforce a session-wide maximum of two.
- Record the actual `source_iteration`.
- Preserve existing `user_request` and `discovered_term` behavior.
- Keep search result schema v2 through additive output fields.
- Avoid a SQLite migration.

Fix the evidence-ID loophole:

- A cited candidate evidence ID is valid only when the same normalized term exists in `discovered_term_evidence`.
- The evidence ID must belong to that term’s own `sources`.
- Update the v0.5.1 deterministic fallback so it no longer depends on an unrelated evidence ID.

## Isolated Sidecar Data Flow

### Base Phase

For every iteration, execute the existing path first using the original request and evidence-derived hypothesis fields:

1. Plan and execute up to 6 ordinary queries.
2. Merge results into the existing 250-candidate pool.
3. Apply the existing 15-README enrichment budget.
4. Build the ordinary Boundary and regular 12-candidate shortlist.

Host-hypothesis additions must not:

- Enter the persisted `SearchRequest.exploration_directions`.
- Change ordinary query planning.
- Change base candidate scores or lanes.
- Consume base README capacity.
- Consume either adjacent shortlist slot.
- Affect the canonical Boundary before validation.

### Semantic Recall Phase

After the base phase, execute the sidecar for newly submitted host hypotheses.

Each hypothesis generates:

1. Pure query: `"<mechanism>" ...`
2. Bridge query: `"<mechanism>" "<request anchor>" ...`

The two concepts are separately quoted. Never generate one strict combined phrase.

Semantic queries use a separate four-query session budget and separate query kinds. They do not count toward the 6/30 base budgets.

Store up to 40 unique sidecar candidates with:

- Hypothesis ID.
- Pure or bridge discovery path.
- `retrieval_partition: "semantic_sidecar"`.
- Source iteration.
- Overlap status when the repository already exists in the base pool.

Sidecar-only candidates remain excluded from base selection and Boundary calculations.

### Semantic Enrichment Phase

For each hypothesis:

- Select up to two candidates from its own recall results.
- Use a separate README budget.
- Match the hypothesized term against description, Topics, and README.
- Emit `mechanism_match` evidence carrying the hypothesis ID and semantic origin.

The hypothesized term is a legal match target inside the sidecar even though it is not part of the base request.

Secondary terms harvested from sidecar repositories are measured separately. They do not enter ordinary `discovered_term_evidence` or later base refinement in v0.6.0.

### Assessment Merge

For every hypothesis reaching `evidence_found`:

- Select at most one evidence-bearing candidate for semantic assessment.
- Prefer an already selected regular candidate when the repository overlaps the base shortlist.
- Otherwise append one sidecar assessment candidate without displacing any of the regular 12.
- Expose at most 14 total assessment candidates.

The regular shortlist composition and its lane counts must remain unchanged.

### Final Ranking Merge

Rank the union of regular and semantic assessments, while keeping the final display cap at 10.

A semantic hypothesis becomes:

- `evidence_found` when objective repository mechanism evidence exists.
- `validated` when an eligible assessment names the same evidence-backed mechanism and cites that evidence.
- `presented` when its validated candidate appears in the final ranked result.

`transferability` and `boundary_value` remain reported but do not determine validation.

Unvalidated semantic terms must be removed from:

- Final `new_mechanisms`.
- Presented Boundary mechanisms.
- Leap or Wildcard mechanism claims.
- Recommendation explanations.

A validated semantic result may displace an ordinary item in the final top 10. That is an intentional presentation decision and must be reported as such; it is not retrieval-budget cannibalization.

## No-Cannibalization Metrics

Report base and sidecar resources separately:

- Base queries planned, executed, duplicated, and budget-skipped.
- Semantic queries planned, executed, reused, and failed.
- Base and semantic candidate counts.
- Base and semantic README enrichments.
- Regular and semantic assessment counts.
- Base/semantic repository overlap.
- Sidecar API-call cost.
- Validated semantic items entering the top 10.
- Regular top-10 items displaced by semantic results.
- Secondary sidecar terms blocked by `request_anchored`.

Add a semantic-disabled replay for the same recorded action sequence.

The no-cannibalization check passes only when enabling semantic additions leaves these base artifacts identical:

- Ordinary query fingerprints and execution results.
- Base candidate pool.
- Base README-enriched repository set.
- Regular 12-candidate shortlist and lane counts.
- Base Boundary before final rank.

Ignore timestamps and semantic-origin metadata during comparison.

This check is release-blocking.

## Derived Audit State

Do not introduce a separate database state machine. Derive status from:

- Host-hypothesis additions.
- Semantic query history.
- Sidecar candidate and enrichment records.
- Mechanism evidence.
- Assessments and final ranking.
- Budget skips and incomplete phases.

Expose additive `semantic_hypotheses` records through observation, rank, evaluation, and Explorer.

A hypothesis is:

- `rejected` only when both planned queries completed successfully and no mechanism evidence was found.
- `inconclusive` when either query or required enrichment was skipped, interrupted, or failed.

## Host Evaluation

### Isolated Capture and Replay

Implement `prepare`, `collect`, and `score`:

- `prepare` creates a sanitized bundle with production code, the Skill, anonymous cases, and fixed initial requests.
- Exclude Golden files, results, reviews, tests, and `.git`.
- `collect --mode capture` records host actions and all GitHub responses into a fresh per-run cassette.
- `collect --mode replay` reapplies those recorded actions without invoking the model.
- `score` maps anonymous cases to Golden data outside the bundle.

Deterministic and host cassettes remain separate.

### Capture Rate-Limit Handling

Capture-only GitHub access uses one shared serial request queue across search, README, release, repository, code-search, fork, and owner endpoints. Production concurrency remains unchanged.

- Keep a 3.5-second search interval.
- Honor `Retry-After`.
- Honor `X-RateLimit-Reset` when the primary limit is exhausted.
- Otherwise wait at least 60 seconds for secondary limits and use bounded exponential backoff for at most three retries.
- Record response headers, waits, and retries.

This follows GitHub’s guidance to avoid concurrent REST requests and respect rate-limit headers. [GitHub REST API best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api), [rate-limit handling](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).

Every capture must replay successfully in a fresh data directory before its results are accepted.

### Discovery Capability Gate

For each case, report:

- Host hypotheses proposed.
- Golden cross-domain hypotheses proposed.
- Pure and bridge queries executed.
- Golden direction queries executed.
- Mechanism evidence found.
- Validated and presented hypotheses.
- Query and enrichment costs.
- No-cannibalization metrics.

The v0.6.0 capability gate requires:

- A valid transcript.
- Passing leakage checks.
- Passing no-cannibalization checks.
- At least one development case querying an acceptable Golden cross-domain direction or alias.
- At least one matching repository mechanism-evidence hit from that query.

Query-only hits do not pass. Agent numeric scores do not affect this gate.

Development aggregate quality, final displacement, query cost, and all holdout discovery metrics remain observational.

## Leakage, Labels, and Existing Fixtures

Extend leakage scanning to the production Skill and its contract references. Check development and holdout cross-mechanism terms and aliases.

Remove concrete Golden phrases currently used in Skill examples.

Add these development-only blind labels:

- `focus | gui automation` → `wrong_domain`
- `learning-habit | local first` → `too_generic`
- `phone-distraction | attention monitoring` → `meaningful`
- `code-review | devops automation` → `wrong_domain`
- `personal-knowledge | rank tracking` → `wrong_domain`

Keep the committed v0.5.1 instrumentation from `d4e3d3f`.

Rebuild deterministic and host cassettes from empty staging files. Resolve the `l33tdawg/sage` replay failure through a complete recapture rather than a manual cassette entry.

## Test Plan

- Validate Skill examples against strict MCP schemas.
- Test zero, one, and two hypotheses distributed across iterations one and two.
- Test the session-wide hypothesis and sidecar budgets.
- Test anchors, exclusions, negatives, duplicates, and later evidence-only behavior.
- Test pure and bridge query syntax and separate budget accounting.
- Test the 40-candidate sidecar cap and base-pool isolation.
- Test separate semantic README enrichment and API accounting.
- Test that a sidecar-only evidence-bearing repository reaches assessment without changing the regular shortlist.
- Test overlap when a repository appears in both partitions.
- Test objective evidence discovery without original problem words in the repository.
- Test every derived status and restart reconstruction.
- Test validation without numeric thresholds, including missing mechanisms and invalid citations.
- Verify unvalidated hypotheses never enter final `new_mechanisms`.
- Verify a validated candidate may enter the final top 10 while displacement is measured.
- Run semantic-enabled and semantic-disabled replays and assert base-path identity.
- Test sanitized host bundles, fixed initial requests, transcript ordering, live capture, rate-limit backoff, cassette closure, and private-suite support.
- Run the full unit suite, MCP and Explorer tests, leakage checks, deterministic replay, host replay, no-cannibalization gate, and development discovery gate.

## Release and Documentation

- Synchronize package metadata at `0.6.0`.
- Document the separate base and sidecar budgets.
- Publish capability-gate, API-cost, and no-cannibalization results.
- Update the existing `Muse-shroom v0.6.0 Skill-First Execution Plan.md` in place as the canonical English plan.
- Do not create another plan file.

## Assumptions

- This plan completely replaces the previous plan.
- “Skill-first” means semantic decisions belong to the Agent; it does not mean retrieval isolation requires no code.
- The sidecar increases worst-case search calls from 30 to 34 and assessment candidates from 12 to 14.
- Existing-path capacity and selection remain invariant before final ranking.
- Final-rank displacement is allowed only for validated semantic candidates and must be measured.
- No model-provider API, SQLite migration, larger final result, or reduction of existing budgets will be introduced.
