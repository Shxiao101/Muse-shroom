# Muse-shroom v0.4.4 Evidence Precision 实施与验证报告

日期：2026-08-31  
分支：`experiment`  
基线提交：`4983cc5`  
结论：**精度改进有效，但当前候选不建议发布。**

## 1. 实施范围

本轮按 `muse-shroom-v0.4.4-evidence-precision-plan.md` 实施 Evidence Precision，保持搜索架构和既有评测边界不变，未修改 Holdout Golden、aliases、thresholds 或 `DISCOVERY_PHRASE_HINTS`。

主要改动：

- 为候选机制加入 request-to-evidence 相关性评分与可解释原因。
- 区分 core use case 与 incidental mention，压低列表、变更日志、披露页和仅关系共现证据。
- 增加机制 specificity 分类、promotion confidence 与 `promotable` 标记。
- 低相关、低特异性证据继续保留在 trace 中，但不允许进入 promoted boundary。
- 对显式 `promotable: false` 的证据增加迭代层校验，避免被策略绕过。
- 将 `web` 与 `browser` 视为等价表达，降低同义词造成的伪增益。
- 用户请求中已经明确给出的方向可作为待确认方向，但确认后不再计作“新机制”。
- 调整评测策略：优先使用高/中置信度候选；兜底查询组合请求方向、已观察锚点与关系种子，以保持候选池变化。
- 版本号更新为 `0.4.4`。

## 2. 真实 Capture 与离线 Replay

v0.4.4 cassette 从独立于 v0.4.3 的真实 GitHub capture 开始，后续 r2 对策略变化所需调用进行增量补录：

- Cassette：`evaluation/cassettes/boundary-v044.json.gz`
- 最终 capture：`evaluation/results/boundary-v044-release-r2/`
- 独立离线 replay：`evaluation/results/boundary-v044-release-r2-replay/`
- CI replay：`evaluation/results/boundary-v044-release-ci/`

Capture 与 replay 的机制、trace 和未标注 verdict 逐字段一致。Development agentic raw 中仅有两处 `activity` 分数差异：capture 为 `100.0`，replay 为 `99.86`，来自墙钟时间衰减，不影响候选、机制或判定。

## 3. 最终指标

| 指标 | v0.4.3 | v0.4.4 r2 | 结果 |
|---|---:|---:|---|
| 执行 agentic iteration 的案例 | 13/14 | 14/14 | 改善 |
| Planned / executed / retrieval-changing | 22/22/22 | 19/19/19 | 100% 有效执行 |
| Duplicate-only iterations | 0 | 0 | 通过 |
| 产生新确认方向或机制的案例 | 13/14 | 12/14 | 达到 12/14 下限 |
| Raw new mechanisms | 24 | 8 | 明显收缩 |
| Known meaningful gain | 3 | 0 | 退化 |
| Development 盲审队列 | 14 | 5 | 噪声面缩小 |
| Development meaningful | 5/14（35.7%） | 4/5（80.0%） | 比例提高、绝对数下降 |
| wrong_domain | 4 | 0 | 改善 |
| noise | 1 | 0 | 改善 |
| synonym | 3 | 1 | 改善 |
| too_generic | 1 | 0 | 改善 |
| Holdout leakage | 0 | 0 | 通过 |

Development 最终盲审：

| Case | 候选机制 | 标签 |
|---|---|---|
| ai-music | speech recognition | meaningful |
| ai-music | song generation | synonym |
| phone-distraction | gaze tracking | meaningful |
| phone-distraction | landmark detection | meaningful |
| personal-knowledge | prompt foundation | meaningful |

v0.4.3 中的 `scope correction`、`llm attention`、`browser automation`、`web automation`、`ai automation`、`data visualization`、`seo enrichment`、`knowledge workflow`、`digital wellbeing` 均未再被提升。说明 precision gate 确实抑制了已知的错域、噪声和过宽类别。

## 4. 发布门槛判定

| 门槛 | 实际 | 判定 |
|---|---:|---|
| 执行覆盖不少于 12/14 | 14/14 | 通过 |
| retrieval-changing / executed 不低于 90% | 19/19，100% | 通过 |
| duplicate-only 为 0 | 0 | 通过 |
| Holdout leakage 为 0 | 0 | 通过 |
| 新确认方向或机制覆盖不少于 12/14 | 12/14 | 通过 |
| Development meaningful 不少于 7 | 4 | **未通过** |
| wrong_domain 不多于 1 | 0 | 通过 |
| noise 为 0 | 0 | 通过 |
| synonym 不多于 1 | 1 | 通过 |
| 保持已知 meaningful gain | 0，v0.4.3 为 3 | **未通过** |

自动 evaluator 的最终 verdict 为 `fail`。虽然盲审 precision 从 35.7% 提高到 80.0%，但机制发现的绝对召回下降过多：Development meaningful 只有 4，Holdout known meaningful gain 为 0。因此不能只凭 precision rate 宣布 v0.4.4 可发布。

## 5. 验证结果

以下检查均已实际运行并通过：

- Boundary leakage：`ok=true`，`leaks=[]`
- CI frozen replay：schema v4，verdict `pass`
- 单元测试：197 项通过
- Python compileall：通过
- `git diff --check`：通过

新增测试覆盖：相关性与 specificity、umbrella category 留痕但不可提升、web/browser canonicalization、低置信度提升拒绝、请求既有方向不计新机制、兜底查询必须使用锚点和关系种子，以及 synthetic fixture 兼容性。

## 6. 工程结论与下一步

当前实现证明了 Evidence Precision 方向正确：错误类型显著减少，迭代覆盖、候选池变化和离线可复现性均保持良好。但单阶段的严格过滤同时切掉了过多真实机制，尚未达到发布门槛。

建议下一步采用“两阶段机制确认”，而不是放宽当前 extractor：

1. 高置信度、核心用例证据可直接提升。
2. 中/低置信度候选先进入 confirmation queue。
3. 确认查询必须组合候选机制、用户问题和已观察的核心锚点。
4. 只有获得新的 core-use-case 仓库证据，或多个独立仓库支持后，才允许提升。
5. 分别记录 discovery evidence 与 confirmation evidence，避免偶然共现重新进入 boundary。

这样可在不恢复 incidental noise 的前提下补回机制召回。实施前应保留本轮 r2 作为 precision 基线，不修改 Golden 或阈值来制造通过结果。

## 7. 工作区状态

本轮代码和本报告尚未提交、尚未推送。评测 cassette 与结果目录受 `.gitignore` 排除，不会随普通提交进入仓库；提交前需要另行决定是否只纳入本报告，或对体积较大的 cassette 采用外部发布资产。
