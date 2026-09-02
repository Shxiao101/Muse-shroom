# Muse-shroom v0.4.8 — Repair Plan: Semantic Typing & Role Eligibility

Date: 2026-09-01
Input: `muse-shroom-diagnosis-summary.md`, `muse-shroom-v0.4.7-three-run-raw-analysis-report.md`,
`muse-shroom-v0.4.7-three-run-raw-data.json`
Status: plan only — no code changed, no tests run.
Audience: implementing agent (Codex).

---

## 0. Verdict on the diagnosis document

The diagnosis is **correct in its conclusion and mostly correct in its causes**. Verified against
the source and the persisted session data. Three corrections change the actual work:

| Diagnosis says | Verified reality | Impact on plan |
|---|---|---|
| "Add a mechanism-candidate classifier/typing step" (fix #1) | The classifier **already exists** and is **already correct** on the failing case. `_specificity_class("chrome extension")` returns `project_category` today. | Do **not** build a classifier. Fix the **bypass** that lets its verdict be ignored, and the **weighting** that makes its verdict nearly irrelevant. Much smaller, much safer change. |
| "Remove or soften origin-based anchor exclusion" (fix #2) | There are **three** origin-based sites, not one, and two of them are worse than the one named. | Fix all three; also fix a fourth bug the doc missed (anchor slot ≠ anchor label). |
| Fixes #1 and #2 are independent | They are **one causal chain** through five modules, terminating in an ungated auto-promotion at `search.py:1308`. | Sequence matters. See §2. |

**Agreed and reaffirmed:** do not build the GPT semantic-leap layer, do not add Relationship/Graph
Discovery, do not raise budgets, do not retune ranking weights — not yet. The evidence for this is
now stronger than the doc states; see §1.6.

---

## 1. Verified root causes

All line references are against the current `experiment` branch at `3243370`.

### 1.1 The full causal chain (one bug, five modules)

This is the `chrome extension` failure, traced end to end. Every step is confirmed by code plus the
persisted Focus-run record.

```
boundary.py:1066   term_disposition() "uncertain_category" escape hatch
                   -> a project_category term with >=2 tokens and score>=70 is queued anyway
                        |
boundary.py:186    _confirmation_priority() ranks it 85 -- ABOVE every correctly typed mechanism
                        |
confirmation.py:87 plan_confirmation_candidates() sorts by that score; session budget is 3
                   -> "chrome extension" (85) takes the last slot,
                      "estimated remaining" (82) -> skipped_budget
                        |
confirmation.py:190 evaluate_confirmation() -> "confirmed" (multi_repo_independent_support)
                        |
search.py:1308     confirmed terms appended to request.exploration_directions
                   with NO specificity gate at all
                        |
boundary.py:635    annotate_candidate_mechanisms() now stamps "chrome extension"
                   on every candidate whose README contains it
                        |
ranking.py:199     new_mechanisms_for() -> FardeenRahman13/Devoted displayed at rank 10
                   with new_mechanisms: ["chrome extension"], role "wildcard"
```

The last hop is a **second-order defect worth calling out on its own**: the `promotable` gate in
`iteration.py:338-346` (`_validate_hypothesis`) refuses to let the host promote a non-promotable
discovered term. The confirmation path at `search.py:1308-1321` **bypasses that gate entirely**. Two
promotion doors, one guarded, one not.

### 1.2 The priority formula is anti-correlated with the product goal

This is the deepest finding and it is **not** in the diagnosis document. It generalises beyond the
three test runs.

`_confirmation_priority` (`boundary.py:186-249`) computes:

```
confirmability = 0.30*relevance + 0.20*specificity + 0.15*source_quality
               + 15*core + 10*request_anchored + 5*mechanism_anchored
               + 5*multi_repo + 5*transfer_plausible
priority       = 0.70*confirmability + 0.30*novelty
```

Two structural problems:

1. **`novelty` is dead.** Across all 27 queued candidates in the three runs, `novelty` was 100 in 25
   cases and 67 in 2. A near-constant contributes no ordering. Effectively
   `priority ≈ 0.7*confirmability + 30`.
2. **Everything that actually discriminates measures proximity to the request.** `relevance` (30),
   `source_quality` (15), `request_anchored` (10), `mechanism_anchored` (5) = 60 points of weight,
   all of which reward a phrase for **co-occurring with the query terms** — which is precisely what a
   generic platform/domain label does, and precisely what a genuinely distant mechanism does not.
   `specificity` — the only signal that asks "is this a *mechanism*?" — carries 20 points, and the
   full mechanism→category swing is only `0.20 * (95-35) * 0.70 = 8.4` priority points.

The measured consequence, from the raw data:

| Run | Confirmed | Its type | Highest-priority correctly-typed mechanism | Its fate |
|---|---|---|---|---|
| Focus | `chrome extension` (85) | `project_category` | `habit tracker` (69) | skipped_budget |
| OMR | `music notation` (87), `mensural notation` (71) | both `project_category` | `music information retrieval` (79) | skipped_budget |
| Dating | *(none — 7 queries, 0 confirmations)* | — | `image recognition` (77) | skipped_budget |

**Every term that reached confirmation as `category_like` beat every term that reached it as
`specific_mechanism`.** The inversion is systematic, not incidental. `estimated remaining` (82) lost
to `chrome extension` (85) by exactly the 3.5 priority points of the `independent_repo_support`
bonus.

### 1.3 Retrieval origin decides semantic role — three sites

`matched_kinds` is pure retrieval provenance: it records which query lane recalled a repo
(`search.py:566`). It is a **monotone union** — a repo matched by both core and adjacent queries
carries both tags forever. It is then read as if it were a relevance signal in three places:

**(a) `ranking.py:422-426` — hard anchor exclusion** (the one the diagnosis names):

```python
anchor_pool = [
    item for item in eligible
    if item["scores"]["components"]["popularity_percentile"] >= 60
    and "adjacent" not in set(by_name[item["repo"].lower()].get("matched_kinds") or [])
]
```

Because `matched_kinds` is a union, this also excludes repos that *did* match core queries.
Confirmed: `curbox-app/curbox-android` had `matched_kinds: ["adjacent", "core"]`, relevance 96,
popularity 69, **the highest boundary score in the Focus run (79.73)** — and was barred from the
anchor slot.

**(b) `boundary_score.py:280-285` — role assignment:**

```python
adjacent = "adjacent" in kinds
if new and float(transfer) >= 70 and (adjacent or popularity < 55): return "wildcard"
if new and (adjacent or float(transfer) >= 55):                     return "leap"
```

Worse than (a), because it is user-visible. `Audiveris/audiveris` — relevance 97, popularity 97,
2,745 stars, the canonical open-source OMR application, and the highest-boundary-scoring candidate in
the OMR run (80.56) — was labeled **`wildcard`**, the most exploratory role in the taxonomy, solely
because the query that found it was tagged `adjacent`. The anchor slot went instead to
`apacha/MusicObjectDetector-TF` (RRF 0, discovered only via a `same_owner` relationship hop).

**(c) `queries.py:449` — confirmation queries stamp `lane_kind: "adjacent"`.** Combined with
`search.py:1308` auto-promoting confirmed terms into `exploration_directions` (which produce
`adjacent`-lane queries at `queries.py:356`), this closes a self-defeating loop: **every repo found
through a mechanism the system worked to confirm is permanently barred from being an anchor and
pushed toward wildcard.** `gh-romi/nanoScore` is the worked example — found by
`confirmation_anchor`, tagged `adjacent`, labeled `wildcard`.

There is a fourth, benign use at `ranking.py:248` (`_compatibility_buckets`). That one is a
presentation-only legacy projection — leave it.

### 1.4 Anchor slot and anchor label are computed by different rules and disagree

Not in the diagnosis document. `ranking.py:427` fills the anchor slot via `_mmr_select` over
`anchor_pool`. `ranking.py:200` then labels roles via `assign_boundary_role`, which never learns
which item won the slot. They disagree in **two of three runs**:

- Focus: slot went to `super-productivity`; label came back `leap` (it had new mechanisms and
  transferability ≥ 55). `boundary_summary.anchor_count == 0`.
- Dating: rank 1 `abhi-yo/trust-dating` labeled `wildcard`, popularity 8.6. `anchor_count == 0`.

So in two of three runs the composer promised the user "one mainstream anchor" (README, §结果) and
delivered a result set containing no item labeled anchor.

### 1.5 Confirmation yield is separately broken

`same_repo_repetition` was the rejection reason for **6 of the 8 attempted confirmations** (3/3 in
Dating, 2/2 in Focus round 1, 1 in OMR). Cause: `confirmation_queries` (`queries.py:439-451`) only
ever emits `"<term>" AND "<context>"` conjunctions, where context is the problem concept, an anchor,
or a seed repo. For a term extracted from one repo's README, that conjunction overwhelmingly
re-retrieves that same repo, and `evaluate_confirmation` (`confirmation.py:157-161`) discards every
source whose repo is already in `discovery_repos`. The stage burns its query budget structurally
unable to succeed.

Real, but **second-order**: with §1.1–§1.3 fixed, better candidates enter the stage and the yield
question can be measured honestly. Fixing it first would mostly confirm more junk, faster.

### 1.6 Why this strengthens "don't build semantic leap yet"

The diagnosis argues semantic leap is unnecessary because `estimated remaining` was already
retrieved. The stronger version, from §1.2: the confirmation-priority function **actively selects
against** the kind of candidate semantic leap is meant to produce. A hypothesised mechanism that
never appeared in a retrieved README would enter the queue with low `relevance`, no
`request_anchored`, no `source_quality`, and no `core_use_case` — it would score at the *bottom* of a
3-slot budget and be skipped before it was ever tested. **Semantic leap fed into the current pipeline
is not merely premature; it is dead on arrival.** Fix §1.2 first or the feature cannot function at
all.

---

## 2. Repair plan

Sequenced so each phase is independently verifiable, and each later phase's acceptance criteria
depend on earlier phases having landed.

**Deviation from the diagnosis document's fix order:** do **Phase A (eligibility) before Phase B
(typing)**. Phase A is ~25 lines, purely deterministic, requires no judgement about word lists, and
its acceptance criteria (`Audiveris` becomes the OMR anchor) are checkable in isolation. Phase B
changes which terms enter a budgeted stage and therefore perturbs every downstream number; landing it
second means Phase A's effects are already isolated and attributed. The document's fix #1/#2 content
is unchanged — only the order.

### Phase 0 — Make the three runs replayable (prerequisite)

Do not re-run live GitHub search to validate. `evaluation/run_boundary_eval.py capture` / `replay`
and `evaluation/cassette.py` already exist for exactly this.

- Capture cassettes for the three diagnostic queries (AI-assisted dating, optical music recognition,
  focus tools) so every later phase is validated offline and deterministically.
- Encode the assertions in §3 as a checked fixture, not as prose.
- Record the current (broken) values as the baseline. Every one of them should move.

Do not skip this. Phases A–C each change ranking output; without a frozen replay you cannot tell a
fix from a regression.

### Phase A — Retrieval origin must never decide semantic role (P0)

Guiding principle for the whole phase: **`matched_kinds` is telemetry. It records how a repo was
found. No code that decides what a repo *is* may read it.**

- **A1 — `ranking.py:422-426`.** Drop the `"adjacent" not in kinds` clause from `anchor_pool`. Anchor
  eligibility should use semantic signals already present on the item: `assessment.relevance` and
  `scores.components.evidence_completeness`, alongside the existing `popularity_percentile >= 60`.
  Reuse the existing `RELEVANCE_GATE` / `TYPE_QUALITY_GATE` constants from `boundary_score.py` rather
  than inventing new thresholds that express the same intent.

- **A2 — `boundary_score.py:273-292`.** Remove `matched_kinds`' influence on `assign_boundary_role`.
  Roles should follow from `new_mechanisms` + `transferability` + `popularity` only. Keep the
  parameter in the signature only if a caller still needs it for something else; otherwise delete it
  and update `ranking.py:198-200`. Sanity-check the new rule against the three runs: `Audiveris`
  (rel 97 / pop 97 / has new mechanisms) must not come out `wildcard`. With popularity ≥ 60 and high
  relevance, `anchor` or `leap` are defensible; `wildcard` is not.

- **A3 — Reconcile slot and label (`ranking.py:427` vs `ranking.py:200`).** The item that fills the
  anchor slot must be labeled `anchor`. Simplest correct form: pass the chosen anchor's key into
  `_explain_ranked_items` and force its role, or have the anchor `_mmr_select` call stamp the role
  directly. `boundary_summary.anchor_count` must then be ≥ 1 whenever `anchor_pool` was non-empty.

- **A4 — `queries.py:449`.** Confirmation queries must not stamp `lane_kind: "adjacent"`. Introduce a
  distinct `confirmation` lane kind. Audit every consumer of `matched_kinds`
  (`selection.py:313-322`, `ranking.py:248`, `boundary_score.py:280`) and decide explicitly for each
  whether the new kind participates. `selection.py:321` (`"adjacent" in kinds` → adjacent lane) is
  legitimate lane routing and may keep its meaning; `ranking.py:248` is presentation-only and is
  fine.

- **A5 — Regression note.** `tests/test_boundary_ranking.py` uses `kinds=["adjacent"]` at lines 138,
  164-165, 190-192, 243, 292 to *stage* wildcard/leap outcomes. Those fixtures mostly pair `adjacent`
  with genuinely low relevance/popularity, so most should still pass. Where one does not, update it
  to assert the **new intent** (role follows relevance and novelty, not provenance) — do not relax
  the assertion to make it green.

### Phase B — A category label must never be confirmed or displayed as a mechanism (P0)

- **B1 — Close the escape hatch (`boundary.py:1066-1075`).** The `uncertain_category` branch is what
  admits `chrome extension`, `music notation`, `loss function`, `screen addiction`,
  `integrated timeboxing`, and `fake payment` into the queue. Two acceptable designs; **prefer the
  second**:
  - *Hard close:* `project_category` → always `reject`. Simple, but loses genuine terms the lexicon
    mistypes — note `estimated remaining` is itself typed `project_category` today (see B2).
  - *Tiered (preferred):* keep the branch, but mark the queued item
    `specificity_tier: "provisional_category"`, and have B3 order it strictly below every typed
    mechanism. Preserves recall for lexicon misses while making it impossible for a category to
    preempt a mechanism.

- **B2 — Add a platform/packaging axis to `_specificity_class` (`boundary.py:328-357`).** The
  existing `ARTIFACT_TAILS` covers document-like artifacts (`journal`, `graph`, `version`), not
  *distribution channels*. Add a head-word rule for the packaging axis:
  `<anything> extension | plugin | addon | add on | app | application | client | bot | wrapper |
  boilerplate | template | starter | theme`, plus bare `browser extension`, `web app`, `cli tool`,
  `desktop app`, `mobile app`, `android app`, `ios app`.

  **Make it head-word based, not a brand list.** Verified today: `chrome extension`,
  `brave extension`, and `browser extension` all classify identically as `project_category` — a brand
  blocklist for "chrome" would let "brave" straight through, and the Focus run queued **both**.
  Return a distinct class (e.g. `packaging`) that is never in `promotable_specificities` and never
  reaches the queue under any tier.

  Then re-check `estimated remaining` after this change: it is a genuine mortality-salience /
  commitment mechanism that the current lexicon types `project_category`. It must survive B1+B2 —
  that is the single most important behavioural assertion in this plan.

- **B3 — Order the confirmation queue by type tier first (`boundary.py:1244-1247`,
  `confirmation.py:87-92`).** Replace the flat `-confirmation_priority_score` sort with a
  lexicographic key: **`(specificity_tier, -priority_score, ...)`**, where tier is
  `mechanism|intervention|workflow_pattern|behavioral_signal` (0) before `provisional_category` (1).
  This is the change that directly produces the `estimated remaining` over `chrome extension`
  outcome, and it does so **structurally** — no weight tuning, no threshold a future run can slip
  past.

- **B4 — Rebalance `_confirmation_priority` (`boundary.py:186-249`).** Within a tier, the current
  formula still rewards request-proximity. Minimum change: drop the `novelty` term (a near-constant
  contributing nothing — see §1.2) and redistribute its 0.30 weight, or replace it with a signal that
  actually varies. Do not attempt a full recalibration here; B3 does the heavy lifting, and per the
  diagnosis document weight tuning belongs after the structural fixes.

- **B5 — Gate the confirmation promotion path (`search.py:1308-1321`).** Confirmed terms are appended
  to `request.exploration_directions` with no type check, bypassing the `promotable` gate that
  `iteration.py:338-346` enforces on the host's own promotions. Apply the same specificity gate here.
  Two doors, one guard — close the second door.

- **B6 — Gate the display path (`ranking.py:199-210`, `boundary_score.py:268-270`).** Even with B5,
  make `new_mechanisms` / `why_different` refuse to surface a term whose specificity is not
  promotable. Defence in depth: this is the surface the user actually reads, and it is where
  `chrome extension` became visible.

- **B7 — Lexicon constraint.** `evaluation/check_boundary_leakage.py` blocks holdout answers from
  entering `DISCOVERY_PHRASE_HINTS`. Any new word set added in B2 must be added to that check the
  same way. Do not grow `MECHANISM_HINTS` or `DISCOVERY_PHRASE_HINTS` to paper over a
  misclassification — that is how the lexicon reached fifteen hand-maintained sets.

### Phase C — Confirmation yield (P1, only after A and B replay clean)

- **C1 — `queries.py:439-451`:** add at least one query shape that is not a conjunction with the
  discovery context. Candidates: the term alone plus the request qualifier suffix; the term conjoined
  with a *different* concept than the one it was extracted next to; or an explicit `-repo:<seed>`
  exclusion. Purpose: give `evaluate_confirmation` a real chance at `confirmation_evidence` from a
  repo outside `discovery_repos`.
- **C2 — `search.py:853-859`:** account `unresolved` separately from `rejected` in the session
  budget. A query that failed to retrieve anything new is not the same evidentiary event as a term
  that was tested and found unsupported; today both silently consume one of the three session slots.
- **C3** — Only after C1/C2 land and are measured, revisit `DEFAULT_CONFIRMATION_CASE_LIMIT` (3) and
  `DEFAULT_CONFIRMATION_CANDIDATE_LIMIT` (2) in `models.py:409-412`. Per the diagnosis document, and
  confirmed here: raising these before the typing fix amplifies the error.

### Phase D — Validation

Replay all three cassettes. Check §3. Then, and only then, run the existing release gates:
`python -m unittest discover -s tests`, `run_boundary_eval.py replay`, `check_boundary_leakage.py`,
and the development/holdout split.

### Phase E — What comes after (do not start these now)

In order, each gated on the previous being measured:

1. Ranking-weight recalibration (`RANK_BOUNDARY_WEIGHTS`, the four lane formulas at
   `ranking.py:344-368`).
2. Re-ask the retrieval-depth question. The OMR run gives a hint the diagnosis did not draw out: the
   core queries were Chinese (`"光学乐谱识别"`) and `Audiveris` was reached only by the English
   `"OMR application"` exploration query. If depth turns out to be a real ceiling,
   **cross-language query expansion may be the actual gap**, not semantic leap.
3. Relationship/Graph discovery.
4. GPT semantic leap — and per §1.6, not before Phase B lands, because the priority function would
   starve every hypothesis it produced.

---

## 3. Acceptance criteria

Concrete, checkable, offline. Baseline column is the measured v0.4.7 value.

| # | Assertion | v0.4.7 baseline | Required after |
|---|---|---|---|
| 1 | OMR anchor is `Audiveris/audiveris` | anchor = `apacha/MusicObjectDetector-TF` (RRF 0, relationship-only) | Phase A |
| 2 | `Audiveris/audiveris` role is not `wildcard` | `wildcard` | Phase A |
| 3 | `curbox-app/curbox-android` (kinds `["adjacent","core"]`, rel 96, pop 69, boundary 79.73) is anchor-eligible | excluded | Phase A |
| 4 | `boundary_summary.anchor_count >= 1` in all three runs | 0 in Focus, 0 in Dating | Phase A |
| 5 | No `packaging`-class term appears in any `confirmation_queue` | `chrome extension`, `brave extension`, `browser extension` all queued | Phase B |
| 6 | Focus run confirms `estimated remaining` | confirmed `chrome extension`; `estimated remaining` skipped_budget | Phase B |
| 7 | No candidate's `new_mechanisms` contains a non-promotable term | `Devoted` shows `["chrome extension"]` | Phase B |
| 8 | In every run, no `provisional_category` term is attempted before an untried typed mechanism | violated in all 3 runs | Phase B |
| 9 | `same_repo_repetition` rejection rate | 6 of 8 attempts | measure after A+B; target after C |
| 10 | Dating run yields ≥ 1 confirmation | 7 queries, 0 confirmations | Phase C |
| 11 | Existing gates still pass: unittest, boundary eval dev+holdout, leakage check | passing | every phase |

Criteria 1–8 are the real test of this plan. 9–11 are guard rails.

The diagnosis document's third validation check ("does the final narrative correctly reflect that
`PerfectReply`-type candidates cover their mechanism?") is deliberately **not** in this table. It is
a judgement about host-Agent prose, not a deterministic property of the Python core, so it cannot be
asserted in a replay. It should be assessed by reading the post-fix Dating run output — but it must
not gate the release, and it must not be silently dropped either.

---

## 4. Refactoring direction (beyond the immediate fix)

Structural observations behind the bugs. Not required for v0.4.8; recorded so the patches above are
written in a direction that does not have to be undone.

1. **One enum is carrying four orthogonal axes.** `_specificity_class` returns a single string mixing
   *solution mechanism* (`mechanism`, `intervention`, `workflow_pattern`, `behavioral_signal`),
   *packaging* (proposed `packaging`; today misfiled as `project_category`), *domain label*
   (`domain_category`, `project_category`), and *implementation substrate* (`technology`,
   `artifact`). These answer different questions and are consumed by code that wants only one of
   them. Splitting the return into `(semantic_axis, tier)` would make B2/B3 a data change rather than
   a lexicon patch, and would make `promotable_specificities` self-documenting.

2. **Provenance and semantics must not share a field.** `matched_kinds` is the single worst offender:
   a monotone union of query-lane tags, read by three separate modules as if it meant "this repo is
   tangential." Phase A treats the symptoms. The durable fix is to keep `matched_kinds` as write-only
   telemetry and derive an explicit `eligibility` / `role_inputs` structure from semantic signals at
   composition time.

3. **Gate and score are conflated.** `_confirmation_priority` blends "is this even a mechanism?" (a
   binary admissibility question) with "is it worth one of three GitHub queries?" (a ranking
   question) into one continuous number — which is exactly how an 8.4-point type penalty gets
   outvoted by a 60-point proximity bonus. B3 imposes the separation lexicographically; the cleaner
   end state is an explicit two-stage `admit() -> rank()`.

4. **The lexicon is at its scaling limit.** `boundary.py` holds ~15 hand-maintained word sets
   (`MECHANISM_HINTS`, `DISCOVERY_PHRASE_HINTS`, `*_TAILS`, …) that are simultaneously the typing
   system and a leakage-audited surface. Measured precision is mediocre in both directions: it types
   `kol collaboration`, `comprehensive collaboration`, and `terminal automation` as *mechanisms* (via
   `TRANSFER_MECHANISM_TAILS`), while typing the genuine `estimated remaining` as
   `project_category`. Direction: prefer morphological/structural rules plus a small committed
   corpus of terms-with-expected-class, so precision is measured rather than assumed. Every list
   added in B2 makes this worse before it gets better — keep it head-word shaped and small.

5. **Two promotion doors, one guard.** `iteration.py:338-346` enforces `promotable` on host-driven
   promotion; `search.py:1308` enforces nothing on confirmation-driven promotion. Any future third
   path into `request.exploration_directions` will need the same guard. Consider a single
   `promote_direction(request, term, evidence)` chokepoint that owns the gate.

---

## 5. Explicitly out of scope for v0.4.8

- GPT semantic-leap layer (see §1.6 — it cannot work until Phase B lands).
- Relationship/Graph discovery.
- Any change to `RANK_BOUNDARY_WEIGHTS` or the four lane score formulas.
- Any increase to retrieval depth, `session_query_budget`, or confirmation budgets.
- Cross-language query expansion (noted in Phase E as a hypothesis, not a task).

---

## 6. Note on scope

Phase A and Phase B are both P0; both are required to satisfy the diagnosis document's own validation
checks. Phase C is genuinely separable and can ship in v0.4.9 if v0.4.8 is time-boxed — but if it is
deferred, criteria 9 and 10 must be deferred with it and recorded as known-open, not quietly dropped.
