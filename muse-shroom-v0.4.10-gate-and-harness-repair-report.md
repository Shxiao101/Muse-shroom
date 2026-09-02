# Muse-shroom v0.4.10 - Release Gate and Eval Harness Repair Report

Date: 2026-09-02  
Branch: `experiment`  
Status: implemented and verified, including fresh capture and two full offline replays  
Commit: not created

## 1. Outcome

v0.4.10 separates deterministic mechanics evidence from discovery capability evidence.
The deterministic harness now reports cross-domain discovery as `not_measured` instead of
converting an unmeasurable capability into a failing Boolean metric. An overall `pass` remains
impossible until both mechanics and host-in-the-loop discovery pass.

The release also repairs two independent harness defects:

- repositories with near-duplicate, mechanism-anchored supporting excerpts no longer count as
  independent confirmation sources;
- replay activity scores use the cassette `captured_at` timestamp throughout probe selection,
  shortlist selection, and ranking instead of the wall clock.

No commit was created. Pre-existing deletions, unrelated modified files, and unrelated untracked
files were left unchanged.

## 2. Phase 0 - Complete-label v0.4.9 replay

The complete 35-label replay was run before v0.4.10 code changes against:

```console
python evaluation/run_boundary_eval.py replay \
  --cassette evaluation/cassettes/boundary-v048.json.gz \
  --output-dir evaluation/results/v049-complete-labels
```

The evaluator returned exit code 1 because the resulting verdict was `fail`; the replay itself
completed normally.

### Development results

| Metric | Actual |
|---|---:|
| `blind_precision` | 0.75 |
| `blind_meaningful_count` | 3 |
| `blind_labels_applied` | `true` |
| `unknown_mechanism_review_count` | 0 |
| development verdict | `fail` |
| overall verdict | `fail` |

The final `learning-habit | spatial computing = wrong_domain` label exposed the existing failure;
it did not create a regression.

### Golden-known metric comparison

All required Golden-known metrics were unchanged from
`evaluation/results/v048-followup-replay-1`:

| Suite | Metric | Baseline | Complete labels |
|---|---|---:|---:|
| development | `confirmation_precision` | 0.0 | 0.0 |
| development | `confirmed_meaningful_count` | 0 | 0 |
| development | `meaningful_boundary_expansion_share` | 0.125 | 0.125 |
| holdout | `confirmation_precision` | 0.0 | 0.0 |
| holdout | `confirmed_meaningful_count` | 0 | 0 |
| holdout | `meaningful_boundary_expansion_share` | 0.0 | 0.0 |

## 3. Honest gate split

Agentic raw payloads now include:

```json
{
  "policy": "deterministic"
}
```

The evaluator emits case-level and suite-level fields with distinct responsibilities:

- `boundary_quality_passed`: mechanics only;
- `discovery_quality_passed`: evaluated only for `host_in_loop`;
- `cross_mechanism_status`: `discovered`, `absent`, or `not_measured`;
- `mechanics_verdict`: `pass`, `fail`, or `insufficient_data`;
- `discovery_verdict`: `pass`, `fail`, `not_measured`, or `insufficient_data`.

Mechanics covers:

- evidence-backed promotions;
- duplicate query rate at or below 0.5;
- query evolution after the initial search;
- no repetition violations;
- no invalid gain.

Discovery covers the Golden thresholds for mainstream coverage, meaningful new mechanisms, and
required cross-mechanism transfer, but only when a real host Agent is in the loop.

Verdict precedence is now:

1. measured mechanics or discovery failure -> `fail`;
2. mechanics pass with discovery not measured -> `needs_review`;
3. both measured gates pass and no unresolved review remains -> `pass`.

An unknown-review branch can no longer mask a mechanics failure.

### Phase 1 rescore

The v0.4.9 complete-label raw results were rescored with the repaired gate and written to:

`evaluation/results/v0410-phase1-rescore/boundary-verdict.json`

| Suite | Mechanics | Discovery | Suite verdict |
|---|---|---|---|
| development | `fail` | `not_measured` | `fail` |
| holdout | `pass` | `not_measured` | `needs_review` |

Development mechanics passed in seven cases. `learning-habit` alone failed because its labelled
`spatial computing` gain remained invalid in the Phase 1 raw input.

## 4. Near-duplicate confirmation guard

Confirmation now clusters repositories by their mechanism-anchored supporting excerpts before
evaluating multi-repository or transfer-backed support.

Two repository evidence sets are treated as the same source when:

- normalized supporting text is equal; or
- token overlap is at least `2/3`;
- and at least one side is explicitly mechanism-anchored.

The comparison requires the repositories' complete supporting-excerpt sets to match. A shared topic
does not collapse two repositories when one repository also supplies distinct supporting text.

Using the actual v0.4.9 `spatial computing` record produced:

| Field | Before | After |
|---|---|---|
| repository count | 3 | 3 |
| independent source count | 3 by `full_name` | 1 |
| confirmation status | `confirmed` | `rejected` |
| confirmation reason | `cross_domain_mechanism_transfer` | `near_duplicate_repository_support` |

Early terminal rejections now stop further confirmation queries, while `unresolved` candidates may
continue through the existing staged query flow.

## 5. Replay time determinism

New captures freeze `captured_at` before the first request. The timestamp is passed, when supported,
through:

- `SearchEngine` probe selection;
- shortlist scoring;
- `rank_search` activity scoring.

Normal product calls retain wall-clock behavior when no reference time is supplied. The evaluation
worker inspects historical source signatures before passing the new argument, preserving replay of
older Muse-shroom versions.

Two post-fix CI cassette replays produced byte-identical outputs:

| File | SHA-256 |
|---|---|
| `boundary-development-single-pass.raw.json` | `D58C75D1101044FDAF7738EF20C2F813F3CA155E3077C218AE6747E431976C3E` |
| `boundary-development-agentic.raw.json` | `BA5126150D7BD92FC4E8B76AF310CA81B65848976EC847CC883475043CC2617E` |
| `boundary-verdict.json` | `46DC1A6EB02AF15583CA8D8390A1375AFC1F4B795FDB1C1FF29CDDCA754F2402` |
| `confirmation-analysis.json` | `5D44CA43363414F8949B4DE197A034B9DF0D0E2ACBC50D899C6A06C7` |

The CI verdict was:

- mechanics: `pass`;
- discovery: `not_measured`;
- overall: `needs_review`.

## 6. Verification

### Passed checks

```console
python -m unittest discover -s tests
python evaluation/check_boundary_leakage.py
git diff --check
```

Actual results:

- 245 tests passed;
- no Boundary leakage was detected;
- no whitespace errors were reported;
- the near-duplicate regression test passed;
- the shared-topic/distinct-evidence regression test passed;
- ranking and shortlist forced-clock activity tests passed;
- historical source compatibility tests passed;
- two post-fix CI replays were byte-identical for both raw packs and both reports.

## 7. Fresh v0.4.10 capture and full offline acceptance

The unchanged `evaluation/cassettes/boundary-v048.json.gz` still cannot cover the repaired call
graph because it lacks:

```text
latest_release ('walbercarvalho/UI-UX-Antigravity-Skills',)
```

That historical fixture limitation was closed with a new credential-bearing cassette rather than
an invented empty response. The accepted cassette is:

```text
evaluation/cassettes/boundary-v0410.json.gz
captured_at: 2026-09-02T02:14:29.102313+00:00
recorded calls: 1294
SHA-256: 97472DDCFE724D698D87D208058729F053D666B1120BD05EE702DB3EB0E2167D
```

The first fresh capture was interrupted twice by transient GitHub 403 responses. The credential
remained valid, the search-rate endpoint recovered to its full allowance, and the rejected calls
were not written into the cassette. Capture therefore resumed on the same initially empty v0.4.10
cassette with a 3.0-second search interval.

The first offline replay then exposed one unrecorded README request:

```text
readme ('uzh-rpg/event-based_vision_resources',)
```

A completion capture recorded that request without changing the frozen `captured_at` reference
time. Two subsequent fully offline replays completed without cassette misses. Capture, replay 1,
and replay 2 produced byte-identical files:

| File | SHA-256 |
|---|---|
| `boundary-development-single-pass.raw.json` | `91F40DA7AA3152F47AFBFCDB8E8AC989762AADF2B9C310F333FC91EAD1856FD0` |
| `boundary-development-agentic.raw.json` | `E316A9232574E790BD0A1B03A6FEDE65A26BB959BF1BFBD2CE054226B98CE334` |
| `boundary-holdout-single-pass.raw.json` | `F9F66B07504ADFBB492373F8E067D9A1395552DD86C0B245A8C3A80AA3AF6674` |
| `boundary-holdout-agentic.raw.json` | `6AAD0E730690E3D5903F9470A2D5EA0C9C3FC7CC3C9FFFC6947761F816CCDCD5` |
| `boundary-verdict.json` | `BFB9EDD2424DE197B52BCE9FEA3E61CF3484E2CA515466B8CE8B49F7AD7062C8` |
| `confirmation-analysis.json` | `94D39C430B740826DFC276E6C86481BD16E470515209A8E6207CEB05D47F0F54` |

### Final live-data results

| Metric | Predicted | Actual |
|---|---|---|
| `spatial computing` status | `rejected` | `rejected` |
| `spatial computing` reason | `near_duplicate_repository_support` | `near_duplicate_repository_support` |
| `learning-habit.invalid_boundary_gain` | 0 | 0 |
| development mechanics | `pass` | `pass` |
| holdout mechanics | `pass` | `pass` |
| both discovery verdicts | `not_measured` | `not_measured` |
| overall verdict | `needs_review` | `needs_review` |
| development `blind_precision` | 1.0 | 0.667 |
| development `blind_meaningful_count` | 3 | 2 |
| development `confirmation_precision_total` | not previously reported | 1.0 |

The mechanics and verdict predictions were confirmed. The blind-precision prediction was falsified
by live GitHub drift: the confirmed development set was `beat tracking`, `pitch detection`, and
`local first`, producing two blind-meaningful confirmations out of three. Labels were applied
completely (`blind_labels_applied=true`, `unlabelled_unknown_count=0`), so 0.667 is a measured result,
not a partial-label artifact.

**Correction (2026-09-02):** Live GitHub drift changed which mechanisms were confirmed:
`data synchronization` dropped out and `local first` entered the confirmed set. It did not
falsify confirmation quality. The reported 0.667 is a metric-composition artifact because the
`blind_precision` denominator includes all three confirmations while its numerator includes only
the two blind-labelled confirmations. `local first` is a Golden match for the `local-archive`
entry in `personal-knowledge`'s `acceptable_new_mechanisms`, so it contributes through
`confirmed_meaningful_count` instead. Development therefore had three confirmations and zero bad
ones; `confirmation_precision_total` was 3 / 3 = 1.0. The separately auditable
`blind_precision` remains 0.667 and `confirmation_precision` remains 0.333.

### Holdout near-duplicate manual audit

The guard also changed holdout confirmations from 2 confirmed / 12 rejected to 1 confirmed / 13
rejected. Because holdout intentionally has no blind-label path, both new rejections were manually
audited against the captured evidence before acceptance.

| Case | Candidate | Repositories | Manual evidence check | Conclusion |
|---|---|---:|---|---|
| `long-writing-consistency` | `batch generation` | 3 -> 1 independent | Each repository has one support entry and all three candidate excerpts are exactly equal. The complete READMEs have normalized sequence similarity 0.9987-0.9996, token Jaccard 0.9967-0.9984, and 292-293 equal lines out of 294. | copied repository support; rejection is correct |
| `family-digital-preservation` | `selective scanning` | 2 -> 1 independent | The candidate excerpts are exactly equal. Both complete READMEs have the same Git blob SHA (`3b98a76ddfa10fb4958bdb9994402b511180f912`), the same full-text SHA-256, the same 7,079-character length, and all 161 lines equal. | exact repository copy; rejection is correct |

All pairwise `_near_duplicate_support` checks were true, every candidate support set contained one
entry per repository, and the mutual-full-set rule therefore compared the complete available
support set rather than collapsing on a partial phrase match. No silent holdout recall loss was
found in these two cases.

The acceptance gate is therefore closed for deterministic mechanics and replayability. Discovery
remains deliberately unmeasured under the deterministic policy and still requires a separate real
host-in-the-loop evaluation before an overall `pass` can be claimed.

## 8. Files changed for v0.4.10

Core implementation:

- `evaluation/boundary_eval.py`
- `evaluation/cassette.py`
- `evaluation/version_worker.py`
- `src/muse_shroom/analyze.py`
- `src/muse_shroom/confirmation.py`
- `src/muse_shroom/ranking.py`
- `src/muse_shroom/search.py`
- `src/muse_shroom/selection.py`

Tests and documentation:

- `tests/test_boundary_evaluation.py`
- `tests/test_boundary_ranking.py`
- `tests/test_confirmation.py`
- `tests/test_ranking.py`
- `evaluation/README.md`
- `muse-shroom-v0.4.9-measurement-repair-plan.md`

The existing v0.4.9 work also includes `evaluation/run_boundary_eval.py`,
`evaluation/blind-review-labels.json`, and related v0.4.9 documentation changes present in the
uncommitted working tree before this repair.
