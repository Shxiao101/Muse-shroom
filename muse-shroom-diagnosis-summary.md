# Muse-shroom v0.4.7 — Diagnosis Summary & Recommended Next Work

Date: 2026-09-01
Purpose: Handoff summary of a diagnostic conversation about Muse-shroom result quality, for continued engineering work.

## What Muse-shroom Is

A GitHub "cognitive boundary discovery" tool. Instead of returning the most popular/relevant repos for a query, it tries to surface projects that solve the same problem via a **different mechanism** the user wouldn't have thought to search for (e.g., for "improve focus," beyond Pomodoro/blockers, surface gaze-tracking, biofeedback, or commitment-device projects).

Architecture: a host Agent (Claude/Codex/Cursor) interprets natural language into a structured `SearchRequest`; a Python Core (CLI/MCP, same `SearchEngine`) does query planning, GitHub retrieval with caching, candidate merging (`discovery_paths`), and README/topic enrichment; a `boundary.py` module classifies extracted terms (mechanism vs. technology vs. category vs. workflow pattern) and tracks what's "covered" vs. "unexplored"; a shortlist (~12) goes to the host Agent for semantic judgment and ranking (RRF, concept coverage, relationship, underexposure, activity, evidence completeness) into Anchor/Edge/Leap/Wildcard roles.

## Initial Proposal Under Discussion (from `chat2.md`)

Add a "GPT semantic leap" layer: let the host Agent hypothesize mechanisms that never appeared in retrieved READMEs (e.g., biofeedback, commitment devices), tag them `unverified_hypothesis`, then verify against real GitHub evidence before promoting to `confirmed_boundary_direction`. Motivation stated at the time: the system can supposedly only discover mechanisms already present in the local README neighborhood of the initial query, so it structurally can't jump to a different conceptual space.

This was proposed alongside two other "jump sources": Evidence Discovery (already implemented — README → new mechanism) and Relationship/Graph Discovery (forks, same-owner, README links — not yet built, fully evidence-grounded, no verification step needed).

## Empirical Testing: Three Real Runs

Three real Codex-hosted searches were run (AI-assisted dating, optical music recognition, focus tools) and diagnosed against raw SQLite session data (selection scores, ranking scores, discovery paths, confirmation budget logs) — not just prose self-summary from Codex.

### Key Finding: The semantic-leap motivating assumption is false for these tests

In all three runs, **strong direct/relevant candidates were already recalled**, often in the top 5 (`PerfectReply`, `Audiveris`, `Curbox`, `Memento-Mori`'s mortality-salience mechanism). This is **not a retrieval-depth problem**. The failures happened downstream, after retrieval.

### Root causes identified (ranked by confidence, all high)

1. **Semantic typing and state pollution after retrieval** (highest confidence, present in all 3 runs)
   - Artifact types get promoted as mechanisms — e.g. `chrome extension` was confirmed and displayed as a "new mechanism" (it's a platform, not a solution mechanism), consuming the highest-priority confirmation slot in the Focus run while a genuine mechanism (`estimated remaining` — a mortality-salience/commitment mechanism) was skipped by budget.
   - Ranking and final narrative can disconnect: `PerfectReply` ranked #5 with high relevance, yet the host's summary said reply-assistance wasn't reliably covered.

2. **Hard composition/role rules override stronger direct evidence** (OMR and Focus runs)
   - `Audiveris` (best-scoring OMR candidate on relevance, activity, popularity, boundary score) was excluded from anchor eligibility purely because its retrieval origin was tagged "adjacent" — a weaker relationship-only repo with RRF 0 became the mandatory anchor instead.
   - This is a hard eligibility rule bug, not a ranking-weight tuning issue.

3. **Confirmation budget spent on poorly typed phrases** (all 3 runs)
   - Dating run: 7 confirmation queries, 0 confirmations.
   - Focus run: `chrome extension` (artifact type) confirmed while `estimated remaining` (genuine mechanism) was skipped by budget.
   - Fix is better pre-confirmation typing/rejection, not a larger budget.

### What this changes vs. earlier self-diagnosis

Codex's own prior self-diagnosis ("candidate priority/ranking calibration needs work," from the v0.4.7 changelog) pointed at ranking *weights*. The raw-data analysis shows weights are not the primary issue — the problem is upstream of weights, in candidate **classification** (mechanism vs. artifact-type vs. product-property vs. domain-label vs. README-section-phrase) and in **hard anchor-eligibility rules** based on retrieval origin rather than actual relevance.

## Conclusion / Recommendation

**Do not rebuild. Do not add the GPT semantic-leap layer yet.** The three-run diagnosis shows the ceiling on result quality right now is a fixable, narrow, well-localized bug — not a structural/architectural limitation, and not a retrieval-depth limitation.

Concrete evidence against building semantic-leap now: `estimated remaining` — a genuinely novel, non-obvious mechanism (the kind of "aha, I wouldn't have searched for this" result semantic-leap was meant to produce) — was **already found by ordinary retrieval** and simply starved by a confirmation-priority/typing bug. Building semantic-leap on top of the current broken typing/eligibility layer would just feed more candidates into the same broken filter, likely amplifying noise (already root-cause #3) rather than fixing anything.

### Recommended fix order

1. **Add a mechanism-candidate classifier/typing step.** Hard-separate: mechanism vs. artifact type vs. product property vs. domain label vs. README-section phrase. Block non-mechanisms (e.g. `chrome extension`) from being confirmed or displayed as "new mechanisms."
2. **Remove or soften origin-based anchor exclusion.** Anchor/role eligibility should be based on direct relevance and evidence completeness, not which query (core vs. adjacent/exploration) happened to recall the candidate first.
3. **Re-run the same 3 test queries (dating, OMR, focus)** after just these two changes. Validation checks:
   - Does `estimated remaining` get confirmed instead of `chrome extension`?
   - Does `Audiveris` become the OMR anchor instead of the relationship-only repo with RRF 0?
   - Does the final narrative correctly reflect that `PerfectReply`-type candidates cover their mechanism?
4. **Only recalibrate ranking weights after steps 1–2**, per the diagnostic report's own conclusion — increasing retrieval depth or confirmation budget now would mostly amplify the same semantic errors, not fix them.
5. **Re-evaluate the "retrieval-depth ceiling" question only after the above fix.** If, after fixing typing/eligibility, there are still cases where a good mechanism genuinely never entered the candidate pool at all (not just mishandled after entering it), that's real evidence for the `chat2.md` proposals. At that point, prioritize the cheaper **Relationship/Graph Discovery** (forks, same-owner, README links — fully evidence-grounded, no verification step, doesn't add load to the already-strained confirmation budget) before the more expensive **GPT Semantic Leap** layer.

## Source Documents

- `chat2.md` — original ChatGPT conversation proposing the semantic-leap architecture and comparing Muse-shroom to Litmaps/ResearchRabbit/Marginalia Search/Are.na/GitRec.
- `muse-shroom-v0_4_7-three-run-raw-analysis-report.md` — raw diagnostic report analyzing 3 real search sessions from persisted SQLite data (selection scores, ranking scores, discovery paths, confirmation budgets) without re-running GitHub search.
- `muse-shroom-v0_4_7-three-run-raw-data.json` — full machine-readable extraction backing the report (all recalled candidates, selection scores; ranking scores only for host-assessed repos).
