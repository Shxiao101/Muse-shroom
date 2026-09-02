# Muse-shroom v0.4.7 Three-Run Raw Diagnostic Report

Date: 2026-09-01

## Scope and Sources

This report analyzes three Codex-hosted Muse-shroom tests without rerunning any GitHub search:

1. AI-assisted online dating
   - Thread: `01a05c0f-6c75-7462-9ac3-eafd686a70b4`
   - Search: `6132fd8d46f5429cbf63856cf66957c7`
2. Optical music recognition (OMR)
   - Thread: `01a05c10-d3e9-74d1-8fc2-5b19e5a7a0d0`
   - Accidental quick search: `8b07cbba50f24b0ca1db63f01598f2e3`
   - Authoritative deep search: `99b71e65851947d78ae6bd5f40395b4e`
3. Focus tools
   - Thread: `01a05c12-ae26-75a3-9d6e-3adcd1d8af1a`
   - Search: `1640f2a5e1c74ae6a830cbd40fe0e0f1`

The source of truth was the persisted SQLite session store at
`C:\Users\SHX\AppData\Local\muse-shroom\muse-shroom.sqlite3`, supplemented by the exact CLI request files where applicable. The complete machine-readable extraction is in `muse-shroom-v0.4.7-three-run-raw-data.json`.

No GitHub request was issued during this analysis.

## Important Score Semantics

Muse-shroom has two distinct scoring layers:

- **Selection scores** exist for every recalled repository: `recall`, `rrf`, `core_concept`, `adjacent_concept`, `relationship`, `popularity_percentile`, `activity`, `underexposure`, and `evidence_completeness`.
- **Ranking scores** exist only for repositories that the host Agent assessed and submitted to `muse_rank`: host assessment scores, four lane scores (`popular`, `gem`, `adjacent`, `boundary`), and final ranking components.

A missing ranking score means **not assessed**, not zero.

There is also a concrete semantic mismatch: selection-stage `relationship` is `0` for query-only candidates, while ranking-stage `_relationship()` assigns a baseline of `30` to any candidate with any discovery path. Thus a query-only repository can display `ranking relationship = 30` despite having no repository relationship edge. This is visible in most rows below and should not be interpreted as actual relationship evidence.

Abbreviations used below:

- `RRF`: normalized reciprocal-rank-fusion score from recall positions.
- `Core` / `Adj`: core and adjacent concept coverage.
- `SelRel`: selection-stage relationship score.
- `Act`: activity score.
- `Under`: underexposure bonus.
- `Evid`: evidence completeness.
- `RankRel`: ranking-stage relationship component.
- `Rel`: host-assessed relevance.
- `BScore`: final boundary lane score.

## 1. AI-Assisted Online Dating

### Actual SearchRequest

The host expanded the original ask into two problem concepts, four mechanisms, and three exploration directions before retrieval:

```text
problem_concepts
- AI 辅助真人网恋 (1.0): AI dating assistant; online dating assistant; dating copilot
- 网恋聊天建议 (0.9): dating conversation coach; dating reply assistant; flirting assistant

mechanisms
- 聊天回复建议 (0.9): reply generator; message suggestions; conversation copilot
- 约会资料优化 (0.75): dating profile optimizer; dating bio generator; profile review
- 对话与关系分析 (0.75): conversation analysis; relationship insights; chat sentiment analysis
- 网恋安全识别 (0.8): romance scam detection; catfish detection; dating safety

exploration_directions
- 隐私优先的本地聊天辅助 (0.75)
- 真人关系沟通教练 (0.7)
- 约会诈骗和身份风险预警 (0.7)

artifact_types
- application; plugin; library; mcp; skill
```

This request is broad, but it is not the primary cause of the observed failure: it explicitly includes reply assistance, and an on-target reply project was recalled and ranked.

### Pool and Query Summary

- Recalled repositories: **231**
- Host-assessed repositories: **8**
- Displayed repositories: **6**
- Executed queries: **38**
- Confirmation queries: **7**
- Confirmation outcome: **0 confirmed, 3 rejected, 7 skipped by candidate budget**

### Top Five: Actual Scores

| Rank | Repository | RRF | Core | Adj | SelRel | Act | Under | Evid | Rel | RankRel | BScore | Role |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | `abhi-yo/trust-dating` | 63.41 | 45.41 | 0.00 | 0 | 44.86 | 80.92 | 100 | 96 | 30 | 76.75 | wildcard |
| 2 | `rotric04/Konvo` | 61.39 | 38.29 | 0.00 | 0 | 93.14 | 90.46 | 90 | 74 | 30 | 65.21 | edge |
| 3 | `EthicalFlipper/Catfish` | 59.50 | 26.28 | 0.00 | 0 | 71.86 | 100.00 | 35 | 92 | 30 | 60.93 | edge |
| 4 | `TomoyamiP/purecomm` | 63.41 | 19.97 | 0.00 | 0 | 49.14 | 87.96 | 60 | 84 | 30 | 59.36 | edge |
| 5 | `MustafaMiyaji/PerfectReply` | 56.88 | 18.94 | 0.00 | 0 | 69.00 | 93.98 | 60 | 77 | 30 | 54.59 | edge |

The display winner was not caused by a bad numeric score: `trust-dating` had the highest relevance and boundary score among the displayed set. The failure was downstream semantic presentation: `PerfectReply` was present at rank 5, yet the host's final answer said reply assistance was not reliably covered.

### Top-Five Discovery Paths

| Rank | Repository | Recall path |
|---:|---|---|
| 1 | `abhi-yo/trust-dating` | refinement `catfish detection`, position 1 |
| 2 | `rotric04/Konvo` | refinement `AI relationship coach`, position 3 |
| 3 | `EthicalFlipper/Catfish` | refinement `catfish detection`, position 5 |
| 4 | `TomoyamiP/purecomm` | refinement `AI relationship coach`, position 1 |
| 5 | `MustafaMiyaji/PerfectReply` | refinement `AI relationship coach`, position 8 |

The good reply candidate was recalled, so this is not a retrieval-depth failure. Its mechanism was not surfaced as a new mechanism, which made the final coverage statement inconsistent with the actual result set.

### Confirmation Budget

Attempted:

| Candidate term | Priority | Max evidence relevance | Queries | Result |
|---|---:|---:|---:|---|
| `fake payment` | 80 | 92 | 3 | rejected: same-repo repetition |
| `comprehensive collaboration` | 78 | 32 | 2 | rejected: same-repo repetition |
| `forensic tampering` | 74 | 66 | 2 | rejected: same-repo repetition |

Highest-value skipped terms:

| Candidate term | Priority | Max evidence relevance | Source | Assessment |
|---|---:|---:|---|---|
| `image recognition` | 77 | 49 | `EthicalFlipper/Catfish` | plausible catfish-detection mechanism |
| `local first` | 75 | 34 | two repositories | requested direction, but generic property rather than mechanism |
| `time feedback` | 71 | 36 | `CARay1502/ai-relationship-coach` | plausible coaching mechanism, weak evidence |

The confirmation budget was inefficient: seven queries produced zero confirmations, while a plausible catfish mechanism was never attempted. However, most skipped terms were noise or product properties, so this run alone does not prove pure anchor starvation.

## 2. Optical Music Recognition

### Actual SearchRequest

The host first ran a quick search without waiting for the requested mode, then ran a second deep search after the user chose `deep`.

The initial request for both searches was:

```text
problem_concepts
- 光学乐谱识别 (1.0): optical music recognition; sheet music recognition; music score recognition; OMR
- 乐谱数字化 (0.9): sheet music digitization; score digitization; MusicXML conversion

mechanisms
- 深度学习乐谱识别 (0.9): deep learning OMR; neural OMR; end-to-end OMR
- 五线谱符号检测 (0.75): music symbol detection; staff detection; note recognition
- 结构化乐谱导出 (0.7): MusicXML export; MEI export; MIDI export

exploration_directions
- 可直接使用的乐谱扫描应用 (0.75): sheet music scanner; OMR application; score scanner
- 可嵌入项目的乐谱识别引擎 (0.8): OMR engine; OMR library; music recognition API
- 手写乐谱识别 (0.55): handwritten music recognition; handwritten OMR

artifact_types
- application; library
```

During the deep session, confirmed terms `music notation` and `mensural notation` were appended as exploration directions with weight `1.0`. They were not part of the initial host interpretation.

### Pool and Query Summary

Accidental quick search:

- Recalled: **58**
- Assessed: **8**
- Executed queries: **12**

Authoritative deep search:

- Recalled: **171**
- Assessed: **11**
- Displayed: **10**
- Executed queries: **34**
- Confirmation queries: **6**
- Confirmation outcome: **2 confirmed, 1 rejected, 6 skipped by candidate budget**

### Top Five: Actual Scores

| Rank | Repository | RRF | Core | Adj | SelRel | Act | Under | Evid | Rel | RankRel | BScore | Role |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | `apacha/MusicObjectDetector-TF` | 0.00 | 46.16 | 35.09 | 45 | 0.00 | 60.72 | 90 | 87 | 45 | 79.88 | anchor |
| 2 | `Audiveris/audiveris` | 25.13 | 46.74 | 40.64 | 90 | 99.00 | 31.23 | 55 | 97 | 90 | 80.56 | wildcard |
| 3 | `gh-romi/nanoScore` | 22.22 | 56.16 | 49.61 | 0 | 85.43 | 100.00 | 90 | 88 | 30 | 74.92 | wildcard |
| 4 | `Jinsizongzi/homr_gui` | 41.88 | 44.04 | 24.33 | 85 | 92.71 | 93.98 | 45 | 94 | 85 | 71.05 | edge |
| 5 | `312205675/toolpure-omr` | 45.59 | 41.32 | 23.05 | 0 | 96.29 | 100.00 | 45 | 96 | 30 | 65.67 | edge |

`Audiveris` had higher relevance, activity, popular score, and boundary score than the displayed winner. The winner had **RRF 0** and entered only through a same-owner relationship from `apacha/OMR-Datasets`.

This was not a simple ranking-weight loss. The composer forced an anchor before MMR selection. `Audiveris` was marked `matched_kinds = adjacent` because it came from an exploration query, so it was excluded from the mainstream anchor pool and later labeled a wildcard. `MusicObjectDetector-TF`, despite being relationship-only and inactive, satisfied the anchor-pool conditions. The wrong winner is therefore primarily a **retrieval-origin classification and composition-rule problem**.

### Top-Five Discovery Paths

| Rank | Repository | Recall path |
|---:|---|---|
| 1 | `apacha/MusicObjectDetector-TF` | same-owner relationship from `apacha/OMR-Datasets`; no query recall |
| 2 | `Audiveris/audiveris` | exploration `OMR application`, position 1; README link from `Lucas0623z/NoteLite` |
| 3 | `gh-romi/nanoScore` | confirmation-anchor query `mensural notation` + `OMR`, position 9 |
| 4 | `Jinsizongzi/homr_gui` | problem position 4; gem positions 2 and 4; reverse-README relationship |
| 5 | `312205675/toolpure-omr` | problem position 6; gem position 2; typed query position 2 |

Direct OMR candidates were recalled successfully. Retrieval depth is not the principal failure.

### Confirmation Budget

Attempted:

| Candidate term | Priority | Max evidence relevance | Queries | Result |
|---|---:|---:|---:|---|
| `jeongganbo notation` | 87 | 100 | 3 | rejected: same-repo repetition |
| `music notation` | 87 | 91 | 1 | confirmed |
| `mensural notation` | 71 | 74 | 2 | confirmed |

Skipped:

| Candidate term | Priority | Max evidence relevance | Source |
|---|---:|---:|---|
| `loss function` | 87 | 100 | `jimitshah77/Focal-CTC-OMR` |
| `dataset preparation` | 86 | 100 | `stefaniacerboni/DDM_HandwrittenMusicRecognition` |
| `performance analysis` | 86 | 100 | same repository |
| `background motivation` | 85 | 100 | `Lucas0623z/NoteLite` |
| `music information retrieval` | 79 | 39 | two repositories |
| `musical notation` | 70 | 78 | `Tsukamotoshio/SumisoraOMR` |

There is real budget pressure, but several skipped terms are section phrases or workflow components rather than user-facing mechanisms. The strongest diagnosis is not simply “increase the budget”; candidate phrase quality must improve before budget allocation can be trusted.

## 3. Focus Tools

### Actual SearchRequest

The initial host interpretation was:

```text
problem_concepts
- 专注管理 (1.0): focus management; focus productivity; deep work
- 减少数字干扰 (0.9): distraction reduction; digital wellbeing; attention management

mechanisms
- 番茄钟与专注计时 (0.9): pomodoro timer; focus timer; work break timer
- 网站与应用屏蔽 (0.9): website blocker; app blocker; distraction blocker
- 时间追踪与专注统计 (0.75): time tracking; activity tracking; focus analytics
- 极简任务执行 (0.7): minimal task manager; single task; task focus

exploration_directions
- 承诺装置与自控训练 (0.65)
- 环境音与专注空间 (0.55)
- 终端或状态栏专注工作流 (0.55)
- 本地优先与隐私友好 (0.6)

artifact_types
- application; plugin; mod
```

The final session request also contained iteration-added directions `本地网站与应用阻断`, `环境音和声音遮蔽`, and `chrome extension`, each at weight `1.0`.

`chrome extension` is an artifact type, not a mechanism. Its promotion into exploration directions is an upstream taxonomy error before final ranking.

This test also did not exercise the previously proposed boundary of “focus without blockers or Pomodoro,” because the actual SearchRequest explicitly requested both blockers and Pomodoro.

### Pool and Query Summary

- Recalled repositories: **171**
- Host-assessed repositories: **12**
- Displayed repositories: **10**
- Executed queries: **36**
- Confirmation queries: **5**
- Confirmation outcome: **1 confirmed, 2 rejected, 8 skipped by candidate budget**

### Top Five: Actual Scores

| Rank | Repository | RRF | Core | Adj | SelRel | Act | Under | Evid | Rel | RankRel | BScore | Role |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | `super-productivity/super-productivity` | 24.12 | 46.02 | 0.00 | 0 | 100.00 | 13.26 | 70 | 95 | 30 | 77.00 | leap |
| 2 | `1372Slash/Zenith` | 36.75 | 39.26 | 0.00 | 0 | 100.00 | 47.35 | 70 | 87 | 30 | 76.25 | edge |
| 3 | `curbox-app/curbox-android` | 100.00 | 53.56 | 3.93 | 0 | 99.57 | 37.90 | 100 | 96 | 30 | 79.73 | wildcard |
| 4 | `namuan/active-breaks` | 22.05 | 31.19 | 0.00 | 0 | 65.57 | 81.94 | 80 | 80 | 30 | 70.13 | edge |
| 5 | `Varrow1/AppTimer` | 24.50 | 39.58 | 0.00 | 0 | 0.00 | 87.96 | 80 | 84 | 30 | 68.58 | edge |

`Curbox` had the strongest recall, core coverage, evidence completeness, and boundary score, but it appeared at rank 3. `Super Productivity` was forced into rank 1 as the mainstream anchor and was then labeled `leap` solely because its requested mechanisms were considered newly presented. This is another composer/role-semantics issue, not a retrieval miss.

### Top-Five Discovery Paths

| Rank | Repository | Recall path |
|---:|---|---|
| 1 | `super-productivity/super-productivity` | mechanism `pomodoro timer`, position 4 |
| 2 | `1372Slash/Zenith` | refinement `digital wellbeing`, position 3 |
| 3 | `curbox-app/curbox-android` | refinement `distraction blocker`, position 2; refinement `digital wellbeing`, position 2; confirmation-anchor `screen addiction`, position 1 |
| 4 | `namuan/active-breaks` | mechanism `work break timer`, position 10 |
| 5 | `Varrow1/AppTimer` | confirmation-anchor `screen addiction` + `deep work`, position 3 |

Again, the strongest direct candidate was recalled. The problem is composition and semantic labeling after recall.

### Confirmation Budget

Attempted:

| Candidate term | Priority | Max evidence relevance | Queries | Result |
|---|---:|---:|---:|---|
| `chrome extension` | 85 | 58 | 1 | confirmed |
| `integrated timeboxing` | 76 | 96 | 2 | rejected: same-repo repetition |
| `screen addiction` | 76 | 78 | 2 | rejected: same-repo repetition |

Highest-value skipped terms:

| Candidate term | Priority | Max evidence relevance | Source | Assessment |
|---|---:|---:|---|---|
| `estimated remaining` | 82 | 70 | `AmazingKeymaster/Memento-Mori` | genuine mortality-salience/commitment mechanism |
| `brave extension` | 78 | 82 | `FardeenRahman13/Devoted` | artifact/platform phrase, not mechanism |
| `browser extension` | 76 | 73 | `malekwael229/FocusTube` | artifact type, not mechanism |
| `habit tracker` | 69 | 54 | `super-productivity/super-productivity` | plausible but already mainstream-adjacent |
| `phone detection` | 68 | 31 | CV distraction project | genuinely different, but weak request evidence |

This is the clearest confirmation failure of the three runs. The system spent its highest-priority slot confirming `chrome extension`, then promoted it as a mechanism, while the stronger `estimated remaining` mechanism was skipped by budget. This directly supports the anchor-starvation hypothesis, but the root is **candidate type classification plus confirmation priority**, not insufficient total budget.

## Cross-Run Comparison

| Run | Were strong direct repos recalled? | Primary failure | Same as other runs? |
|---|---|---|---|
| Dating | Yes: `trust-dating`, `PerfectReply`, `Catfish` | mechanism extraction and final coverage statement mismatch | No |
| OMR | Yes: `Audiveris`, multiple direct OMR tools | exploration-origin candidate misclassified as adjacent; hard anchor composition selected relationship-only repo | Partly shared with focus |
| Focus | Yes: `Curbox`, `Super Productivity`, `Memento-Mori` | artifact type promoted as mechanism; confirmation budget favored it; boundary roles misleading | Partly shared with OMR |

The three runs do **not** show one identical “broad mechanism beats specific mechanism” failure.

What is consistent:

1. Retrieval found relevant direct candidates in all three runs.
2. The post-retrieval semantic layer was unreliable: mechanism extraction, origin-based `matched_kinds`, confirmation candidate typing, and boundary-role assignment changed how good candidates were presented.
3. Final display order is composition-driven, not a descending sort of any single lane score. A simple weight adjustment cannot fix hard anchor-pool eligibility or wrong mechanism types.

What differs:

1. Dating mostly failed in coverage interpretation and host presentation after a reasonable ranking.
2. OMR failed because a direct application recalled through an exploration query was treated as adjacent, while a relationship-only project with RRF 0 became the mandatory anchor.
3. Focus failed because an artifact type was confirmed as a mechanism and consumed the slot that should have gone to a genuine behavioral mechanism.

## Root-Cause Ranking

### 1. Semantic typing and state pollution after retrieval

Evidence:

- `chrome extension` became an exploration direction, a confirmed candidate, and a displayed new mechanism.
- `estimated remaining` was skipped despite being a genuine different mechanism.
- `PerfectReply` was ranked but did not count as covering reply assistance.
- OMR mechanism labels attached handwritten novelty to the wrong project while the dedicated handwritten project had no new mechanism.

Impact: all three runs, highest confidence.

### 2. Hard composition and role rules override stronger direct evidence

Evidence:

- OMR rank 1 had RRF 0 and activity 0, while `Audiveris` had higher relevance, activity, popular score, and boundary score.
- `Audiveris` was excluded from the anchor pool because its retrieval origin marked it adjacent.
- Focus rank 1 had a lower boundary score than `Curbox`, yet the latter was labeled wildcard.

Impact: OMR and focus, high confidence.

### 3. Confirmation budget is spent on poorly typed phrases

Evidence:

- Dating: 7 confirmation queries, 0 confirmations.
- Focus: `chrome extension` confirmed while `estimated remaining` was skipped.
- OMR: multiple score-100 evidence phrases were skipped, but several were document-section or workflow phrases rather than mechanisms.

Impact: all three runs, high confidence. The safe fix is better pre-confirmation typing and rejection, not a larger budget.

## Answer to the Main Diagnostic Question

This is **not primarily a retrieval-depth problem**. The good candidates were already in the pool and often in the top five.

It is also **not fixable by ranking weights alone**. Two structural rules dominate the visible outcome:

1. retrieval origin can classify a direct solution as adjacent and remove it from anchor eligibility;
2. confirmation and boundary state can treat artifact types or incidental README phrases as mechanisms.

The first implementation work should therefore target:

1. mechanism candidate typing and normalization, including a hard separation between mechanism, artifact type, product property, domain label, and README section phrase;
2. anchor/role composition eligibility based on direct problem evidence and usability, not query origin or “new mechanism” status alone.

Only after those changes should ranking weights be recalibrated. Increasing retrieval depth or confirmation budget now would mostly amplify the same semantic errors.

## Data Completeness and Limitations

- The raw JSON contains the full candidate list and selection scores for all four persisted search sessions associated with the three tests.
- Ranking components exist only for host-assessed repositories by design.
- `request_json` is mutable during deep iteration. The raw export labels it `search_request_final`; the report separately identifies initial versus appended directions.
- Confirmation “candidates” are extracted mechanism terms, not repository candidates. They must not be conflated with the recalled repository pool.
- The analysis did not independently judge all 631 persisted candidate rows; it compared scores, paths, assessments, and confirmation records and manually inspected the high-impact rows.
