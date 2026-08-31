# Muse-shroom v0.4.3 Implementation Report

Date: 2026-08-31

Branch: `experiment`
Baseline: v0.4.2 failure analysis (`4/14` promotable initial evidence, `4/14` reported agentic coverage, including three duplicate-only iterations)

## Scope

Implemented the v0.4.3 Boundary pipeline repair:

```text
Evidence
-> Promotable Direction
-> Effective Iteration
-> Canonical New Mechanism
```

The following surfaces were deliberately not changed:

- Holdout Golden cases, aliases, and release thresholds
- Query Engine architecture
- Relationship defaults
- Ranking algorithm
- MCP, Skill, Explorer, and Personal Boundary behavior
- `DISCOVERY_PHRASE_HINTS` benchmark vocabulary

`ranking.py` only receives the confirmed-direction session state required to rebuild the same Boundary contract after ranking. No scoring or ordering rule was changed.

## Implemented Changes

### Agentic policy and direction confirmation

- Lexical matches no longer confirm that an exploration direction was explored.
- A direction is confirmed only after an iteration actually targets it.
- The deterministic evaluation policy seeds its used-direction set from initial executed queries.
- Evidence priority is `candidate_mechanism`, then `cross_domain_direction`, then an unexecuted request direction.
- Duplicate evidence is skipped and the next eligible evidence item is tried.
- Planned, executed, and retrieval-changing iterations are reported separately.

### Evidence discovery

- Phrase extraction now respects sentences, punctuation, table cells, command-list boundaries, and emoji-separated use cases.
- Governance/philosophy boilerplate is no longer a curated discovery source.
- Feature, use-case, overview, description, relationship, and topic sources have deterministic priorities.
- Generic morphology can identify operation-like mechanisms without adding benchmark phrases.
- Conjunction fragments, promotional/meta-document fragments, malformed numeric/Markdown terms, and request-mechanism overlaps are filtered.
- Relevance is local to the evidence segment and limited to a 12-token direct-match window.
- Weak query-path relevance cannot flow from one unrelated README section to another.
- Evidence excerpts are centered on the proposed term so the trace remains inspectable.
- Compound forms such as `decision_log_template` can yield `decision log` without a phrase hint.

### Mechanism normalization

- Boundary gain uses canonical mechanism identities.
- Surface terms remain available in `new_mechanism_surfaces` and `new_presented_mechanism_surfaces`.
- Normalization trace records `surface_term`, `canonical_term`, and `normalization_reason`.
- Containment normalization requires shared repository evidence; unrelated mechanisms are not merged.

### Evaluation output

- Boundary evaluation schema is now version 4.
- `agentic_case_count` uses executed iterations rather than recorded iteration count.
- Aggregate output includes planned, executed, retrieval-changing, and duplicate-only counts.
- Development output includes a Golden-blind unknown review queue with the approved labels.
- Holdout output does not expose or generate that review queue.
- Package and README version are `0.4.3`.

## Offline Cassette Analysis

The existing real GitHub cassette was replayed without network access for both development and holdout single-pass suites.

| Measure | v0.4.2 baseline | v0.4.3 offline result |
| --- | ---: | ---: |
| Promotable initial evidence cases | 4/14 | 10/14 |
| Policy-planned evidence directions | not separated | 10/14 |
| Duplicate-only iterations | 3 reported in failure analysis | not measurable with the old real cassette |
| Executed iteration cases | 4/14 reported, 4 cases actually had queries | not measurable with the old real cassette |
| Retrieval-changing iterations | 4/4 actually executed cases | not measurable with the old real cassette |

The first promotable term per case was:

| Suite | Case | First promotable term |
| --- | --- | --- |
| Development | focus | none |
| Development | codex-overthinking | browser automation |
| Development | learning-habit | mastery tracking |
| Development | ai-music | none |
| Development | phone-distraction | digital wellbeing |
| Development | code-review | none |
| Development | long-project-motivation | dot timeline |
| Development | personal-knowledge | SEO enrichment |
| Holdout | photo-organization | perceptual hashing |
| Holdout | meeting-efficiency | action items (followed by decision log) |
| Holdout | indie-churn | cohort analysis |
| Holdout | remote-information-loss | home automation |
| Holdout | long-writing-consistency | none |
| Holdout | family-digital-preservation | data deduplication |

`browser automation`, `SEO enrichment`, and `home automation` remain blind-review candidates. They are evidence-backed and well-formed, but this offline check does not claim that they are meaningful for the request. Precision was preferred over raising coverage to 14/14: removing the assessment gate did reach 14/14 but also promoted obvious noise such as generic automation, client presentation, and unrelated detection terms, so that approach was rejected.

## Validation

Passed:

```text
python evaluation/check_boundary_leakage.py
  ok, 0 leaks

python evaluation/run_boundary_eval.py replay --ci
  pass, schema_version=4
  planned=1, executed=1, duplicate-only=0

python -m unittest discover -s tests
  193 tests passed

python -m compileall -q src evaluation tests
  passed

git diff --check
  passed
```

The focused Boundary/evaluation suite contains 59 passing tests, including lexical-versus-confirmed direction state, duplicate policy fallback, phrase boundaries, local relevance, compound extraction, canonical gain, blind unknown review, and iteration metric separation.

## Real Agentic Replay Limitation

The complete real cassette replay was attempted and stopped at the first newly generated v0.4.3 query:

```text
cassette miss in replay mode:
search_repositories(
  '"scope correction" in:name,description,topics,readme is:public archived:false',
  10,
  'stars'
)
```

This is expected because `boundary-v2.json.gz` predates the new evidence-driven query choices. The implementation does not suppress cassette misses and no new GitHub data was captured. Therefore the real `executed_iteration_count`, `retrieval_changing_iteration_count`, duplicate-only count, and final meaningful/unknown gains must be revalidated with a newly captured credential-bearing cassette before release.

## Release Assessment

The implementation and deterministic regression coverage are complete. The v0.4.3 extraction target is met in offline single-pass replay (`10/14` promotable cases versus `4/14` baseline), with zero Holdout leakage and no threshold or Golden changes.

Release acceptance is still pending one fresh real capture and blind review. The old cassette cannot establish the required real executed-iteration coverage, retrieval changes, or the semantic quality of the three flagged terms.
