# Muse-shroom v0.4.5 Release Validation Report

Date: 2026-08-31

Branch: `experiment`

## Decision

**Do not release v0.4.5 yet.** The two-stage confirmation pipeline is implemented,
tested, and reproducible. It preserves the v0.4.4 Development precision target,
but it misses both absolute recall gates:

| Release gate | Result | Required | Status |
| --- | ---: | ---: | --- |
| Executed iteration cases | 14/14 | >= 12/14 | Pass |
| Retrieval-changing / executed | 19/19 (100%) | >= 90% | Pass |
| Duplicate-only iterations | 0 | 0 | Pass |
| Holdout leakage | 0 | 0 | Pass |
| Development wrong-domain | 0 | <= 1 | Pass |
| Development noise | 0 | 0 | Pass |
| Development synonym | 1 | <= 1 | Pass |
| Development meaningful | 4 | >= 7 | **Fail** |
| Holdout known meaningful gain | 2 | >= 3 | **Fail** |

The generated evaluator verdict is `fail`, which agrees with this manual release
decision.

## Implementation delivered

- Added `direct_promote`, `needs_confirmation`, and `reject` evidence dispositions.
- Added a bounded confirmation queue and candidate-plus-context queries.
- Kept discovery and confirmation evidence separate in the trace.
- Required independent core-use-case support, multi-repository support, or an
  explicitly anchored cross-domain transfer before promotion.
- Prevented same-repository repetition, unrelated confirmation evidence, known
  request mechanisms, surface duplicates, list/changelog evidence, and malformed
  fragments from confirming.
- Kept confirmation queries and counts separate from normal boundary iterations.
- Added confirmation metrics and `confirmation-analysis.json` generation.
- Kept the MCP, Skill, ranking, Agentic policy, Holdout Golden, aliases,
  thresholds, and `DISCOVERY_PHRASE_HINTS` unchanged.
- Updated the package version to `0.4.5`.

## Real validation

Artifacts:

```text
evaluation/cassettes/boundary-v045.json.gz
evaluation/results/boundary-v045-release/
evaluation/results/boundary-v045-release-replay/
```

The v0.4.5 cassette is independent of the v0.4.3 and v0.4.4 cassettes. It
contains 1,662 GitHub calls:

| Call | Count |
| --- | ---: |
| Repository search | 380 |
| README read | 874 |
| Latest release read | 328 |
| Repository read | 52 |
| Fork traversal | 14 |
| Owner repository traversal | 14 |

The compressed cassette is 16,339,631 bytes and contains zero stale responses.
Incremental capture retained successful calls while implementation fixes changed
candidate selection; no baseline cassette was overwritten.

Capture and an independent replay produced identical `boundary-verdict.json` and
`confirmation-analysis.json`. Both Holdout raw files were byte-identical. The two
Development raw pairs differed only in five time-dependent `activity` values;
all candidates, evidence, traces, and decisions were identical.

## Boundary results

| Metric | Development | Holdout | Total |
| --- | ---: | ---: | ---: |
| Cases with executed iterations | 8/8 | 6/6 | 14/14 |
| Planned iterations | 11 | 8 | 19 |
| Executed iterations | 11 | 8 | 19 |
| Retrieval-changing iterations | 11 | 8 | 19 |
| Duplicate-only iterations | 0 | 0 | 0 |
| Raw boundary gain | 5 | 7 | 12 |
| Golden-known meaningful gain | 1 | 2 | 3 |
| Unknown gain | 3 | 5 | 8 |
| Invalid gain | 1 | 0 | 1 |

The Development invalid item is `pomodoro`, which the human review classifies as
a synonym rather than a harmful mechanism.

## Development blind review

| Case | Mechanism | Label | Basis |
| --- | --- | --- | --- |
| ai-music | `beat tracking` | `meaningful` | Directly supports music timing and composition analysis. |
| ai-music | `speech recognition` | `meaningful` | Uses the same blind-review classification as the v0.4.4 precision baseline. |
| phone-distraction | `distraction detection` | `meaningful` | Provides a behavioral signal for detecting phone-use distraction. |

Together with one Golden-known mechanism and the `pomodoro` synonym, Development
has 4 meaningful items out of 5 gains: 80.0% meaningful precision, zero
wrong-domain items, zero noise, and one synonym.

## Confirmation review

| Suite | Candidate | Label |
| --- | --- | --- |
| Development | `pomodoro` | synonym |
| Development | `beat tracking` | meaningful |
| Holdout | `perceptual hashing` | meaningful |
| Holdout | `cohort analysis` | meaningful |
| Holdout | `decision log` | meaningful |
| Holdout | `data deduplication` | meaningful |
| Holdout | `data encryption` | meaningful |

Confirmation produced 6 meaningful mechanisms from 7 confirmations, for 85.7%
human precision. It used 93 queries, or 13.3 queries per confirmed mechanism and
15.5 queries per meaningful confirmation. This cost and the low Development
yield are the primary residual weaknesses.

Two false-positive paths found during validation were fixed before this report:

- unrelated attendance-domain `identity verification` can no longer borrow
  request relevance from a different discovery repository;
- `lock-free deduplication` can no longer be truncated into the malformed
  candidate `free deduplication`.

## Verification

Completed successfully:

```text
python evaluation/check_boundary_leakage.py
  ok=true, leaks=[]

python evaluation/run_boundary_eval.py replay --ci \
  --output-dir evaluation/results/boundary-v045-release-ci
  schema_version=4, verdict=pass

python -m unittest discover -s tests
  209 tests passed

python -m compileall -q src evaluation tests
  passed

git diff --check
  passed
```

## Next step

Do not loosen the v0.4.4 direct-promotion gate. The next cycle should focus on a
higher-yield, still bounded confirmation query strategy and candidate ordering,
then repeat real capture against a new cassette. No Golden, alias, threshold, or
benchmark phrase changes are justified by this result.
