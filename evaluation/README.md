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

Raw v0.4 results also include `boundary`, `boundary_delta`, and diagnostic-only `boundary_diagnostics`. Retrieval-pool redundancy and final-presentation redundancy are reported separately, and `redundancy_scope` says whether presentation means `ranking_items`, `selected_for_assessment`, or `candidates`; the compatibility field `mechanism_redundancy` maps to presentation redundancy. Results also include `loop_diagnostics`: planned, executed, and retrieval-changing iteration counts; queries per iteration; new mechanisms per iteration; boundary gain per iteration; duplicate query rate (only `skip_reason=duplicate`); candidate novelty per iteration; stop reason; unexplored directions at stop; and the evidence trace. A planned iteration is recorded policy intent, an executed iteration contains at least one query, and a retrieval-changing iteration is executed and produces new candidates or mechanisms. `version_worker.py` defaults to the historical single-pass A/B behavior; `--agentic` switches it to a deterministic observation-driven policy. The policy can promote only evidence emitted by the current observation and never reads Golden expected directions. Cassette replay must reproduce the same boundary for the same request and recorded responses.

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

Host-in-the-loop discovery now starts with an isolated `prepare / collect /
summarize` recording workflow. `prepare` writes anonymous cases, Agent
instructions, and an MCP configuration into a fresh bundle while keeping the
case map and Golden data outside it. `collect` is the stdio MCP server itself:
the host calls the normal Muse-shroom tools and the proxy records those calls
and GitHub responses as they happen. There is no handwritten transcript input.

```console
python evaluation/host_eval.py prepare --suite development --bundle evaluation/host-dev-bundle
# Configure the host with evaluation/host-dev-bundle/mcp.json and follow HOST-RUN.md.
# The generated MCP command runs:
python evaluation/host_eval.py collect --bundle evaluation/host-dev-bundle --attempt-root evaluation/results/host-development
python evaluation/host_eval.py summarize --attempt evaluation/results/host-development/attempt-01 --mapping evaluation/host-dev-bundle.case-map.json --suite development
```

Each capture gets a new `attempt-NN` directory. A summarized attempt is sealed
and cannot be overwritten; a later replay or scoring fix can reuse its cassette
without another live Agent run.

An MCP client allocates an attempt whenever it launches or relaunches the
recorder, including launches the host never uses, so `attempt-NN` numbers do not
correspond one-to-one with evaluation runs. An attempt that recorded no host tool
call holds a start event and nothing else — no host decision, no GitHub response,
no result. Governance treats those as an absence of measurement: they are neither
classified nor taken as the newest attempt. Attempts that did record calls are
never skipped, so a later failure still cannot be bypassed by an older pass. Pass
the real attempt path to `summarize`; do not assume `attempt-01`.

MCP clients normally terminate a stdio server rather than shutting it down, so
`attempt.close()` often never runs and the attempt carries no close event.
Termination is the ordinary path, not a defect: the cassette is saved after every
recorded call, so a terminated capture still holds complete artifacts up to its
last call. `summarize` therefore seals a capture with no close event once the
attempt has been quiet for `CAPTURE_QUIESCE_SECONDS` (60s), and records
`close_kind` as `graceful`, `terminated`, or `failed`. Close the host and its MCP
server before summarizing; the quiet period is what prevents sealing a live
capture.

Complete the development shakedown before preparing the six-case holdout bundle.
Development is leaked and always `release_eligible: false`.

### The development shakedown preflight

Holdout `prepare` and `collect` both run the same preflight against the **newest**
sealed development attempt. An older attempt is never used as a fallback, so a
shakedown that regressed cannot be bypassed. Expected case counts come from that
attempt's manifest; no suite size is hard-coded.

The preflight checks facts about the capture, never what it found:

| check | rule |
|---|---|
| attempt is sealed | `manifest.json` and `raw-summary.json` both present |
| identity agreement | attempt, suite, bundle, component and cassette digests match between manifest and summary |
| capture completed | classified `capture_complete`, closed, no diagnostic failures, all prepared cases run |
| rate denominator | `host_hypothesis_proposal_case_rate.total` equals the prepared case count |
| `agent_visible_component_digest` | digest of `src/` + `skills/` equals the shakedown's |

**No rate gates a capture.** `host_hypothesis_proposal_case_rate` is reported for a
human to read and nothing else. Gating on agreement with a model-authored answer
key would measure whether one model guesses another's vocabulary, so those gates,
their ratios, and the acknowledgement flow were removed. A shakedown is a readiness
report, not a permission.

The component digest binds a shakedown to the Skill revision it measured. The
whole-bundle digest cannot do this, because development and holdout bundles
necessarily differ in `cases.json`, `mcp.json`, and `HOST-RUN.md`. Revising the
Skill after a shakedown invalidates it; re-run the shakedown first.

```console
python evaluation/host_eval.py prepare --suite holdout --bundle evaluation/host-bundle   --development-root evaluation/results/host-development
# Configure the host with evaluation/host-bundle/mcp.json and follow HOST-RUN.md.
# The generated MCP command carries --development-root.
python evaluation/host_eval.py summarize --attempt evaluation/results/host-holdout/attempt-NN --mapping evaluation/host-bundle.case-map.json --suite holdout
```

`collect` re-runs the whole preflight before allocating `attempt-NN`, so a
development attempt sealed as incomplete between `prepare` and `collect` still
blocks the capture.

### Capture classifications

Summarization classifies a sealed attempt by whether it completed, never by what it
found. Outcome classifications keyed to an answer key were removed along with the
gates that consumed them.

- `capture_complete` — the recorder produced intact artifacts. Case rows are included.
- `capture_incomplete` — an infrastructure, transcript, or pacing failure. Case rows
  are omitted, and this is the only classification that permits recapturing the same
  holdout suite: a capture that produced intact artifacts has been observed, and
  re-running those cases would test a revised system against known results.

### Pipeline telemetry

`summarize` reports one suite-independent chain per case and in aggregate:
hypothesis → executed query → evidence found → selected by the Agent → displayed.
Every link is a fact about the run, with no answer key involved. `pipeline_rates`
carries `executed_query`, `matching_evidence`, `agent_selected`, and `presented`.

`case_level_summary_included: false` suppresses case rows from the summary. It is
**summary suppression, not an information barrier**: `transcript.jsonl`, the
cassette, and the external case map all remain on disk, and diagnosing a
`capture_incomplete` attempt requires reading them. Holdout independence still
rests on not using raw case-specific results to tune an allowed retry.

The release decision does not come from this recorder. It comes from the matched
blind A/B in `evaluation/ab-protocol.md`, which compares the same Agent and
configuration with and without Muse-shroom on real recorded needs. Nothing in a
shakedown or holdout summary is a release claim. The four small
publication artifacts — `manifest.json`, `raw-summary.json`, `leakage.json`, and
`score.json` — are published under `evaluation/evidence/`, which is deliberately
outside the ignored `evaluation/results/` capture tree. The multi-megabyte cassette
stays ignored; its digest in the manifest identifies it.

Capture the complete deterministic flow once in a credential-bearing host context, then replay
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

Agentic raw payloads declare their evaluation `policy`. The current replay
worker uses `deterministic`: it can promote only terms already present in the
observation, so it cannot measure cross-domain discovery capability. Under this
policy, `cross_mechanism_status` and `discovery_verdict` are `not_measured`;
`cross_mechanism_discovery` and case-level `discovery_quality_passed` are
`null`. Golden and blind gain counts remain numeric diagnostic classifications
for comparison with earlier releases, but they do not make the discovery gate
pass.

`boundary_quality_passed` and `mechanics_verdict` cover only reproducible
mechanics: evidence-backed additions, query evolution, duplicate-query rate,
presentation repetition, and invalid gain. A `host_in_loop` payload additionally
evaluates `discovery_quality_passed` from meaningful gain, cross-mechanism
transfer, and mainstream coverage. The overall verdict is `fail` when either
measured gate fails, `needs_review` when mechanics pass but discovery is not
measured, and `pass` only when both measured gates pass. An unmeasured discovery
gate can never produce `pass`.

Development summaries also include `blind_unknown_review`. Each item contains
only the request, proposed mechanism, supporting evidence and repositories, its
iteration source, and a reviewer label slot. Reviewers may assign
`meaningful`, `noise`, `synonym`, `too_generic`, `wrong_domain`, or
`insufficient_evidence`. Golden expectations are excluded from this queue.

Development-only post-hoc labels live in `evaluation/blind-review-labels.json`
under casefolded `suite|case_id|mechanism` keys. The evaluator applies them only
when every unknown mechanism in the entire development run has a label. Partial
files are still shown in `blind_unknown_review` so reviewers can see completed
and missing work, but they do not change scoring, gain classification, or the
verdict. The three confirmation precision metrics share the confirmed count as
their denominator but keep their sources explicit:

- `confirmation_precision`: Golden-known meaningful confirmations / confirmed
- `blind_precision`: blind-labelled meaningful confirmations / confirmed
- `confirmation_precision_total`: both sources / confirmed

The first two remain separately auditable and can each understate quality when
read alone. Read `confirmation_precision_total` for "how good were the
confirmations". It is `null` when there are no confirmations; unlike
`blind_precision`, it remains computable from the Golden-known term when blind
labels were not applied. `blind_precision` is `null` until
`blind_labels_applied` is true. Labels never feed `acceptable_new_mechanisms`,
Golden aliases, or `DISCOVERY_PHRASE_HINTS`.

`run_boundary_eval.py` loads that committed file by default when it exists. Use
`--labels PATH` to select another validated development label file, or
`--no-labels` to produce an unlabeled baseline replay.

Holdout summaries never expose or generate this review queue and must not feed
production phrase hints, aliases, or Golden updates.

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
