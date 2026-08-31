# Muse-shroom v0.4.3 / v0.4.4 / v0.4.5 Comparison

Date: 2026-08-31

## Comparison scope

- v0.4.3: recall baseline, `boundary-v043-release`.
- v0.4.4: precision baseline, `boundary-v044-release-r2`.
- v0.4.5: final two-stage candidate, `boundary-v045-release`.
- Development labels use the same blind-review rubric across versions. Holdout
  Golden and aliases were not consulted by production code and were not changed.

## Results

| Metric | v0.4.3 | v0.4.4 r2 | v0.4.5 |
| --- | ---: | ---: | ---: |
| Executed iteration cases | 13/14 | 14/14 | 14/14 |
| Planned / executed / retrieval-changing iterations | 22 / 22 / 22 | 19 / 19 / 19 | 19 / 19 / 19 |
| Duplicate-only iterations | 0 | 0 | 0 |
| Raw boundary gain | 24 | 9 | 12 |
| Development known meaningful | 0 | 1 | 1 |
| Development blind-review meaningful | 5 | 4 | 3 |
| Development total meaningful | 5 | 5 | 4 |
| Development wrong-domain | 4 | 0 | 0 |
| Development noise | 1 | 0 | 0 |
| Development synonym | 3 | 1 | 1 |
| Development meaningful precision | 35.7% | 80.0% | 80.0% |
| Holdout known meaningful gain | 3 | 0 | 2 |
| Holdout leakage | 0 | 0 | 0 |

The v0.4.5 Development total contains one Golden-known mechanism, three blind
meaningful mechanisms (`beat tracking`, `speech recognition`, and
`distraction detection`), and one synonym (`pomodoro`). Its 4/5 meaningful
precision therefore matches the v0.4.4 precision baseline.

## Confirmation stage

| Metric | Development | Holdout | Total |
| --- | ---: | ---: | ---: |
| Planned / executed candidates | 41 / 41 | 32 / 32 | 73 / 73 |
| Confirmed / rejected / unresolved | 2 / 39 / 0 | 5 / 27 / 0 | 7 / 66 / 0 |
| Confirmation queries | 51 | 42 | 93 |
| Human meaningful | 1 | 5 | 6 |
| Wrong-domain / noise / synonym | 0 / 0 / 1 | 0 / 0 / 0 | 0 / 0 / 1 |
| Human confirmation precision | 50.0% | 100.0% | 85.7% |
| Queries per confirmed mechanism | 25.5 | 8.4 | 13.3 |
| Queries per meaningful confirmation | 51.0 | 8.4 | 15.5 |

Confirmed meaningful mechanisms are `beat tracking`, `perceptual hashing`,
`cohort analysis`, `decision log`, `data deduplication`, and `data encryption`.
`pomodoro` is a surface synonym of the existing focus-timer mechanism.

Automatic confirmation precision is lower than the human figure because it
only recognizes frozen Golden-known terms. It identifies two of the five
Holdout confirmations as known meaningful; the other three remain unknown to
the frozen taxonomy but have direct evidence and passed blind human review.

## Interpretation

v0.4.5 keeps v0.4.4 precision while recovering three raw gains and two
Holdout-known gains. It does not recover the v0.4.3 recall baseline: Development
meaningful is 4 rather than the required 7, and Holdout known meaningful is 2
rather than the required 3.

The confirmation stage itself is precise but expensive. The next improvement
should increase the yield of candidate-to-problem confirmation without
weakening direct promotion, changing the frozen benchmark taxonomy, or adding
benchmark-specific query phrases.

## Decision

The two-stage architecture is validated, but v0.4.5 does not satisfy its release
recall gates. Keep it as a release candidate and do not mark the v0.4.5 release
validation as passed.
