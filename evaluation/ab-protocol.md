# Agent-owned selection A/B protocol

Registered before any A/B result is read, and before `evaluation/ab-requests.json`
contains a single entry. Nothing below may be changed once a need has been captured;
a threshold chosen after seeing results is not a threshold.

## Why this is the release gate

Every fixture under `evaluation/` was written by a language model: the prompts, the
Golden `cross_mechanism_directions`, and the blind-review labels. Agreement with any
of them measures whether one model guesses another model's vocabulary. This comparison
is anchored outside that loop — a person judging real output on their own real needs.

## Arms

Both arms are identical except that the candidate arm has Muse-shroom and its Skill
available and the direct arm does not.

- Same model ID and reasoning configuration.
- Same verbatim request text.
- Fresh context per run. No carry-over between arms, needs, or repetitions.
- The direct arm emits the same structured result shape itself. A human must not
  reformat its prose into that shape: the reformatting would inject the very
  interpretation being measured.
- Seeded schedule randomises arm order and need order.
- Each run records model ID, Muse-shroom revision, Skill component digest, UTC
  timestamp, and the complete configuration.

## Requests

`evaluation/ab-requests.json` holds needs the maintainer actually wanted to run,
verbatim:

```json
{
  "schema_version": 2,
  "collection_status": "awaiting_real_maintainer_needs",
  "requests": [
    {"id": "need-01", "captured_at": "2026-09-04", "text": "<exactly what the maintainer would have typed>"}
  ]
}
```

Nothing else. No `problem_concepts`, no `mechanisms`, no category, no back-filled
structure — any structure added here is interpretation applied to both arms before
either has run. Synthetic or reconstructed needs are not permitted.

## Threshold

**The unit of evidence is the need, not the judgement.**

Each need is run three times per arm. Those repetitions measure Agent variance on the
same question; they are not three independent observations about the tool. Pooling them
would inflate the effective sample roughly threefold.

So each need resolves to one verdict — the arm preferred in a majority of its
repetitions, or `tie` — and the release threshold is stated over needs:

- At least **8 distinct real needs**.
- Muse-shroom wins at least **6 of 8** need-level verdicts.
- Ties count as losses, never as wins.

Report separately, never folded into the verdict:

- **Stability**: how often a need flipped between its repetitions. A need whose verdict
  changes across runs is evidence about Agent variance and must be reported as such
  rather than averaged away.
- **Claim traceability** per arm: for every repository either arm names, whether it
  exists, whether it is archived, and — when a quote is claimed — whether that exact
  quote and source term occur in a source recorded at a repository SHA. This checks
  traceability only. Whether the quote *supports* the capability claim is a judgement
  and is not mechanised.

## Review

Blind and interleaved via `build_blind_pack` in `evaluation/run_ab.py`, which shuffles
the two arms per need and writes `blind-key.json`. Judges rate `relevance`,
`interesting`, `evidence`, `actionability`, and `diversity`, then choose `A`, `B`, or
`tie`. Do not open `blind-key.json` until every rating is final.
