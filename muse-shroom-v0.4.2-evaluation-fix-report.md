# Muse-shroom v0.4.2 Evaluation Fix Report

## 1. 结论

本轮已完成 `muse-shroom-v0.4.2-evaluation-fix-guide.md` 要求的评估可信度改造。

评估系统现在能够分别判断：

- recall pool 是否重复；
- final presentation 是否重复；
- 是否产生 raw Boundary gain；
- gain 是已知 meaningful、Golden 外 unknown，还是同机制改写等 invalid gain；
- development 与 holdout 是否分别达标；
- production phrase hints 是否泄漏 holdout 答案。

代码、离线 fixture 和测试均通过，但真实 GitHub development + holdout release gate 的最终 verdict 为：

```text
fail
```

这不是评估脚本执行失败，而是新的可信评估明确显示：当前搜索能产生一些新方向，但尚未稳定发现 Golden mechanism space 中有意义的跨机制方向。

## 2. 已完成修改

### 2.1 Retrieval 与 Presentation Redundancy 拆分

新增：

```text
retrieval_mechanism_redundancy
presentation_mechanism_redundancy
redundancy_scope
```

兼容字段 `mechanism_redundancy` 保留，并明确映射到 presentation scope。

Golden `repetition_groups` 现在只检查：

```text
ranking.items
```

没有 ranking 时才回退到 `selected_for_assessment` 或 `candidates`，并通过 `redundancy_scope` 标记范围。Recall pool 高重复不再直接导致最终展示失败。

### 2.2 Boundary Gain 分类

Evaluator 现在区分：

```text
boundary_gain
meaningful_boundary_gain
unknown_boundary_gain
invalid_boundary_gain
```

分类行为：

- Golden acceptable mechanism：known meaningful；
- mainstream rewording 或 repetition-group synonym：invalid；
- Golden 未覆盖但带 evidence 的新机制：进入 `unknown_mechanisms` review queue；
- unknown 不再被自动丢弃或直接视为 meaningful。

Unknown queue 保留 term、iteration、repos 和 evidence sources。

### 2.3 Development / Holdout 隔离

保留原有 8 个 case 作为 development/regression suite，并新增 6 个不同领域的 holdout case：

- 个人照片整理；
- 团队会议低效；
- 独立产品用户流失；
- 远程协作信息丢失；
- 长篇写作设定一致性；
- 家庭数字资料长期保存。

Holdout expected mechanisms 不进入 SearchRequest、deterministic policy 或 production phrase hints。

最终 evaluator 输出：

```json
{
  "development": {},
  "holdout": {},
  "verdict": "pass | fail | needs_review | insufficient_data | leakage_detected"
}
```

### 2.4 Benchmark Leakage Gate

新增：

```console
python evaluation/check_boundary_leakage.py
```

检查 production `DISCOVERY_PHRASE_HINTS` 与 holdout 的：

- expected terms；
- aliases；
- cross-mechanism directions。

比较前会 normalized；发现重合时返回非零退出码并输出具体来源。

当前结果：

```json
{
  "ok": true,
  "leaks": []
}
```

### 2.5 Production Discovery 与 Agentic Policy 收口

生产常量由 `DISCOVERY_MECHANISM_PHRASES` 重命名为 `DISCOVERY_PHRASE_HINTS`，并注明其只是 optional precision aid，禁止根据 holdout 扩展。

同时增加通用结构化 phrase 规则，并过滤：

- `agent`、`llm`、`awesome list` 等普通项目分类噪声；
- SearchRequest 明确 exclusions 中的词；
- 过于宽泛的 README workflow heading。

Deterministic policy 的允许输入限制为：

1. evidence-backed `candidate_mechanism`；
2. evidence-backed `cross_domain_direction`；
3. 用户原有 `unexplored_directions`。

普通 `project_category` 不再被提升为新机制，避免制造伪 Boundary gain。Golden/holdout 文件不会被该 policy 读取。

### 2.6 Offline CI Fixture

新增稳定 synthetic cassette：

```text
evaluation/fixtures/boundary-ci-v1.json.gz
```

支持 fresh clone 无网络回归：

```console
python evaluation/run_boundary_eval.py replay --ci
```

Fixture 生成过程固定 `captured_at` 和 gzip mtime；连续构建 SHA-256 一致。当前 fixture SHA-256：

```text
56508FC7678C172E6DEEB39FE401CEC98F79FC2373722BEE3C57BF850AAFAD0E
```

真实 GitHub cassette 继续保存在 gitignored 的 `evaluation/cassettes/`，不会进入仓库。

### 2.7 Windows 输出兼容

修复 `boundary_eval.py` 在 Windows GBK 控制台输出带 emoji 的 GitHub evidence 时触发 `UnicodeEncodeError` 的问题。Evaluator 现在显式使用 UTF-8 stdout/stderr。

## 3. 测试覆盖

新增或强化以下测试：

```text
test_retrieval_redundancy_does_not_fail_diverse_presentation
test_repetitive_presentation_fails_even_with_diverse_recall
test_holdout_terms_are_not_in_production_phrase_hints
test_unknown_mechanism_is_reported_for_review
test_same_mechanism_rewording_is_not_meaningful_gain
test_holdout_golden_is_not_visible_to_agentic_policy
test_fresh_fixture_replay_is_offline
test_agentic_policy_does_not_promote_project_category_as_mechanism
```

最终验证：

```text
python -m unittest discover -s tests
185 tests passed

python -m compileall -q src evaluation tests
passed

python evaluation/check_boundary_leakage.py
passed, 0 leaks

python evaluation/run_boundary_eval.py replay --ci
passed

git diff --check
passed
```

## 4. 真实 GitHub Evaluation 结果

真实 capture 首次使用 2.1 秒 Search API 间隔时在 development 5/8 遇到 GitHub 403；cassette 已增量保存。调整到 4 秒间隔后从已有 cassette 续录并完成全部 8 个 development case 与 6 个 holdout case。

结果文件位于 gitignored 目录：

```text
evaluation/results/boundary-v042-eval-fix/boundary-verdict.json
```

### Development

```text
case count:                         8
agentic case count:                 4
agentic boundary expansion share:   0.375
meaningful boundary expansion:      0.000
unknown mechanism review count:     6
median retrieval redundancy:        0.813
median presentation redundancy:     0.0715
verdict:                             fail
```

### Holdout

```text
case count:                         6
agentic case count:                 3
agentic boundary expansion share:   0.167
meaningful boundary expansion:      0.000
unknown mechanism review count:     3
median retrieval redundancy:        0.809
median presentation redundancy:     0.000
verdict:                             fail
```

### 解读

新 evaluator 成功证明了 redundancy scope 拆分的必要性：recall pool 的 mechanism redundancy 仍约为 0.81，但 final presentation 已降到 0～0.0715，因此最终失败不再是由完整 recall pool 的重复误罚造成。

失败的核心原因是：

- 只有部分 case 产生有效 agentic iteration；
- raw expansion 不稳定；
- development 与 holdout 均未命中 known meaningful mechanism；
- 部分通用 evidence 只能进入 unknown review，不能自动视为成功。

## 5. 剩余风险与下一阶段建议

本轮目标是修复 Evaluation 可信度，不是通过补 benchmark 关键词制造 release pass。真实结果说明后续应单独进入 Boundary Discovery 能力提升阶段。

建议优先级：

1. 提升 description / curated README 中通用 mechanism phrase 的精度；
2. 改善 evidence-backed query generation，使 agentic iteration 能跨出用户已给出的主流机制；
3. 对 unknown review queue 做人工盲审，区分 meaningful、noise、synonym；
4. 检查高 retrieval redundancy 的 query recall 结构，但不以牺牲 final presentation 为代价；
5. 每次通用修改后重跑全部 development + holdout，禁止根据 holdout expected aliases 补词。

在这些问题解决前，v0.4.2 的 Evaluation Framework 可以合入，但不应宣称真实 Boundary Discovery release gate 已通过。
