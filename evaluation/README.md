# Muse-shroom blind A/B evaluation

This evaluation checks whether a new version produces a more useful assessment shortlist without treating any repository as a canonical answer. It compares the v0.2.0 baseline at `5cc5621` with the current worktree by default.

## Capture once

Run capture in the host user context so Muse-shroom can read the system credential store and reach GitHub:

```console
python evaluation/run_ab.py capture
```

The recorder runs both versions in isolated source trees. Calls with the same method and arguments reuse exactly the same recorded response; calls unique to one version are captured during the same session. Repository search is paced below GitHub's 30 requests/minute search limit. Authentication failures, rate limits, and transient errors stop capture instead of becoming frozen fixtures.

Generated cassettes and results are deliberately ignored by Git because they contain large snapshots of external README content. The default outputs are:

```text
evaluation/cassettes/ab-v1.json.gz
evaluation/results/baseline.raw.json
evaluation/results/candidate.raw.json
evaluation/results/blind-review.json
evaluation/results/blind-key.json
```

## Replay and review

Replay performs no network calls and fails explicitly if a required API call is absent:

```console
python evaluation/run_ab.py replay
```

Capture and replay each write two review packs from the same raw results:

- `blind-review.json`: natural length (the real shortlist the user would see);
- `blind-review-standard.json`: both lists truncated to 12 rows and projected to the same reviewer-visible fields;
- `blind-cases/manifest.json`: routes reviewers through six-candidate chunks so no single tool read must load the full pack.

Model reviewers should read `blind-cases/manifest.json` and every listed A/B chunk, not the monolithic pack. The standard projection removes internal selection scores, lanes, and discovery paths while retaining repository metadata and README evidence. For every prompt, rate lists A and B from 1 to 5 on `relevance`, `interesting`, `evidence`, `actionability`, and `diversity`, then choose `A`, `B`, or `tie`. Save each reviewer's ratings under a distinct name such as `ratings-grok.json` or `ratings-codex.json`; do not inspect `blind-key.json` until ratings are final. Raw cassette replays belong under `evaluation/results/` (gitignored).

```console
python evaluation/score_ab.py RATINGS.json --key evaluation/results/blind-key.json --output evaluation/results/summary.json
```

The release gate passes when all eight prompts are rated, the candidate wins at least 60%, its median evidence score improves by at least 0.5, and median relevance and diversity do not decrease. Probe repositories may be recorded separately but never affect this calculation.

This v1 harness evaluates the 12-repository assessment shortlist and its evidence, which is the stage changed most heavily in v0.3.3. Final Agent-authored semantic assessments and ranking prose remain a separate human-in-the-loop evaluation.

Raw v0.4 results also include `boundary`, `boundary_delta`, and diagnostic-only `boundary_diagnostics`. Retrieval-pool redundancy and final-presentation redundancy are reported separately, and `redundancy_scope` says whether presentation means `ranking_items`, `selected_for_assessment`, or `candidates`; the compatibility field `mechanism_redundancy` maps to presentation redundancy. Results also include `loop_diagnostics`: iterations used, queries per iteration, new mechanisms per iteration, boundary gain per iteration, duplicate query rate (only `skip_reason=duplicate`), candidate novelty per iteration, stop reason, unexplored directions at stop, and the evidence trace. `version_worker.py` defaults to the historical single-pass A/B behavior; `--agentic` switches it to a deterministic observation-driven policy. The policy can promote only evidence emitted by the current observation and never reads Golden expected directions. Cassette replay must reproduce the same boundary for the same request and recorded responses.

The prompt fixture retains a duplicate `core_concepts` field solely so historical fixture validators can still inspect it. `version_worker.py` detects the checked-out request model: current versions consume the v0.4 fields, while older baseline worktrees receive a generated v0.3 request with problem concepts and mechanisms combined as core concepts.

The probe and shortlist stages cap a single repository owner at two entries. This keeps ecosystems such as a release tool plus its plugins discoverable without allowing one owner to dominate the Agent's review budget.

## Boundary evaluation

Eight development/regression Golden Cases live in
`evaluation/boundary-golden-cases.json`; their matching requests are isolated in
`evaluation/boundary-prompts.json`. Six separately reported holdout cases live
under `evaluation/holdout/`. They define concept IDs and aliases for
mainstream coverage, acceptable new mechanisms, repetition groups, required
cross-mechanism directions, and release thresholds. They deliberately require
neither a particular repository nor an exact output phrase. Holdout expected
terms and aliases must never be copied into production phrase hints, requests,
or the deterministic policy. Run the enforced normalized check with:

```console
python evaluation/check_boundary_leakage.py
```

Capture the complete flow once in a credential-bearing host context, then replay
it fully offline:

```console
python evaluation/run_boundary_eval.py capture
python evaluation/run_boundary_eval.py replay
```

The command writes development and holdout single-pass packs, then runs deep
search, deterministic evidence-backed iterations, fixture-driven Boundary rank,
trace collection, and `boundary_eval.py` for both suites. Fixture assessments
exist only to exercise ranking structure; they do not replace blind semantic
review. The evaluator distinguishes raw, known meaningful, unknown, and invalid
gain. Unknown evidence-backed mechanisms enter a `needs_review` queue instead of
being discarded. Release verdicts are `pass`, `fail`, `needs_review`,
`insufficient_data`, or `leakage_detected`; holdout and development are reported
separately. Golden data is loaded only after retrieval.

A committed synthetic cassette provides a fresh-clone, no-network regression:

```console
python evaluation/run_boundary_eval.py replay --ci
```

Rebuild it only from the deterministic synthetic source with
`python evaluation/build_boundary_ci_fixture.py`. Real GitHub release cassettes
remain under the ignored `evaluation/cassettes/` directory. To score an existing
development pack directly:

```console
python evaluation/boundary_eval.py evaluation/results/boundary/boundary-development-agentic.raw.json
```
