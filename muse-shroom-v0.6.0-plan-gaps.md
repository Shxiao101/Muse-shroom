# v0.6.0 Skill-First Plan — Gaps vs the v0.5.1 Review

Date: 2026-09-02
Reviewed plan: `Muse-shroom v0.6.0 Skill-First Execution Plan.md`
Against: `muse-shroom-v0.5.1-discovery-mechanism-review.md`

The plan aims at the right ceiling: inject host-Agent vocabulary that retrieved text does not contain, and do not feed Golden phrases into query generation. These gaps are why that ceiling can still stay closed after the release.

## 1. Host evaluation is still observational

The review called a host-in-the-loop path a **prerequisite** for judging any leap. The plan keeps discovery quality observational and release-blocking only on mechanical correctness, transcripts, and leakage.

That repeats the v0.4.8–v0.5.1 pattern: the instrument moves, the product claim does not. v0.6.0 can ship a Skill rewrite, a provenance value, an evidence-ID fix, and a recapture while host transcripts still emit **zero** Golden cross-domain directions.

Transcript checks (waited for the first observation, at most two hypotheses, used `host_hypothesis`) measure protocol compliance, not discovery.

## 2. No operational test that the ceiling moved

The development suite has eight cases, two `cross_mechanism_directions` each. The plan does not say what must be observed, for example:

- how many cases issued a Golden cross-domain query
- how many retrieved a repo with matching mechanism evidence
- how many of those entered `new_mechanisms`

Without those counts, the release cannot answer the review’s question: is discovery capable of what the product claims?

## 3. Host-eval GitHub I/O is unspecified

Existing cassettes do not contain leap queries such as `biofeedback`. A host Agent that leaps cannot replay them.

The plan lists host transcript collection and a fresh empty-staging recapture as separate items. It does not say how they compose:

- live GitHub is costly, rate-limited, and not reproducible
- cassette replay only works if capture happens **during** the host run
- recapturing only the deterministic policy leaves the leap path unverified

`prepare / collect / score` is the right shape; collect still needs a recorded GitHub layer. The review also noted that the search interval throttles `search_repositories` only. The plan raises that interval to 3.5s and does not throttle `readme` or `latest_release`.

## 4. Current Skill still forbids the leap

`SKILL.md` tells the Agent not to invent directions without evidence. The hypothesis contract requires `discovered_term`, a candidate evidence ID, or `user_request`.

The plan reverses that for the first deep iteration only. If the Skill rewrite is incomplete, real hosts will keep refusing to hypothesize, and the thin core will have nothing to retrieve.

## 5. Skill examples already leak Golden terms

`hypothesis-contract.md` uses `digital wellbeing`, `commitment device`, and `biofeedback`. `check_boundary_leakage.py` scans production Python hint lists, not `skills/`.

The cheap-fix trap the review warned about would then live in Markdown instead of `DISCOVERY_PHRASE_HINTS`.

## 6. Leap queries can miss or be crowded out

`_quote()` wraps a whole concept as one GitHub phrase. `"biofeedback focus"` is stricter than the unquoted juxtaposition that finds `breathe-cli`.

`hypothesis_queries` enqueues `target_mechanism` / `target_direction` / `concepts` / promotions **before** `add_exploration_directions`. With a six-query round budget, two host hypotheses can land in `round_budget` skips. Status becomes Proposed, not Searched.

The core should give `host_hypothesis` pure and bridge terms first-round priority, and should not quote the bridge as a single phrase.

## 7. One pass, two hypotheses, no retry

Golden cases ask for two cross-domain directions. If the first pass guesses wrong, later iterations cannot leap again. Zero hypotheses is allowed, so conservative hosts will often emit none.

A single retry after Rejected, while budget remains, is not in the plan.

## 8. Recognition and scoring can drop a successful retrieval

Sixteen of twenty-eight Golden concepts are already in hint lists. The other twelve can be retrieved and still fail “evidence-backed mechanism matching the hypothesized term” unless the hypothesized term itself is a legal match target for that round.

The 60/60 validation gate is scored by the same Agent that proposed the hypothesis. Core can check citations and thresholds, not honesty. High scores overclaim; low scores hide a real leap. Discovery success should be query issued plus mechanism evidence found. The scores only decide formal presentation.

## 9. Initial-request leakage is Skill-only

The plan forbids front-loading cross-domain ideas into the first `SearchRequest`. Core does not enforce it. A host that stuffs leaps into the opening request pollutes the first retrieval and splits deterministic replay from host transcripts.

## 10. Five unlabeled terms remain

`gui automation`, `local first`, `attention monitoring`, `devops automation`, and `rank tracking` are still unlabeled. Blind precision will not compute until they are. The plan’s test list does not include this.

## What to add, without changing the Skill-first shape

1. Publish development-suite host counts for query issued, evidence found, and `new_mechanisms`. Do not make holdout discovery quality a release gate.
2. Capture GitHub during host collect, isolated per transcript. Recapture the deterministic policy separately.
3. Extend leakage checks to `skills/` and remove Golden phrases from Skill examples.
4. Prioritize `host_hypothesis` queries in the first-round budget; emit a pure term and a two-token bridge, not one quoted phrase.
5. Make the hypothesized term a legal mechanism-match target for that round.
6. Label the five unlabeled terms.
7. Treat 60/60 as a presentation gate, not the definition of a successful leap.
