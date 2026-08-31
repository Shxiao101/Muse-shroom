# Muse-shroom v0.4.3 / v0.4.4 / v0.4.5 / v0.4.6 Comparison

Date: 2026-08-31

## Comparison scope

- v0.4.3: recall baseline, `boundary-v043-release`.
- v0.4.4: precision baseline, `boundary-v044-release-r2`.
- v0.4.5: two-stage confirmation architecture baseline, `boundary-v045-release`.
- v0.4.6: bounded priority and progressive confirmation candidate,
  `boundary-v046-release`.
- Development labels use the same blind-review rubric across versions. Golden,
  aliases, thresholds, prompts, and discovery phrase hints were not changed.

## Boundary results

| Metric | v0.4.3 | v0.4.4 r2 | v0.4.5 | v0.4.6 |
| --- | ---: | ---: | ---: | ---: |
| Executed iteration cases | 13/14 | 14/14 | 14/14 | 14/14 |
| Planned / executed / retrieval-changing iterations | 22 / 22 / 22 | 19 / 19 / 19 | 19 / 19 / 19 | 19 / 19 / 19 |
| Duplicate-only iterations | 0 | 0 | 0 | 0 |
| Raw boundary gain | 24 | 9 | 12 | 11 |
| Development known meaningful | 0 | 1 | 1 | 1 |
| Development blind-review meaningful | 5 | 4 | 3 | 5 |
| Development total meaningful | 5 | 5 | 4 | 6 |
| Development wrong-domain | 4 | 0 | 0 | 0 |
| Development noise | 1 | 0 | 0 | 0 |
| Development synonym | 3 | 1 | 1 | 0 |
| Development meaningful precision | 35.7% | 80.0% | 80.0% | 100.0% |
| Holdout known meaningful gain | 3 | 0 | 2 | 0 |
| Holdout leakage | 0 | 0 | 0 | 0 |

The v0.4.6 Development total consists of one Golden-known mechanism and five
blind-review meaningful mechanisms: `beat tracking`, `pitch detection`,
`score generation`, `distraction detection`, and `data synchronization`.
All six Development gains are meaningful under the established rubric.

Holdout has five unknown gains. Human review labels `face recognition`,
`question generation`, `data deduplication`, and `data encryption` meaningful;
`identity verification` is wrong-domain for photo organization. The frozen
Holdout taxonomy recognizes none of these as known meaningful gain.

## Confirmation stage

| Metric | v0.4.5 | v0.4.6 | Change |
| --- | ---: | ---: | ---: |
| Total candidates recorded | 73 | 115 | +42 |
| Attempted candidates | 73 | 33 | -40 (-54.8%) |
| Skipped candidates | 0 | 82 | +82 |
| Confirmed / rejected / unresolved | 7 / 66 / 0 | 6 / 27 / 0 | -1 / -39 / 0 |
| Confirmation queries | 93 | 61 | -32 (-34.4%) |
| Human meaningful confirmations | 6 | 5 | -1 |
| Human confirmation precision | 85.7% | 83.3% | -2.4 pp |
| Queries per confirmed mechanism | 13.3 | 10.2 | -23.3% |
| Queries per meaningful confirmation | 15.5 | 12.2 | -21.3% |

Development confirmation improved from 41 attempts, 51 queries, two confirms,
and one meaningful confirmation in v0.4.5 to 19 attempts, 35 queries, three
confirms, and three meaningful confirmations in v0.4.6. Confirmed yield rose
from 4.9% to 15.8%, while meaningful yield rose from 2.4% to 15.8%.

The bounded priority strategy therefore improves confirmation efficiency and
Development precision without weakening the direct-promotion gate. It does not
meet the absolute release targets: Development meaningful is 6 rather than 7,
Holdout known meaningful is 0 rather than 3, and combined queries per meaningful
confirmation is 12.2 rather than below 10.

## Decision

v0.4.6 is a measurable confirmation-yield improvement over v0.4.5 and exceeds
the v0.4.4 Development precision result, but the release candidate is **not
accepted** because the recall and absolute confirmation-cost gates fail.
