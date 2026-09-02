# Muse-shroom v0.4.8 Follow-up Validation Report

Date: 2026-09-01

Branch: `experiment`

Status: implemented and validated

## Scope

This follow-up preserves the previously validated v0.4.8 Phase A/B work and applies only three localized fixes identified during post-implementation review:

1. Repair the `template` collision in packaging specificity typing.
2. Complete the `matched_kinds` consumer audit for the new `confirmation` lane.
3. Remove the dead single-use query helper left after reverting Phase C.

Pre-existing unrelated deletions and untracked files were not modified.

## Implemented fixes

### 1. Restored workflow-pattern typing for template terms

Removed `template` and `templates` from `PACKAGING_HEADS` in `src/muse_shroom/boundary.py`.

This restores the existing workflow-pattern behavior for terms such as:

- `decision template`
- `review template`
- `checklist template`

Explicit packaging phrases such as `browser extension` remain classified as `packaging` through `PACKAGING_PHRASES`.

A regression assertion now verifies:

```python
mechanism_specificity("decision template") == "workflow_pattern"
```

An exhaustive intersection check compared `PACKAGING_HEADS` with every `*_TAILS` set in the module. After removing `template` and `templates`, no other overlaps were found.

### 2. Made confirmation-lane routing explicit

The `confirmation` lane now participates in the adjacent shortlist quota in `src/muse_shroom/selection.py`:

```python
if kinds & {"adjacent", "confirmation"} or adjacent >= 35:
    lanes.append("adjacent")
```

This deliberately restores the selection behavior used before confirmation queries received their own lane kind. A repository found only by a confirmation query can therefore compete for the reserved adjacent quota, including when the probe is later rejected.

The remaining consumers were audited as follows:

- `selection.py:nongeneric_query_source`: confirmation paths qualify through their explicit non-generic candidate term. The query kind is not a blanket bypass for generic terms.
- `ranking.py:_compatibility_buckets`: `confirmation` is deliberately not projected into the legacy adjacent presentation bucket. Confirmed mechanisms are represented through Boundary fields.
- `boundary_score.py`: no longer reads `matched_kinds`, as required by Phase A.

A regression test now verifies that a confirmation-only candidate:

- qualifies as a non-generic query source;
- receives the `adjacent` selection lane;
- does not receive the `core` lane solely because of its confirmation origin.

### 3. Removed reverted Phase C residue

The single-use `add_query` closure in `confirmation_queries` was removed. Its dictionary construction is inlined directly into the existing loop. Query behavior and the distinct `lane_kind: "confirmation"` value are unchanged.

## Verification results

### Unit tests

Command:

```text
python -m unittest discover -s tests
```

Result:

```text
Ran 234 tests in 19.779s

OK
```

### Boundary leakage check

Command:

```text
python evaluation/check_boundary_leakage.py
```

Result:

```json
{
  "ok": true,
  "leaks": []
}
```

### Full development and holdout replay

The full replay was run twice using the unchanged `evaluation/cassettes/boundary-v048.json.gz` cassette.

Overall verdict after both runs:

```text
needs_review
```

Deterministic output hashes:

| Artifact | Replay 1 SHA-256 | Replay 2 SHA-256 |
|---|---|---|
| `boundary-verdict.json` | `4F66CF2F0CAA12E2CBCD4962DEC6289DD9011E185E541783249BC0AFCC52F405` | `4F66CF2F0CAA12E2CBCD4962DEC6289DD9011E185E541783249BC0AFCC52F405` |
| `confirmation-analysis.json` | `79D2E75BA16E987A679DD340FDD33E5E0E8EB4C7FAFBA146F8D1BFF9B214F5F3` | `79D2E75BA16E987A679DD340FDD33E5E0E8EB4C7FAFBA146F8D1BFF9B214F5F3` |

The repeated hashes are byte-identical.

## Before-and-after aggregate metrics

The baseline is the validated pre-follow-up v0.4.8 replay in `evaluation/results/v048-final2-replay`.

### Development suite

| Metric | Before | After | Change |
|---|---:|---:|---:|
| `confirmation_precision` | 0.0 | 0.0 | 0.0 |
| `confirmed_meaningful_count` | 0 | 0 | 0 |
| `meaningful_boundary_expansion_share` | 0.125 | 0.125 | 0.0 |
| `unknown_mechanism_review_count` | 6 | 6 | 0 |
| `confirmation_confirmed_count` | 4 | 4 | 0 |

### Holdout suite

| Metric | Before | After | Change |
|---|---:|---:|---:|
| `confirmation_precision` | 0.0 | 0.0 | 0.0 |
| `confirmed_meaningful_count` | 0 | 0 | 0 |
| `meaningful_boundary_expansion_share` | 0.0 | 0.0 | 0.0 |
| `unknown_mechanism_review_count` | 4 | 4 | 0 |
| `confirmation_confirmed_count` | 2 | 2 | 0 |

None of the five requested quality or confirmation-yield metrics changed.

One non-requested aggregate changed: development `median_presented_mechanism_count` moved from 4.5 to 4.0. Restoring confirmation-only repositories to the adjacent quota changed the `phone-distraction` shortlist and removed one presented mechanism from that case. Holdout aggregate metrics were otherwise unchanged.

## Release framing

The change from `fail` to `needs_review` must not be described as a measured quality improvement. It is a reclassification into the pending-human-review bucket.

The accurate release statement is:

> Phase A, covering role and eligibility behavior, is a verified fix backed by deterministic tests. Phase B is verified at the typing layer but has no measured effect on confirmation outcome quality. Confirmation precision remains 0.0, confirmed meaningful count remains 0, and meaningful boundary expansion share remains unchanged across both suites. Confirmation yield, particularly the `same_repo_repetition` failure mode that accounted for six of eight reviewed attempts and worsened during the reverted Phase C experiments, is now the leading open problem.

## Final status

- Three localized follow-up fixes implemented.
- 234 unit tests passed.
- Boundary leakage check passed.
- Development and holdout replay completed twice with deterministic hashes.
- Requested quality and confirmation metrics unchanged.
- Overall replay verdict remains `needs_review`.
- Included with the validated v0.4.8 changes for commit and push.

## Correction — 2026-09-01

The `confirmation_precision` value of 0.0 above is Golden-known precision by
design: it measures confirmations against the frozen
`acceptable_new_mechanisms` recall key, not against post-hoc judgements of the
actual confirmed output. Cross-referencing the six confirmations with the
existing human diagnostic labels identifies at least four as meaningful, so
the corresponding blind precision is at least 4/6 (0.67). The earlier report
therefore does not establish confirmation yield, including
`same_repo_repetition`, as the leading open problem. Yield should be revisited
only after blind confirmation quality is measurable.
