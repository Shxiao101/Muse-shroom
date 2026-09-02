# Muse-shroom v0.4.10 — Repair Plan: Release Gate and Eval Harness

Date: 2026-09-01
Branch: `experiment` (v0.4.9 implemented, uncommitted)
Status: plan only — no code changed, no tests run.
Audience: implementing agent (Codex).

---

## 0. Context

v0.4.9 made confirmation quality measurable and, in doing so, removed a mask. With the final label
now supplied (`development|learning-habit|spatial computing` → `wrong_domain`, 35 labels total), the
complete-label replay will drop the development suite from `needs_review` to `fail`.

**That `fail` is correct and should not be treated as a regression.** It exposes a gate that has never
been satisfiable. This release makes the gate honest.

### The finding

`boundary_quality_passed` is 0/8 development and 0/6 holdout in **every** recorded release
(`boundary-v046-release`, `v048-followup-replay-1`, `v049-*`). `cross_mechanism_discovery` has never
returned `True` in any case, in any suite, in any release. `needs_review` was masking this because
the `unknown_count` branch short-circuits ahead of the `fail` branch
(`evaluation/boundary_eval.py:418-424`).

Two independent blockers, neither of which labelling can touch:

1. **`require_cross_mechanism: True` on all 8 development cases**, and `cross_mechanism_discovery`
   is `False` on all 8.
2. **4 of 8 development cases produce zero later-step mechanisms**, so `meaningful_gain >= 1` is
   unreachable for them regardless of scoring.

### Why: the gate asks the harness for something it cannot produce

The eval has **no LLM host in the loop**. `evaluation/version_worker.py:251` drives every agentic run
through `deterministic_hypothesis` (:65-110), which — as `evaluation/README.md:49` states — *"can
promote only evidence emitted by the current observation."* It selects a promotable discovered term,
or falls back to an unexplored direction from the request itself.

The observed signals confirm this exactly:

| case | cross-directions the gate wants | signals the harness actually produced |
|---|---|---|
| focus | digital wellbeing, biofeedback | attention management |
| codex-overthinking | implementation minimalism, decision hygiene | agent workflow |
| learning-habit | behavior design, accountability | learning workflow, spatial computing |
| ai-music | creative coding, tangible interface | beat tracking, music workflow, pitch detection, score generation |
| phone-distraction | digital minimalism, commitment device | attention management, distraction detection |
| code-review | decision record, change impact | review workflow |
| long-project-motivation | progress visualization, behavioral economics | project workflow |
| personal-knowledge | memory timeline, sensemaking | data synchronization, knowledge workflow, local first |

The wanted terms are cross-domain concepts. What comes out is same-domain refinement
(`beat tracking` for a music query) plus the request's own `exploration_directions` echoing back as
`<domain> workflow`. A policy that can only promote terms already extracted from retrieved READMEs
**cannot by construction** produce a cross-domain leap.

So the gate is unsatisfiable by construction, not by capability. It has been measuring the harness's
hypothesis policy and reporting the result as a product verdict.

### What this does and does not say about semantic leap

It does **not** establish that the product cannot do cross-domain discovery — the benchmark never
puts a real host Agent in the loop, so it has never tested it.

It does mean the `chat2.md` question is genuinely **open again**, and that the v0.4.7 diagnosis's
dismissal of it ("the semantic-leap motivating assumption is false") rested on three ad-hoc runs
whose failures were downstream bugs — now fixed in v0.4.8. With those fixed, there is still no
evidence either way, because no instrument exists to gather it. Building the instrument is in scope
here. Building semantic leap is not.

---

## 1. Design principle

The eval currently produces one verdict from two different kinds of evidence. Split them, exactly as
v0.4.9 split blind precision from Golden-known precision:

- **Mechanics** — evidence-backed promotions, duplicate query rate, repetition violations, invalid
  gain, query evolution. Fully determined by the deterministic harness. **Gated; must pass.**
- **Discovery capability** — meaningful new mechanisms, cross-domain transfer. Requires a real host
  Agent. **Cannot be measured by the current harness; must report `not_measured`, never `False`.**

A metric the harness cannot produce must not read as a failing metric. That is the whole bug.

---

## 2. Changes

### Phase 0 — Finish v0.4.9 (do first, report before touching anything else)

Run the complete-label replay now that all 35 labels exist. Record and report:

- development `blind_precision`, `blind_meaningful_count`, `blind_labels_applied: true`
- `unknown_mechanism_review_count` → 0 for development
- development verdict → expected `fail`; overall → `fail`
- confirm all Golden-known metrics (`confirmation_precision`, `confirmed_meaningful_count`,
  `meaningful_boundary_expansion_share`) are unchanged in **both** suites

Do not adjust anything to avoid the `fail`. Report it as the expected, correct outcome.

### Phase 1 — Make the gate honest (`evaluation/boundary_eval.py`)

- Add a `policy` field to the summary, sourced from the raw payload, distinguishing
  `deterministic` from `host_in_loop`. `version_worker.py` already knows which mode it ran
  (`--agentic`); thread that through into the raw results so `summarize` can read it.
- Under `deterministic` policy, `cross_mechanism_discovery` and `meaningful_gain` become
  **`not_measured`**, not `False`/`0`. Introduce `cross_mechanism_status` with values
  `discovered | absent | not_measured`.
- Split the verdict. `boundary_quality_passed` keeps only the mechanics conditions
  (`evidence_backed_promotions`, `duplicate_query_rate <= 0.5`, `queries_changed_after_initial`,
  no `repetition_violations`, no `invalid`). Move `min_meaningful_new_mechanisms`,
  `require_cross_mechanism`, and `min_mainstream_coverage` into a separate
  `discovery_quality_passed`, evaluated only under `host_in_loop`.
- `summarize` emits both `mechanics_verdict` and `discovery_verdict`. Overall verdict:
  `fail` if mechanics fails; otherwise `needs_review` while `discovery_verdict` is `not_measured`;
  `pass` only when both pass. Never `pass` on unmeasured discovery.
- Blind labels continue to feed `blind_meaningful_gain` and `invalid` exactly as v0.4.9 built them.
  A `wrong_domain` label still lands in `invalid` and still fails mechanics — that is intended, and
  `spatial computing` is the live example.

Expected outcome: development mechanics should **pass** on everything except `learning-habit`, which
carries the `wrong_domain` invalid gain. State clearly whether it does; do not tune to make it green.

### Phase 2 — Near-duplicate repositories defeat independent-repo support (`src/muse_shroom/`)

`spatial computing` was confirmed via `cross_domain_mechanism_transfer` on two repositories:

```
bbylw/ui-ux-pro-max-skill-cn
ganavisk/nextlevelbuilder-ui-ux-pro-max-skill-Public
```

These are clones. The supporting evidence is the same README table, differing only in punctuation
(`、` vs `,`), and the term itself is a row in a UI/UX category list beside Web3 and drone fleets.

`evaluate_confirmation` (`src/muse_shroom/confirmation.py:157-185`) treats distinct `full_name`
values as independent support. Add a near-duplicate guard before counting `core_repos` toward
`multi_repo` or `transfer_backed`: treat two repositories as one source when their supporting
evidence text is near-identical (normalized excerpt equality or high token overlap), or when one is a
recorded fork/same-owner relative of the other — that relationship data already exists on
`discovery_paths`.

This is the only genuinely bad confirmation in the entire evaluation. It has one narrow cause; fix
that cause, not the symptom. Add a regression test with two clone candidates asserting they yield
`multi_repo == False`.

### Phase 3 — Latent replay nondeterminism (`src/muse_shroom/analyze.py`)

`age_days` (:46-53) uses `datetime.now(timezone.utc)` and returns integer `.days`, so
`activity = 100 − age_days/7` shifts by exactly `1/7 ≈ 0.143` whenever a repository crosses a day
boundary between replays. v0.4.8's replays hashed identically only because nothing crossed; v0.4.9's
did not. **Replay determinism has never actually been guaranteed.**

Resolve `activity` against a timestamp recorded in the cassette rather than wall clock, so replay is
reproducible by construction. If that is too invasive, the fallback is to exclude `activity` from the
hashed comparison and document that full-file hashing is not a determinism guarantee — but prefer the
real fix, since the whole replay methodology rests on it.

### Phase 4 — Documentation

- `evaluation/README.md`: document the mechanics/discovery verdict split, and state plainly that the
  deterministic policy cannot produce cross-domain transfer, so those metrics report `not_measured`.
- Append a dated note to `muse-shroom-v0.4.9-measurement-repair-plan.md` recording that its predicted
  `needs_review` outcome was wrong, and why (`boundary_quality_passed` unsatisfiable, not the
  labelling).

---

## 3. Verification

```console
python -m unittest discover -s tests
python evaluation/check_boundary_leakage.py
```

Replay against `evaluation/cassettes/boundary-v048.json.gz` and report actual output for:

| Check | Expected |
|---|---|
| Phase 0, complete labels | development `fail`; `blind_precision` populated; `unknown_mechanism_review_count` 0 |
| Golden-known metrics, all phases | unchanged in both suites |
| After Phase 1, deterministic policy | `cross_mechanism_status: not_measured`; `discovery_verdict: not_measured`; `mechanics_verdict` reported per suite |
| After Phase 1, overall verdict | `fail` if mechanics fails, else `needs_review`; never `pass` |
| After Phase 2 | `spatial computing` no longer reaches `confirmed`; clone-pair regression test passes |
| After Phase 3 | two replays separated by a forced clock change produce identical `activity` values |
| Holdout | no metric changes from Phase 2 or 3 beyond those explained by the duplicate guard; explain any that appear |

Do not commit. Leave the pre-existing untracked files and unrelated deletions alone.

---

## 4. Explicitly out of scope

- **GPT semantic leap.** The question is open again, but there is still no evidence for it, because
  no instrument exists. Phase 1 builds the instrument's foundation; a host-in-the-loop eval path is
  the next release, and only its results can justify building the feature.
- Confirmation yield / `same_repo_repetition`. Blind precision now says the confirmations are largely
  good; yield remains unproven as a problem.
- Relationship/Graph discovery.
- Ranking weights, retrieval depth, confirmation budgets.
- Holdout blind labelling and holdout policy.

## 5. Note on the honest headline

After this release the overall verdict will still not be `pass`, and that is the correct state. The
product's central claim — surfacing mechanisms from a different conceptual space — has never been
measured. Saying so plainly is more valuable than any verdict this harness can currently produce.
