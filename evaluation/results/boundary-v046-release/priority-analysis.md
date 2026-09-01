# v0.4.7 Priority Calibration Analysis

Date: 2026-08-31

## Decision

Offline diagnosis of v0.4.6. No production search behavior, Golden, aliases, thresholds, or v0.4.6 artifacts were changed.

## Root causes

1. **priority_selection_misses_meaningful_candidates** (`confirmation_candidate_selection`)
   - Cases: codex-overthinking, learning-habit, long-project-motivation, personal-knowledge, phone-distraction
   - Candidates: `asynchronous execution`, `prompt collaboration`, `instant feedback`, `data synchronization`, `landmark detection`, `object detection`
   - Trace evidence: `{"meaningful_skipped_count": 11, "development_top_3_meaningful_coverage": 0.286}`
   - Metric impact: Meaningful candidates are present in the queue but do not receive an attempt slot.
   - Confidence: high

2. **confirmation_queries_spend_on_non_independent_evidence** (`progressive_confirmation_queries`)
   - Cases: ai-music, code-review, codex-overthinking, focus, indie-churn, learning-habit, long-project-motivation, long-writing-consistency, meeting-efficiency, personal-knowledge, phone-distraction, photo-organization, remote-information-loss
   - Candidates: `gui automation`, `code generation`, `parallel execution`, `epic planning`, `local first`, `colored feedback`
   - Trace evidence: `{"development_queries_on_eventual_rejects": 32, "development_same_repo_overlap_rate": 0.571, "stage2_incremental_confirmation_count": 0, "stage3_incremental_confirmation_count": 0}`
   - Metric impact: Executed query budget is consumed without independent core-use-case gain.
   - Confidence: high

3. **frozen_taxonomy_underrepresents_valid_discovery** (`evaluation_coverage`)
   - Cases: family-digital-preservation, long-writing-consistency, photo-organization
   - Candidates: `face recognition`, `question generation`, `data deduplication`, `data encryption`
   - Trace evidence: `{"named_holdout_human_meaningful_count": 4, "taxonomy_miss_count": 0, "valid_alternative_count": 3, "true_boundary_discovery_count": 1}`
   - Metric impact: Frozen-known recall can undercount valid alternatives and boundary discoveries.
   - Confidence: medium

## Candidate metrics

| Metric | Development | Holdout |
| --- | ---: | ---: |
| meaningful_candidate_count | 14 | 7 |
| meaningful_attempted_count | 3 | 4 |
| meaningful_skipped_count | 11 | 3 |
| meaningful_skipped_rate | 0.786 | 0.429 |
| top_1_meaningful_coverage | 0.0 | 0.286 |
| top_2_meaningful_coverage | 0.143 | 0.714 |
| top_3_meaningful_coverage | 0.286 | 0.857 |
| meaningful_attempted_rate | 0.214 | 0.571 |
| non_meaningful_attempted_rate | 0.0 | 1.0 |

## Query cost

| Metric | Development | Holdout |
| --- | ---: | ---: |
| total_executed_queries | 35 | 26 |
| queries_on_confirmed_meaningful | 3 | 2 |
| queries_on_confirmed_non_meaningful | 0 | 1 |
| queries_on_eventual_rejects | 32 | 23 |
| queries_on_labeled_non_meaningful_candidates | 0 | 1 |
| queries_on_unresolved | 0 | 0 |
| query_failure_count | 0 | 0 |
| queries_with_same_repo_overlap | 20 | 13 |
| queries_with_independent_repo_result | 24 | 6 |
| queries_with_independent_repo_gain | 4 | 1 |
| stage1_query_count | 19 | 14 |
| stage2_query_count | 0 | 2 |
| stage3_query_count | 16 | 10 |
| stage1_confirmation_count | 3 | 2 |
| stage2_incremental_confirmation_count | 0 | 0 |
| stage3_incremental_confirmation_count | 0 | 0 |
| average_queries_per_confirm | 1.0 | 1.0 |
| average_queries_per_reject | 2.0 | 2.091 |
| queries_before_first_meaningful_confirmation | 4 | 0 |
| same_repo_overlap_rate | 0.571 | 0.5 |

The two suites executed 61 queries. Of these, 55 were spent on candidates whose final confirmation status was `rejected`. Query failures were counted separately and were zero.

## Holdout taxonomy classification

The following table is limited to the four human-meaningful final Holdout gains named in the v0.4.6 review. Historical diagnostic candidates are retained separately in JSON and do not change these counts.

| Case | Candidate | Class | Basis |
| --- | --- | --- | --- |
| photo-organization | `face recognition` | B | Direct person-recognition evidence supports photo grouping, but frozen taxonomy does not express this mechanism and it is not a surface alias. |
| long-writing-consistency | `question generation` | C | StorySage uses dynamic question generation to surface missing facts; this transfers a distinct elicitation mechanism into long-form consistency checking. |
| family-digital-preservation | `data deduplication` | B | Direct archive and backup evidence supports eliminating redundant stored content, without a frozen taxonomy equivalent. |
| family-digital-preservation | `data encryption` | B | Direct archive and backup evidence supports protecting preserved data at rest, without a frozen taxonomy equivalent. |

Counts: {'A': 0, 'B': 3, 'C': 1}
Evaluator coverage warning: `True`.

## Identity verification adjacency analysis

A use-case list placed identity verification beside personal photo organization. The pipeline treated request adjacency plus core_use_case as transfer support even though mechanism_anchored was false and specificity was project_category; stage 1 then found the same adjacency pattern in a second repository. No domain-role check distinguished photo grouping from identity/access verification.

- `discovery_repos`: ['tusharsingh011/face_recognition_system']
- `source_fields`: ['readme_concept_match']
- `discovery_evidence`: [{'repo': 'Tusharsingh011/Face_recognition_system', 'source_field': 'readme_concept_match', 'evidence_id': 'repo:tusharsingh011/face_recognition_system:readme:concept_match', 'evidence_text': 'Personal photo organization Access-control prototypes Identity verification research Computer vision projects Academic AI/ML projects', 'confidence': 0.86, 'evidence_relevance_score': 90, 'evidence_relevance_reason': 'core_context,local_problem,repo_request_alignment,assessment_shortlist', 'core_use_case': True, 'request_anchored': True, 'mechanism_anchored': False, 'retrieval_stage': 'confirmation'}]
- `core_use_case`: [True]
- `request_anchored`: [True]
- `mechanism_anchored`: [False]
- `evidence_relevance_score`: 90
- `mechanism_specificity`: project_category
- `novelty_score`: 100
- `confirmability_score`: 73
- `transfer_plausible`: False
- `priority`: 81
- `priority_reason`: request_anchored,core_use_case,category_like,strong_source
- `query_details`: [{'query': '"identity verification" "personal photo organization" in:name,description,topics,readme is:public archived:false', 'query_stage': 'stage1', 'query_kind': 'confirmation_problem', 'query_position': 1, 'query_result_count': 2, 'new_repo_count': 1, 'independent_repo_count': 1, 'independent_core_evidence_repo_count': 1, 'same_repo_overlap': True, 'new_core_evidence': True, 'query_failed': False, 'result_repos': ['awnishk23/facematchnet', 'tusharsingh011/face_recognition_system'], 'candidate': 'identity verification', 'final_candidate_label': 'wrong_domain'}]
- `confirmation_evidence`: [{'repo': 'Awnishk23/FaceMatchNet', 'source_field': 'readme_use_cases', 'evidence_id': 'repo:awnishk23/facematchnet:readme:use_cases', 'evidence_text': '## Use Cases Access Control Systems** Identity Verification** Security Applications** Attendance Systems** Personal Photo Organization**', 'confidence': 0.86, 'evidence_relevance_score': 92, 'evidence_relevance_reason': 'core_context,local_problem,repo_problem', 'core_use_case': True, 'request_anchored': True, 'mechanism_anchored': False, 'retrieval_stage': 'confirmation'}]
- `final_status`: confirmed
- `human_diagnostic_label`: wrong_domain

Comparison:

| Candidate | Deduped rank | Relevance | Specificity | Priority | Status | Human label |
| --- | ---: | ---: | --- | ---: | --- | --- |
| `identity verification` | 4 | 90 | project_category | 81 | confirmed | wrong_domain |
| `face recognition` | 2 | 70 | behavioral_signal | 87 | rejected | meaningful |
| `perceptual hashing` | 6 | 78 | project_category | 76 | skipped_budget | meaningful |

## Top skipped Development candidates

### focus
- `ai development dashboard` — too_generic
- `ai progress tracking` — too_generic
- `desktop automation` — insufficient_evidence

### codex-overthinking
- `asynchronous execution` — meaningful
- `prompt collaboration` — meaningful
- `tool execution` — insufficient_evidence

### learning-habit
- `interview preparation` — too_generic
- `instant feedback` — meaningful
- `data synchronization` — meaningful

### ai-music
- `image generation` — wrong_domain
- `image harmonization` — wrong_domain
- `object placement` — wrong_domain

### phone-distraction
- `opencv visualization` — insufficient_evidence
- `landmark detection` — meaningful
- `object detection` — meaningful

### code-review
- `requirements generation` — meaningful
- `fixed decision` — insufficient_evidence
- `performance optimization` — too_generic

### long-project-motivation
- `security review` — wrong_domain
- `workflow evaluation` — meaningful
- `agentic workflow` — insufficient_evidence

### personal-knowledge
- `explicit decision` — meaningful
- `content recommendation` — too_generic
- `contradiction tracking` — meaningful

## Required answers

1. Development has 11 labeled meaningful candidates in the skipped deduped queue.
2. Top-1/top-2/top-3 meaningful coverage — Development: 0.0 / 0.143 / 0.286; Holdout: 0.286 / 0.714 / 0.857.
3. Meaningful attempted/skipped — Development: 3 / 11; Holdout: 4 / 3.
4. `identity verification` passed because request-adjacent use-case lists produced `core_use_case=true`, `request_anchored=true`, relevance 90, and a stage-1 second-repository match. It remained `mechanism_anchored=false` and `project_category`; the missing domain-role check allowed identity/access verification to masquerade as photo organization.
5. Queries on eventual rejects: 55/61 (90.2%): Development 32, Holdout 23.
6. New meaningful confirmations by stage: stage 1 = 5; stage 2 = 0; stage 3 = 0.
7. The four named Holdout human-meaningful candidates classify as A/B/C = 0 / 3 / 1.
8. Holdout frozen-known meaningful=0 is primarily an evaluator-coverage limitation, not zero real recall: all four reviewed gains are B/C and were surfaced as unknown gains. The existing release gate remains unchanged.
9. Root-cause order: candidate priority selection; confirmation query independence/efficiency; frozen taxonomy coverage.
10. Next implementation is limited to: Priority calibration, Query efficiency. No confirmation budget increase is recommended.

## Provenance

- Network requests: 0
- v0.4.6 artifacts modified: false
- Production behavior changed: false
- Reconstructed fields are declared in JSON under `reconstructed_fields` and per record under `field_provenance`.
- Completion status: diagnosis_complete, root_causes_ranked, implementation_directions_limited_to_1_or_2.
