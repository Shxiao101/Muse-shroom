# Review — Muse-shroom v0.6.0 Skill-First Execution Plan

Date: 2026-09-02
Reviewing: `Muse-shroom v0.6.0 Skill-First Execution Plan.md`
Reviewer context: `muse-shroom-v0.5.1-discovery-mechanism-review.md` (the 0/296 ceiling finding)
Verdict: **proceed**, with one blocking decision and two technical concerns to resolve during
implementation.

---

## 1. Overall assessment

The plan targets the actual ceiling — injecting vocabulary the retrieved corpus does not contain —
and routes it through the one principled source available: the host Agent's world knowledge.

Skill-first is architecturally correct here. It matches the existing division of labour (core =
deterministic retrieval, Agent = semantics), requires no model-provider API, and keeps the core thin.
The scope discipline is good and the thin-core boundary is drawn in the right place.

This is a better direction than the core-gate loosening the v0.5.1 work was heading toward, which the
v0.5.1 measurements did not in fact justify.

---

## 2. What the plan gets right

Non-obvious calls worth preserving through implementation:

| plan § | call | why it matters |
|---|---|---|
| §31 | the bridge pass runs **after** the initial observation, not front-loaded | the only way to tell whether the leap was needed rather than lucky |
| §75 | `host_hypothesis` is **provenance, not evidence** | stops a guess from being recorded as a finding |
| §149 | unvalidated host terms stripped from `new_mechanisms` before output | prevents the failure mode that would discredit the feature outright |
| §27, §162 | no fixed mechanism lists or Golden phrases in the Skill | independently identifies the leakage trap named in the v0.5.1 review §9 |
| §51 | reuse the existing hypothesis contract, no new field | minimal core surface |
| §47 | the Agent may return **zero** hypotheses | avoids forcing speculation when nothing transfers |
| §164 | fix `l33tdawg/sage` by full recapture, not a manual cassette entry | preserves strict replay |
| §165 | raise default capture interval to 3.5s | matches the observed 403 secondary-limit failure |

### §124 is a genuine bug, correctly identified

The plan proposes closing an evidence-ID loophole. Verified in
[`src/muse_shroom/iteration.py:333`](src/muse_shroom/iteration.py):

```python
if addition.evidence in evidence_ids:
    continue
```

Any evidence ID from **any** candidate satisfies the check, with no requirement that it relate to the
added term. The v0.5.1 harness fallback relies on exactly this door. Closing it is correct.

---

## 3. BLOCKING — discovery quality is not gated

**Severity: high. Resolve before implementation starts.**

§161 makes mechanical correctness, transcript validity and leakage release-blocking, and treats
discovery quality as *observational*.

The consequence: after v0.6.0 ships, the `0/296` cross-domain figure will have no successor number.
The deterministic harness cannot hypothesise by construction, and transcript collection (§154-159)
checks **process** — did the Skill wait for the observation, propose at most two, use the right
provenance, cite evidence — not **outcome**. §157 ("produced genuinely new mechanism candidates") is
the nearest thing to an outcome check and it is a human judgement.

This is the precise pattern that consumed v0.4.9, v0.4.10 and v0.5.1: ship a change, then be unable
to say whether it worked.

**Recommendation.** Add one gated outcome metric, even a crude one:

- run the development suite with a host in the loop, manually if necessary, once per release;
- count case-runs producing a golden `cross_mechanism_direction` (the same measurement as the v0.5.1
  review §4);
- require it to be **non-zero** before the feature may be described as working.

It does not need to be automated or cheap. It needs to exist, so that "did it work" stops being a
matter of opinion.

---

## 4. CONCERN — the validation gate is partly self-graded

**Severity: medium.**

§145-146 gate validation on `transferability >= 60` and `boundary_value >= 60`. Both are scores the
Agent produces, against a rubric the Skill supplies, with a pass mark it can infer. §97 instructs the
Skill to tell the Agent to score honestly rather than target the threshold — which asks the examinee
to disregard the pass mark.

The same gate also requires an evidence-backed mechanism match and a citation of the corresponding
`mechanism_match` evidence. Those are objective and do the substantive work.

**Recommendation.** Keep the evidence requirements as gating. Make the two numeric thresholds
reported-but-not-gating, or drop them. They add little discrimination and invite the behaviour §97
warns against.

---

## 5. CONCERN — a correctly retrieved repo may never reach assessment

**Severity: medium. Verify during implementation rather than discovering it as a null result.**

Validation (§142) requires an **eligible ranked** candidate. Ranking requires host assessment, which
requires making the shortlist.

A host hypothesis adds its mechanism to `exploration_directions`, so a cross-domain repo scores on
the **adjacent** lane. But `SHORTLIST_QUOTAS` in
[`src/muse_shroom/selection.py:35`](src/muse_shroom/selection.py) reserves:

```python
{"core": 3, "gems": 4, "adjacent": 2, "concept_bridge": 3}
```

**Two adjacent slots**, shared with every pre-existing exploration direction. With up to two
hypotheses competing against those, a correctly retrieved cross-domain repo can be squeezed out
before anyone assesses it — making validation unreachable regardless of how good the leap was.

The bridge concept in §60 (`"<mechanism> <request-grounded problem anchor>"`) partly mitigates this by
biasing retrieval toward repos containing both, which should also lift core-lane coverage.

**Recommendation.** Add a scenario test asserting that a repo retrieved solely by a host hypothesis
reaches `selected_for_assessment`. If it does not, either reserve a slot for host-hypothesis
retrievals or confirm the bridge concept reliably carries them into the core lane.

### Related interaction not addressed by the plan

The v0.5.1 instrumentation measured `request_anchored` blocking **76 of 112** discovered terms,
because it requires a term's evidence text to sit near the user's problem words. Terms discovered
*inside* a cross-domain repository are far from those words by definition.

The plan does not address what happens to terms harvested from hypothesis-retrieved repositories.
This is not necessarily a defect — the bridge concept mitigates it — but it should be measured rather
than assumed.

---

## 6. Smaller notes

- **Reconcile with the uncommitted v0.5.1 work.** Closing the §124 evidence-ID loophole breaks the
  v0.5.1 harness fallback, which depends on it. That is correct and desirable, but decide explicitly
  whether v0.5.1 lands first or is folded into v0.6.0. The v0.5.1 gate instrumentation
  (`gate_blocked_by`, `request_anchored`, `mechanism_anchored`) is worth keeping either way — the
  §5 verification above depends on it.
- **Five unlabelled terms.** `gui automation`, `local first`, `attention monitoring`,
  `devops automation`, `rank tracking` surfaced in v0.5.1 and are unlabelled. The all-or-nothing rule
  means blind precision will not compute until they are labelled.
- **One-shot bridge (§77).** Restricting hypotheses to the first post-search iteration is defensible
  discipline, but the first observation is also the thinnest — it covers mainstream mechanisms only.
  A tradeoff worth revisiting if hit rates are low, not an error.
- **Artifact name.** §186 specifies `muse-shroom-v0.6.0-execution-plan.md`; the plan file currently
  carries a different name.

---

## 7. Summary of required actions

| # | action | severity |
|---|---|---|
| 1 | Define one gated outcome metric for cross-domain discovery before implementation begins | **blocking** |
| 2 | Make `transferability`/`boundary_value` thresholds reported, not gating; keep evidence requirements gating | medium |
| 3 | Add a scenario test that a hypothesis-retrieved repo reaches `selected_for_assessment` | medium |
| 4 | Measure what `request_anchored` does to terms harvested from hypothesis-retrieved repos | medium |
| 5 | Decide whether v0.5.1 lands first or folds into v0.6.0 | low |
| 6 | Label the five outstanding blind-review terms | low |

Items 2-6 are refinements. **Item 1 is the difference between v0.6.0 being progress and being a
fourth release that cannot be evaluated.**
