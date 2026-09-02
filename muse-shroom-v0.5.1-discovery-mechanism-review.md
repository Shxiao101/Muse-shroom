# Muse-shroom v0.5.1 — Discovery Mechanism Review

Date: 2026-09-02
Version reviewed: v0.5.0 (`60fce8a`), plus uncommitted v0.5.1 instrumentation
Scope: is the discovery mechanism capable of what the product claims?
Status: review only. The v0.5.1 code described in §7 is implemented and unit-tested but **not committed**.

---

## 1. The question

Three consecutive releases fixed real defects without moving the product's output. This review asks
whether the main problem had actually been found.

**It had not.** The three fixes were downstream of an architectural ceiling that no release has
touched, and that this review measures for the first time.

---

## 2. The pattern that should have been the warning

| release | the "core problem" | what it turned out to be |
|---|---|---|
| v0.4.8 | typing and role eligibility | real core bugs — fixed, but `confirmation_precision` stayed 0.0 |
| v0.4.9 | confirmation quality is broken | **measurement artifact** — 0.0 was Golden-known precision only; true blind precision 0.67–1.0 |
| v0.4.10 | the release gate never passes | **harness artifact** — the gate demanded cross-domain transfer the deterministic policy cannot produce |
| v0.5.1 | deep mode stalls after one iteration | **harness artifact again** — the eval policy halted, not the core |

Each diagnosis was correct at its own level. None reached the ceiling. Three iterations of
"the instrument is wrong" is itself a finding: effort had drifted into the measurement apparatus.

---

## 3. The architectural finding: discovery is a closed lexical loop

A discovered term can enter the system at exactly four call sites, all in
`discovered_term_evidence` (`src/muse_shroom/boundary.py:1001-1036`):

| source | origin |
|---|---|
| `topics` | GitHub topics of an already-retrieved repo |
| `description` | GitHub description of an already-retrieved repo |
| `readme_*` | README excerpt of an already-retrieved repo |
| `relationship_detail` | relationship metadata of an already-retrieved repo |

**There is no fifth source.** Every term the system can emit is a substring of text belonging to a
repository it had already retrieved.

Retrieval, in turn, is driven by `build_queries` (`src/muse_shroom/queries.py:231`) from
`problem_concepts` + `mechanisms` + `exploration_directions` — the user's own words — plus terms
promoted from previously retrieved repos.

The loop closes:

```
user's words -> repos containing those words -> terms in those repos' text
             -> repos containing those terms -> ...
```

The reachable set is the **lexical neighbourhood of the query**. A cross-domain mechanism is, by
definition, outside that neighbourhood. The architecture cannot originate vocabulary the corpus does
not already contain.

---

## 4. The measurement

Every recorded run in the project, matched against the golden cases' `cross_mechanism_directions`
(the concepts each case says a good answer should reach):

```
case-runs examined ............................................. 296
case-runs producing a golden cross-domain direction ............   0
```

Not 0/14 in one release. **Zero out of 296, across the project's entire recorded history.**

Reproduce: match `new_mechanisms`, `directions_uncovered` and `evidence_sources[].term` from every
`boundary-*-agentic.raw.json` under `evaluation/results/` against each case's
`cross_mechanism_directions`, using the same alias matching as `_matches` in
`evaluation/boundary_eval.py`.

---

## 5. It is a retrieval failure, not a recognition failure

The distinction matters, because it decides what to fix.

**16 of the 28 cross-domain concepts the golden cases ask for are already present in the production
recogniser's hint lists** (`DISCOVERY_PHRASE_HINTS` and `MECHANISM_HINTS` in `boundary.py`):

```
accountability          behavior design        behavioral economics    biofeedback
change impact           commitment device      creative coding         decision hygiene
decision record         digital minimalism     digital wellbeing       implementation minimalism
memory timeline         progress visualization sensemaking             tangible interface
```

The system is explicitly primed to recognise exactly these terms, and has been throughout. In 296
case-runs not one has appeared in retrieved text. The recogniser is not failing — nothing arrives
for it to recognise.

---

## 6. The ceiling is ours, not the corpus's

If no GitHub repository connected these domains, the golden cases would be asking the impossible and
the benchmark would be at fault. They are not:

```
biofeedback focus              -> marekkowalczyk/breathe-cli            (326 stars)
"commitment device" productivity -> ever-works/awesome-time-tracking    (219 stars)
```

`breathe-cli` is precisely the kind of cross-domain focus mechanism the `focus` case wants. It is
**one query away**. The system never issues that query, because it never possesses the word
"biofeedback" — and it never possesses the word because it only harvests vocabulary from repos it
found using focus vocabulary.

---

## 7. What v0.5.1 delivered (uncommitted)

The work stands on its own merits even though it did not reach the ceiling.

- **Gate instrumentation** (`src/muse_shroom/boundary.py`) — `gate_signals` emits `request_anchored`,
  `mechanism_anchored` and `gate_blocked_by` per term, over all sources rather than the truncated
  three. Diagnostic only; verified behaviour-neutral (every pre-existing aggregate identical between
  `v051-before-harness` and `v0410-replay-1`).
- **Promotion funnel reporting** (`evaluation/boundary_eval.py`) — per-case and per-suite funnel,
  `stop_reason`, later-stage query counts. Cases predating the telemetry report `None` rather than
  inferring wrong counts from absence.
- **Harness realism** (`evaluation/version_worker.py`) — the evidence-ID fallback a real host Agent
  has and the deterministic policy never used.

Measured effect on development (capture and replay produced byte-identical hypotheses, 16/16):

| | before | after |
|---|---|---|
| cases where iteration added no new mechanism | 5/8 | **0/8** |
| iterations used | `[1,1,1,2,2,1,1,1]` | `[2,2,2,2,2,2,2,2]` |
| later-stage queries | 10 | 16 |
| promotable terms | 13 | **13** |
| terms blocked by `request_anchored` | 42 | **41** |

The stall vanished and **the core funnel did not move**. The harness routed around the gate; it did
not relieve it.

### The promotion funnel itself

From `v051-before-harness`:

```
development   discovered 64 -> typed 56 -> request_anchored 15 -> promotable 13
holdout       discovered 48 -> typed 38 -> request_anchored  6 -> promotable  2
blocked_by    request_anchored 76 | specificity 18 | score 3 | promoted 15
```

`request_anchored` — which requires a discovered mechanism's evidence text to sit near the *user's
own problem words* — blocks 76 terms. The score threshold blocks 3. This is the same
request-proximity anti-correlation documented in the v0.4.8 plan §1.2 for `_confirmation_priority`,
living in a second place.

**But the v0.5.1 evidence does not justify changing it.** Iteration does not stall once the policy is
realistic, so the gate was not the binding constraint. It is also fully bypassable: a host Agent can
promote any typed term through the evidence-ID path with no `promotable` requirement. The open
question is not "is the gate too tight" but "what is a bypassable gate protecting".

---

## 8. Where the earlier diagnosis went wrong

`muse-shroom-diagnosis-summary.md` concluded: *"the semantic-leap motivating assumption is false"* and
*"this is not a retrieval-depth problem."*

It conflated two different claims:

1. **"Strong relevant candidates were already recalled."** True, well-evidenced, and it correctly
   identified real downstream bugs (role eligibility, typing) that v0.4.8 fixed.
2. **"Therefore retrieval is not the problem."** Does not follow. Recalling good **relevant** repos
   is not the same as reaching **different-mechanism** repos.

Its single counter-example — `estimated remaining`, a mortality-salience mechanism — surfaced in a
focus search where "time remaining" is lexically adjacent to focus and time vocabulary. That is not a
cross-domain leap; it is the lexical neighbourhood working as designed.

The `chat2.md` proposal was right about the ceiling. Three releases were spent downstream of it.

---

## 9. What follows

The requirement is now precise: **something must inject vocabulary the retrieved corpus does not
contain.**

### The cheap fix is a trap

`DISCOVERY_PHRASE_HINTS` already holds 16 of the 28 wanted concepts and is used only to *recognise*.
Feeding it into query generation is a small change that would light up the development suite — and it
would be benchmark gaming. `check_boundary_leakage.py` guards holdout answers but not development
ones, so this inflates development scores while doing nothing real. Any lexicon used for query
generation must be broad and principled, never answer-shaped, and the leakage guard must be extended
to cover it.

### The legitimate source

The host Agent's world knowledge. It knows focus relates to biofeedback without needing a repo to say
so. That is `chat2.md`'s semantic-leap layer, now with evidence rather than speculation behind it.

### The blocking prerequisite

**It cannot be measured yet.** `deterministic_hypothesis` cannot hypothesise by construction, so a
host-in-the-loop evaluation path is no longer a nice-to-have — it is the prerequisite for judging any
leap mechanism. This has been deferred in three consecutive plans; it should be next.

---

## 10. Open items

- **Holdout replay divergence.** The v0.5.1 capture is valid, but replaying it fails on
  `readme('l33tdawg/sage')` — a repo absent from every holdout pool in the capture, so replay issues
  a query the capture did not. Ruled out: hash-seed nondeterminism (identical under `PYTHONHASHSEED`
  0 and 12345), unrecorded 404s (the cassette records those as `not_found`), and `reference_time`
  drift (`captured_at` correctly frozen at capture start). Development replays cleanly. Not
  root-caused. Holdout v0.5.1 numbers are capture-only, not replay-verified.
- **Capture fragility.** The first v0.5.1 capture died on a GitHub 403 secondary rate limit at the
  default `--search-interval 2.1` (~28.5 searches/min against a 30/min ceiling, dispatched through a
  thread pool). Succeeded at 3.5. Note the interval throttles `search_repositories` only — `readme`
  and `latest_release` fire unthrottled.
- **Five unlabelled terms.** The harness fallback surfaced `gui automation`, `local first`,
  `attention monitoring`, `devops automation`, `rank tracking`. Until labelled, the all-or-nothing
  rule means blind precision will not compute. Quality is visibly mixed — `prompt collaboration`,
  `workflow evaluation` and `data synchronization` are already labelled `meaningful`, while
  `gui automation` in a *focus* search and `devops automation` in *code review* look like noise.

## 11. Verification state

```console
python -m unittest discover -s tests      # 258 pass
python evaluation/check_boundary_leakage.py   # ok, no leaks
```

Changed and uncommitted: `src/muse_shroom/boundary.py`, `evaluation/boundary_eval.py`,
`evaluation/version_worker.py`, `tests/test_boundary.py`, `tests/test_evaluation.py`.
