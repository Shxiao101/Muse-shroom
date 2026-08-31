# Muse-shroom v0.4.6 Release Validation Report

Date: 2026-08-31

Branch: `experiment`

## Decision

**Do not release v0.4.6 yet.** Candidate prioritization, bounded confirmation,
progressive queries, and early stopping are implemented and reproducible. They
substantially improve v0.4.5 confirmation yield, but three absolute release
gates remain unmet.

| Release gate | Result | Required | Status |
| --- | ---: | ---: | --- |
| Executed iteration cases | 14/14 | >= 12/14 | Pass |
| Retrieval-changing / executed | 19/19 (100%) | >= 90% | Pass |
| Duplicate-only iterations | 0 | 0 | Pass |
| Holdout leakage | 0 | 0 | Pass |
| Development wrong-domain | 0 | <= 1 | Pass |
| Development noise | 0 | 0 | Pass |
| Development synonym | 0 | <= 1 | Pass |
| Development meaningful | 6 | >= 7 | **Fail** |
| Holdout known meaningful gain | 0 | >= 3 | **Fail** |
| Human confirmation precision | 5/6 (83.3%) | >= 80% | Pass |
| Queries per meaningful confirmation | 61/5 (12.2) | < 10 | **Fail** |

The generated evaluator verdict is `fail`, which agrees with the manual release
decision.

## Implementation delivered

- Added independent `novelty_score` and `confirmability_score` dimensions.
- Added `confirmation_priority_score` and traceable
  `confirmation_priority_reason` using request relevance, specificity, evidence
  and source quality, core-use-case support, repository independence, novelty,
  and transfer plausibility.
- Ordered the confirmation queue by priority and limited each invocation to two
  attempts with a case-level maximum of three.
- Added pre-confirmation canonical, parent/child, surface, and conservative
  same-repository core-phrase deduplication.
- Recorded budget and duplicate omissions as `skipped_budget` and
  `skipped_duplicate`; neither is counted as rejected or attempted.
- Replaced eager query portfolios with problem, observed-anchor, and optional
  high-priority seed stages, evaluating evidence and stopping after each query.
- Kept confirmation query counts separate from normal Agentic iteration counts.
- Added attempted, skipped, budget, yield, and query-cost metrics to the
  evaluator and `confirmation-analysis.json`.
- Kept the v0.4.4 direct-promotion gate and v0.4.5 confirmation contract.
- Did not change Golden, aliases, thresholds, prompts,
  `DISCOVERY_PHRASE_HINTS`, Ranking, MCP, Skill, normal Agentic iteration count,
  or broad relationship traversal.
- Updated the package version to `0.4.6`.

## Real validation

Artifacts:

```text
evaluation/cassettes/boundary-v046.json.gz
evaluation/results/boundary-v046-release/
evaluation/results/boundary-v046-release-replay/
```

The v0.4.6 cassette contains 1,316 GitHub calls and zero stale responses:

| Call | Count |
| --- | ---: |
| Repository search | 261 |
| README read | 716 |
| Latest release read | 259 |
| Repository read | 52 |
| Fork traversal | 14 |
| Owner repository traversal | 14 |

The compressed cassette is 14,970,769 bytes. The first online capture hit one
GitHub read timeout after five Development single-pass cases; successful calls
had already been saved and were safely reused. A later independent replay found
one missing relationship call, which was captured and followed by a complete
network-free replay with no cassette misses.

Capture and final replay produced byte-identical `boundary-verdict.json` and
`confirmation-analysis.json`. Holdout agentic raw output was byte-identical.
Development agentic raw output differed only in one time-dependent `activity`
value (`7.57` versus `7.43`); candidates, evidence, confirmations, metrics, and
verdicts were identical.

## Boundary results

| Metric | Development | Holdout | Total |
| --- | ---: | ---: | ---: |
| Cases with executed iterations | 8/8 | 6/6 | 14/14 |
| Planned iterations | 11 | 8 | 19 |
| Executed iterations | 11 | 8 | 19 |
| Retrieval-changing iterations | 11 | 8 | 19 |
| Duplicate-only iterations | 0 | 0 | 0 |
| Raw boundary gain | 6 | 5 | 11 |
| Golden-known meaningful gain | 1 | 0 | 1 |
| Unknown gain | 5 | 5 | 10 |
| Invalid gain | 0 | 0 | 0 |

## Blind review

| Suite | Case | Mechanism | Label | Basis |
| --- | --- | --- | --- | --- |
| Development | ai-music | `beat tracking` | meaningful | Adds musical timing analysis to the creation workflow. |
| Development | ai-music | `pitch detection` | meaningful | Converts hummed or recorded pitch into score and music input. |
| Development | ai-music | `score generation` | meaningful | Produces editable score output from audio or generated music. |
| Development | phone-distraction | `distraction detection` | meaningful | Detects phone-use distraction as a behavioral signal. |
| Development | personal-knowledge | `data synchronization` | meaningful | Keeps a personal knowledge base consistent across devices. |
| Holdout | photo-organization | `face recognition` | meaningful | Supports grouping and retrieving personal photos by person. |
| Holdout | photo-organization | `identity verification` | wrong_domain | Verifies access or identity; it does not organize the archive. |
| Holdout | long-writing-consistency | `question generation` | meaningful | Surfaces missing story facts and consistency questions. |
| Holdout | family-digital-preservation | `data deduplication` | meaningful | Reduces redundant archive storage while retaining content. |
| Holdout | family-digital-preservation | `data encryption` | meaningful | Protects preserved family data at rest and in backup. |

Development therefore has six meaningful gains out of six, for 100.0% human
meaningful precision, zero wrong-domain, zero noise, and zero synonym items.
It improves on v0.4.5's four meaningful gains but remains one below the release
gate.

## Confirmation review

| Suite | Candidate | Label | Queries |
| --- | --- | --- | ---: |
| Development | `beat tracking` | meaningful | 1 |
| Development | `pitch detection` | meaningful | 1 |
| Development | `data synchronization` | meaningful | 1 |
| Holdout | `identity verification` | wrong_domain | 1 |
| Holdout | `data deduplication` | meaningful | 1 |
| Holdout | `data encryption` | meaningful | 1 |

| Metric | Development | Holdout | Total |
| --- | ---: | ---: | ---: |
| Candidates total | 65 | 50 | 115 |
| Attempted | 19 | 14 | 33 |
| Skipped | 46 | 36 | 82 |
| Confirmed / rejected / unresolved | 3 / 16 / 0 | 3 / 11 / 0 | 6 / 27 / 0 |
| Confirmation queries | 35 | 26 | 61 |
| Human meaningful confirmations | 3 | 2 | 5 |
| Human confirmation precision | 100.0% | 66.7% | 83.3% |
| Queries per confirmed mechanism | 11.7 | 8.7 | 10.2 |
| Queries per meaningful confirmation | 11.7 | 13.0 | 12.2 |

Compared with v0.4.5, attempts fell from 73 to 33 and queries from 93 to 61.
Development confirmed yield rose from 4.9% to 15.8%, and Development meaningful
yield rose from 2.4% to 15.8%. The efficiency gain is real, but the absolute
queries-per-meaningful target remains unmet.

## Required questions

1. **Did Development confirmation yield significantly improve?** Yes. Confirmed
   yield increased 3.2x, and meaningful yield increased 6.5x over v0.4.5.
2. **Did confirmation query count fall?** Yes. Total queries fell from 93 to 61;
   Development fell from 51 to 35.
3. **Are queries per meaningful below 10?** No. The combined result is 12.2 and
   Development is 11.7.
4. **Did Development meaningful recover to at least 7?** No. It reached 6.
5. **Did Holdout known meaningful recover to at least 3?** No. It is 0.
6. **Is human confirmation precision at least 80%?** Yes, 83.3% combined.
7. **Does v0.4.6 beat v0.4.4 precision and v0.4.5 efficiency?** Numerically yes:
   Development precision is 100.0% versus 80.0%, and queries per meaningful are
   12.2 versus 15.5. This does not override the failed absolute gates.

## Verification

Completed successfully:

```text
python evaluation/check_boundary_leakage.py
  ok=true, leaks=[]

python evaluation/run_boundary_eval.py replay --ci \
  --output-dir evaluation/results/boundary-v046-release-ci
  schema_version=4, verdict=pass

python -m unittest discover -s tests
  216 tests passed

python -m compileall -q src evaluation tests
  passed

git diff --check
  passed
```

## Next step

Keep the direct-promotion and independent-evidence gates. The remaining issue is
not query volume alone: priority still spends attempts on high-scoring but
weakly transferable candidates, while Holdout known recall remains unstable.
The next cycle should diagnose priority calibration on non-Golden evidence and
the `identity verification` adjacency failure without adding benchmark phrases
or loosening confirmation.
