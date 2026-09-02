# Muse-shroom v0.4.7 Priority Calibration Analysis Plan

## 修改原因与目标

v0.4.6 已明显改善 confirmation yield：

```text
attempted candidates: 73 → 33
confirmation queries: 93 → 61
Development meaningful yield: 2.4% → 15.8%
Development meaningful precision: 100%
```

但仍未达到 release gate：

```text
Development meaningful: 6 < 7
Holdout known meaningful gain: 0 < 3
queries / meaningful confirmation: 12.2 > 10
```

同时出现一个需要先解释的问题：

```text
Holdout frozen-known meaningful = 0

但 post-hoc blind human review:
5 个 Holdout gain 中 4 个 meaningful
```

因此下一步不要直接调权重，也不要为了恢复 Holdout known gain 增加 benchmark-specific query。

先判断当前失败究竟主要来自：

```text
A. confirmation priority 排序错误
B. confirmation query / early-stop 效率不足
C. frozen taxonomy 对真实新机制覆盖不足
```

本阶段只做分析，不实现 v0.4.7 行为修改。

---

## 1. 重建所有 Confirmation Candidate

基于 v0.4.6 现有：

```text
boundary-v046 release raw results
confirmation-analysis.json
boundary-verdict.json
boundary-v046 cassette
```

不要重新请求 GitHub。

对 Development 8 case + Holdout 6 case 的所有 confirmation candidate 输出：

```text
case
candidate
canonical term
discovery evidence
source repo
source type
request relevance
specificity
novelty score
confirmability score
confirmation priority score
priority reason
queue rank
attempted / skipped
skip reason
queries executed
confirmation evidence
confirmed / rejected / unresolved
human diagnostic label（若已有）
frozen taxonomy match
```

生成：

```text
evaluation/results/boundary-v046-release/priority-analysis.json
evaluation/results/boundary-v046-release/priority-analysis.md
```

---

## 2. 分析 Candidate Ranking 质量

不要只看最终 confirmed 数。

分别计算 Development / Holdout：

```text
meaningful candidate rank distribution
meaningful attempted rate
meaningful skipped rate
top-1 meaningful rate
top-2 meaningful coverage
top-3 meaningful coverage
queries spent on non-meaningful candidates
queries spent before first meaningful confirmation
```

重点回答：

> v0.4.6 是“没有发现有价值 candidate”，还是“发现了但 priority 没选中”？

如果 meaningful candidate 大量存在于 skipped queue，说明 priority calibration 是主问题。

如果 skipped 中也没有 meaningful candidate，则不要继续调 priority。

---

## 3. Blind Review Top Skipped Candidates

Development 中每个 case 最多取：

```text
top 3 skipped candidates
```

进行 Golden-blind diagnostic review。

只展示：

```text
request
candidate
discovery evidence
repo
```

不要展示：

```text
priority score
attempted/skipped 状态
Golden expected mechanism
release verdict
```

标签仍为：

```text
meaningful
noise
wrong_domain
synonym
too_generic
insufficient_evidence
```

Holdout 也可以做一次 **post-hoc blind diagnostic review**，但：

```text
不得回写 Golden
不得修改 aliases
不得进入 production hints
不得参与 query generation
```

目的只是判断 evaluator 是否低估真实 discovery。

---

## 4. 分析 identity verification Adjacency Failure

单独重建：

```text
photo-organization
→ identity verification
```

完整打分链：

```text
discovery source
local evidence
request relevance
specificity
novelty
confirmability
transfer plausibility
priority
confirmation query
confirmation evidence
```

回答：

```text
为什么它通过了 confirmation？
哪个 signal 把“人脸/身份”误判成“照片组织”？
是否存在 domain adjacency 被误当 mechanism transfer？
```

与真正 meaningful 的：

```text
face recognition
perceptual hashing
```

做并列对比。

不要添加针对 `identity verification` 的硬编码 blacklist。

目标是找到可泛化的失败模式。

---

## 5. 分析 Holdout Known = 0 与 Human Meaningful 的差异

对 Holdout 人工 meaningful：

```text
face recognition
question generation
data deduplication
data encryption
```

逐项分类：

### A. Taxonomy / normalization miss

机制实际上属于 frozen taxonomy 已表示的方向，只是 canonicalization 没对齐。

### B. Valid alternative mechanism

是解决该问题的有效机制，但 frozen taxonomy 没列出。

### C. True boundary discovery

明显超出 frozen expected space，但仍具有可迁移价值。

不要修改 Holdout taxonomy。

只统计：

```text
taxonomy_miss_count
valid_alternative_count
true_boundary_discovery_count
```

如果多数属于 B/C，则：

> `Holdout known meaningful gain >= 3`

不应继续作为唯一的 recall release gate。

这时下一阶段应修改 **evaluation policy**，而不是让搜索去追 Golden。

---

## 6. Confirmation Query Cost Analysis

当前：

```text
61 confirmation queries
5 human meaningful confirmations
12.2 queries / meaningful
```

拆解 query 消耗：

```text
confirmed meaningful
confirmed non-meaningful
rejected candidate
skipped candidate
query stage 1
query stage 2
relationship stage
```

统计：

```text
queries_on_eventual_rejects
queries_after_candidate_should_have_been_rejected
average_queries_per_reject
average_queries_per_confirm
stage1_confirmation_rate
stage2_incremental_gain
relationship_incremental_gain
```

重点判断：

> 成本问题来自候选选错，还是每个错误候选花的 query 太多？

---

## 7. Root Cause Ranking

最终只给最多 3 个 root causes。

每个必须包含：

```text
affected cases
trace evidence
candidate examples
metric impact
pipeline stage
```

不要用直觉写：

```text
priority needs tuning
```

必须指出具体哪个 signal / stage 造成错误。

---

## 8. 决定 v0.4.7 实现方向

分析完成后只选择 1～2 个主要修改方向。

### 如果是 Priority Calibration

修改：

```text
confirmation_priority_score
transfer relevance
candidate ordering
```

但不放宽 direct promotion。

### 如果是 Query Efficiency

修改：

```text
cheap pre-confirmation rejection
stage-1 query formulation
early stop
```

但不增加 query budget。

### 如果是 Evaluation Coverage

建立：

```text
frozen known meaningful
+
post-hoc blind meaningful
```

两个独立指标。

Holdout blind labels 只用于 release evaluation，不允许反馈进生产系统。

不要为了 Golden pass 修改搜索行为。

---

## 9. 暂时不要做

不要：

```text
修改 Golden / aliases / thresholds
添加 benchmark phrase hints
放宽 v0.4.4 relevance gate
增加 confirmation budget
扩大普通 Agentic iteration
默认增加 owner/fork traversal
直接调整 priority 权重后重新跑 benchmark
进入 Personal Boundary
```

在 root cause 明确前不要实现 v0.4.7 搜索行为。

---

## 10. 完成标准

分析报告必须明确回答：

```text
1. Development skipped queue 中有多少 meaningful candidate？
2. top-1 / top-2 / top-3 priority 能覆盖多少 meaningful candidate？
3. identity verification 为什么被确认？
4. 61 条 query 中多少浪费在最终 reject 上？
5. Holdout 4 个 human meaningful 中多少是 taxonomy miss / valid alternative / true boundary discovery？
6. Holdout known meaningful = 0 是否真实代表 recall failure？
7. 下一步最应该改 priority、query efficiency，还是 evaluation policy？
8. v0.4.7 最多应该实现哪 1～2 个改动？
```

最终原则：

> **不要为了把 Holdout known meaningful 从 0 调回 3 而追逐 Golden。先确认当前 evaluator 是否正在惩罚真正有价值的 Boundary Discovery。**
