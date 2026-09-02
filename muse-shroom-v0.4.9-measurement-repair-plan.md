# Muse-shroom v0.4.9 — Repair Plan: Make Confirmation Quality Measurable

Date: 2026-09-01
Branch: `experiment` (v0.4.8 committed at `2bd3deb`)
Status: plan only — no code changed, no tests run.
Audience: implementing agent (Codex).

---

## 0. Why this release exists

v0.4.8 shipped with a release framing that says confirmation quality is broken:
`confirmation_precision` 0.0, `confirmed_meaningful_count` 0, in both suites, unchanged by the whole
release. **That framing is wrong**, and it is currently steering roadmap decisions.

`confirmed_meaningful_count` only counts confirmations that match a **frozen**
`acceptable_new_mechanisms` list in each golden case (`evaluation/boundary_eval.py:356`,
`_golden_quality` at :183-210). A correct-but-unanticipated mechanism scores zero and is filed as
`unknown` / `needs_review`. The codebase already says this at `evaluation/run_boundary_eval.py:87`:

> "automatic precision is Golden-known precision only"

Cross-referencing the 6 actual confirmations in `evaluation/results/v048-followup-replay-1/` against
the 43 human labels already sitting in `evaluation/priority-diagnostic-labels.json`:

| suite | case | confirmed term | existing label |
|---|---|---|---|
| development | ai-music | beat tracking | meaningful |
| development | ai-music | pitch detection | meaningful |
| development | personal-knowledge | data synchronization | meaningful |
| holdout | remote-information-loss | decision log | meaningful |
| development | learning-habit | spatial computing | *unlabelled* |
| holdout | family-digital-preservation | selective scanning | *unlabelled* |

**Blind precision is ≥ 4/6 = 0.67, not 0.00.** Eight of the ten mechanisms currently blocking the
verdict are already human-labelled `meaningful`. The confirmation stage is producing beat tracking,
pitch detection, score generation, distraction detection, face recognition, and question generation —
good answers the answer key does not contain.

The consequence is worse than a bad number. Phase C was tried and reverted on `same_repo_repetition`,
a mechanical proxy, because the actual quality signal was unmeasurable. Every future
confirmation-stage decision has the same problem. So this release fixes the measurement, not the
mechanism.

**Scope decisions already made by the project owner:** development suite only (holdout keeps
Golden-known precision, preserving the blind-holdout guarantee), and measurement before yield.

### Expectation to set upfront — do not report this as a failure

`summarize_suites` (`evaluation/boundary_eval.py:547-565`) takes the worst of the two suites. Holdout
has 4 unlabelled unknowns and, by policy, no review path. **The overall release verdict will still
read `needs_review` after this work.** What changes is that the *development* suite becomes legible
and can reach `pass`. Holdout legibility is a separate policy decision and is not part of this
release. State this plainly in the final report.

---

## 1. Design

The eval conflates two questions that require different ground truth:

- *"Did it find the pre-specified answers?"* → recall. Legitimately needs a frozen key. **Unchanged.**
- *"Was what it produced good?"* → precision. Needs post-hoc labels of actual output. Today it is
  scored against the recall key. That is the category error this release fixes.

The ingestion point is already built and merely has no return path: `summarize` emits
`blind_unknown_review` for development only (`evaluation/boundary_eval.py:524-543`) carrying the exact
label vocabulary `meaningful | noise | synonym | too_generic | wrong_domain |
insufficient_evidence` and `"label": None` per item. It is a reviewer template. Feed it back.

### Anti-gaming constraints — these matter more than the feature

1. Labels are **all-or-nothing per run**. If any unknown in the run lacks a label, no labels apply and
   the run behaves exactly as today. This blocks cherry-picking favourable labels.
2. Golden-known and blind-labelled counts stay **separately reported** and separately auditable.
   Never collapse them into a single number.
3. `noise` and `wrong_domain` labels feed `invalid_boundary_gain`, which fails
   `boundary_quality_passed`. Labelling must be able to make the verdict *worse*, or it is not a
   measurement.
4. Labels never touch `acceptable_new_mechanisms`, aliases, or `DISCOVERY_PHRASE_HINTS`.

---

## 2. Changes

### 2.1 New label file — `evaluation/blind-review-labels.json`

Key scheme `suite|case_id|mechanism` (casefolded), matching the existing convention in
`priority-diagnostic-labels.json`. Seed it from the development-suite labels already in that file
(43 labels total: 24 `meaningful`, 7 `too_generic`, 5 `insufficient_evidence`, 5 `wrong_domain`,
2 `synonym`).

**Leave `priority-diagnostic-labels.json` untouched.** It is a historical post-hoc artifact with its
own provenance (`label_source: post_hoc_diagnostic_review`) and its note "Never loaded by production
search or evaluation" must stay true of it.

Header fields for the new file: `schema_version`, `label_source: "blind_unknown_review"`,
`suite_scope: ["development"]`, and a note stating that labels are precision-only and never feed
golden data.

One item needs a human label before labels can apply to the current run:
`development|learning-habit|spatial computing`. Do not invent a label for it — surface it as
required human input and let the all-or-nothing rule hold until it is provided.
(`holdout|family-digital-preservation|selective scanning` is out of scope.)

### 2.2 `evaluation/boundary_eval.py`

- `summarize(...)` and `summarize_suites(...)` accept an optional `labels: dict[str, str] | None`.
  Ignore it entirely when `suite != "development"`.
- In `_golden_quality`, after the existing `unknown` list is built (:200-210): if **every** unknown
  term in the run has a label, partition it —
  - `meaningful` → increments a new `blind_meaningful_gain`, removed from `unknown`
  - `noise` / `wrong_domain` → appended to `invalid`
  - `synonym` / `too_generic` / `insufficient_evidence` → new `labelled_discounted` bucket, removed
    from `unknown`, credited nowhere
- Keep `meaningful_boundary_gain` as the Golden-known count. Add `blind_meaningful_gain` alongside
  it, plus `total_meaningful_gain` as the sum used by the verdict gate.
- Verdict gate at :397-424: `meaningful_share` switches to counting `total_meaningful_gain > 0`;
  `unknown_count` reaches 0 naturally when labels are complete, which is what lets development leave
  `needs_review`. Every other condition in `passed_quality` is unchanged.
- New per-case and aggregate metrics, named so they cannot be confused with the Golden ones:
  `blind_meaningful_count`, `blind_precision`, `labelled_unknown_count`, `unlabelled_unknown_count`.
  Register them in the metric-name list at :19-31.
- Keep emitting `blind_unknown_review`, but populate each item's `label` from the file where known,
  so the packet doubles as a diff of what still needs review.

### 2.3 `evaluation/run_boundary_eval.py`

- Add `--labels PATH` (defaulting to `evaluation/blind-review-labels.json` when present, otherwise
  none) and thread it into `summarize_suites`.
- **Separate bug, one line.** `_confirmation_records` at :30-46 hand-picks fields and omits
  `mechanism_specificity` and `specificity_tier`. v0.4.8 added both to the record payload in
  `src/muse_shroom/confirmation.py`, but they are `None` for every record in
  `confirmation-analysis.json` — the new typing telemetry never reaches the artifact you would audit
  it with. Add both keys to the projection.
- Update the `note` at :86-89 to distinguish Golden-known precision from blind precision now that
  both exist.

### 2.4 `evaluation/README.md`

Document the label file, the all-or-nothing rule, and the development-only scope. State explicitly
that blind labels never feed `acceptable_new_mechanisms`, aliases, or phrase hints. The existing
holdout paragraph at :94-95 stays exactly as written.

### 2.5 Correct the shipped release framing

`muse-shroom-v0.4.8-follow-up-validation-report.md:148-154` states that confirmation quality is
unimproved and names yield the leading open problem. **Append a dated correction** — do not rewrite
history. The correction should say: the 0.0 was Golden-known precision by design; blind precision
measured against existing human labels is ≥ 0.67; confirmation yield is *not* established as the
leading problem.

### 2.6 Tests — `tests/test_boundary_evaluation.py`

Extend near `test_unknown_mechanism_is_reported_for_review` (:157):

- a labelled-`meaningful` unknown moves to `blind_meaningful_gain`, leaves `unknown`, and does **not**
  change `meaningful_boundary_gain`
- a labelled-`noise` unknown lands in `invalid` and makes `boundary_quality_passed` False
- **partial labels are ignored entirely** — one unlabelled unknown in the run means the summary is
  byte-identical to the no-labels run
- holdout ignores labels even when supplied
- `blind_precision` and Golden `confirmation_precision` are reported as separate fields

---

## 3. Verification

```console
python -m unittest discover -s tests
python evaluation/check_boundary_leakage.py
```

Then replay twice against the unchanged `evaluation/cassettes/boundary-v048.json.gz` and confirm
byte-identical `boundary-verdict.json` and `confirmation-analysis.json` hashes, as in the v0.4.8
follow-up.

Report actual output for each of these:

| Check | Expected |
|---|---|
| Replay **without** `--labels` | every aggregate byte-identical to `evaluation/results/v048-followup-replay-1` |
| Replay **with** labels, `spatial computing` still unlabelled | still byte-identical — proves the all-or-nothing rule |
| Replay **with** labels, all development unknowns labelled | development `blind_precision` > 0; `unknown_mechanism_review_count` → 0; development verdict may leave `needs_review` |
| Golden metrics | `confirmation_precision`, `confirmed_meaningful_count`, `meaningful_boundary_expansion_share` unchanged in **both** suites — labels must never move Golden-known numbers |
| Holdout | every aggregate unchanged under all three runs |
| Overall verdict | still `needs_review` — holdout is unresolved by design; report it as expected, not as a failure |
| `confirmation-analysis.json` | `mechanism_specificity` / `specificity_tier` populated, not `None` |

Do not commit. Leave the pre-existing untracked files and unrelated deletions in the working tree
alone.

---

## 4. Explicitly out of scope

- Confirmation yield, `same_repo_repetition`, and any Phase C retry. Revisit only once blind precision
  gives a real signal — the reverted experiment failed a proxy, which is not the same as failing.
- Holdout blind labelling, and any change to holdout policy.
- Cassette capture for the three diagnostic queries; acceptance criteria 1, 2 and 6 from the v0.4.8
  plan remain covered by unit test only. Worth its own release; it does not block this one.
- Any change under `src/muse_shroom/`. This release touches `evaluation/`, docs, and tests only.

---

## 2026-09-01 outcome correction

The predicted overall `needs_review` outcome was wrong. After the 35th
development label (`learning-habit | spatial computing = wrong_domain`) was
added, the complete-label replay produced development `fail` and overall
`fail`, while all Golden-known metrics remained unchanged.

The cause was not blind labelling. `boundary_quality_passed` combined mechanics
with discovery thresholds that the deterministic harness cannot satisfy:
`require_cross_mechanism` was true for every development case, but the worker
can promote only observation-emitted evidence and reported no cross-mechanism
discovery in any case. Four cases also had no later-step mechanism, making their
minimum meaningful-gain threshold unreachable. The earlier `needs_review`
branch merely short-circuited before this failure. v0.4.10 splits the mechanics
and discovery verdicts and reports deterministic discovery as `not_measured`.
