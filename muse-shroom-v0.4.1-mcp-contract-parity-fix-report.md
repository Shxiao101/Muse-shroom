# Muse-shroom v0.4.1 — MCP Contract Parity 修复报告

## 日期

2026-08-30

## 修复范围

本报告记录 `muse-shroom-v0.4.1-mcp-postpush-review.md` 中 Issue 1 的修复结果：

> Published MCP Schema 与 Runtime Strict Parser 还没有完全对齐。

目标是让 MCP boundary 使用的 `strict=True` runtime parser 独立执行与 published JSON Schema 一致的类型、枚举和嵌套结构校验，不能依赖 MCP Host 是否执行 JSON Schema。

## 已完成修改

### 1. SearchRequest 严格类型校验

在 `src/muse_shroom/models.py` 中补齐了以下 strict 校验：

- `request` 必须是 string
- `problem_concepts`、`mechanisms`、`exploration_directions` 必须是数组
- `artifact_types` 必须是字符串数组
- `artifact_types` 只能包含：
  - `application`
  - `mcp`
  - `skill`
  - `mod`
  - `plugin`
  - `library`
- `exclusions` 必须是字符串数组
- `exploration_level` 必须是 number
- `constraints.language` 必须是 string
- `constraints.pushed_after` 必须是 `YYYY-MM-DD` 格式
- `constraints.include_archived` 必须是 boolean
- `constraints.min_stars` / `max_stars` 必须是真正的非负 integer

这样可以避免例如字符串 `"false"` 被 Python 当作 truthy 值，错误改变 archived filtering 语义。

### 2. Concept 严格类型校验

strict 模式下：

- `term` 必须是 string
- `aliases` 必须是字符串数组
- `weight` 必须是 number
- 不再依赖 `str(...)` 将错误类型自动转换为合法值
- `null` aliases 在 strict 模式下拒绝

### 3. Assessment 严格类型与枚举校验

strict 模式下：

- `repo` 必须是 string
- `relevance`、`uniqueness`、`usability` 必须是 number
- `difficulty` 只能是：
  - `easy`
  - `medium`
  - `hard`
  - `unknown`
- `use_case` 必须是 string
- `category` 必须是 string
- `artifact_type` 只能是：
  - `application`
  - `mcp`
  - `skill`
  - `mod`
  - `plugin`
  - `library`
  - `unknown`
- 可选字段 `mechanism` 必须是 string
- 可选字段 `transferability` / `boundary_value` 必须是 number，不能为 `null`

### 4. Claim 严格类型校验

`reasons` 和 `risks` 中的 Claim 现在要求：

- `text` 必须是 string
- `evidence_ids` 必须是字符串数组
- `text` 与 `evidence_ids` 都必须存在
- `evidence_ids` 至少包含一个元素
- 未知字段仍会被拒绝

### 5. SearchHypothesis 与 ExplorationAddition

同步加强了 strict 模式下的已发布字段校验：

- `decision` 必须是精确的 `continue` 或 `stop`
- optional text 字段不能传入非 string 或 `null`
- `strategies` 必须使用已发布的枚举值，显式 `null` 会被拒绝
- `add_exploration_directions` 的字段类型与 `source_iteration` integer 约束得到执行，显式 `null` 不再按缺省值处理

### 6. 统一 enum 来源

在 `models.py` 中集中定义：

- `SEARCH_ARTIFACT_TYPES`
- `ASSESSMENT_ARTIFACT_TYPES`
- `ASSESSMENT_DIFFICULTIES`

`mcp_schema.py` 复用这些常量生成 published schema，避免 published schema 与 runtime parser 使用两套可能漂移的 enum 定义。

## 新增回归测试

修改文件：

- `tests/test_contracts_and_queries.py`
- `tests/test_mcp.py`

覆盖的非法 payload 包括：

```text
artifact_types = "application"          → reject
artifact_types = ["banana"]             → reject
constraints.include_archived = "false"  → reject
request = 123                            → reject
concept.term = 123                       → reject
assessment.artifact_type = "banana"     → reject
reason.evidence_ids = "repo:..."         → reject
strategies = null                       → reject
source_iteration = null                 → reject
```

同时验证了完整合法 v0.4 payload 仍然可以通过。

## 验证结果

使用 Windows 环境中现有的 Muse-shroom Python 环境执行。

### 定向 contract 测试

```text
python -m unittest tests.test_contracts_and_queries -v
```

结果：

```text
Ran 25 tests
OK
```

### MCP 集成测试

```text
python -m unittest tests.test_mcp -v
```

结果：

```text
Ran 14 tests
OK
```

### 完整测试集

```text
python -m unittest discover -s tests -v
```

结果：

```text
Ran 154 tests
OK
```

### 额外检查

以下检查通过：

```text
python -m py_compile src/muse_shroom/models.py src/muse_shroom/mcp_schema.py tests/test_contracts_and_queries.py tests/test_mcp.py
git diff --check
```

## 当前结论

```text
Published MCP Schema 与 Runtime Strict Parser 对齐  ✅
SearchRequest 类型 / enum 校验                  ✅
Concept 类型校验                               ✅
Assessment 类型 / enum 校验                   ✅
Claim evidence_ids 类型校验                   ✅
MCP regression tests                           ✅
完整测试集                                    ✅
```

Issue 1 已修复。

## 尚未完成事项

原 review 中的 Issue 2 仍然需要单独完成：

```text
真实 fresh-host MCP live test                  ⏳
```

当前仓库内的 `test_fresh_agent_focus_flow_uses_v04_schema_and_one_search_id` 已通过，但它直接使用测试代码构造的 `FOCUS_V04` payload。它证明正确的 v0.4 flow 可以稳定执行，尚不能完全替代一个没有项目上下文的真实 MCP Host 黑盒验收。

建议后续使用全新的 MCP Host 会话，只输入：

```text
使用 muse-shroom，帮我找提高专注力的 GitHub 工具。
```

并确认：

- 第一次 `muse_search` 使用 v0.4 nested schema
- 不猜 `query` / `prompt`
- 不意外使用 legacy `core_concepts`
- 全流程复用同一个 `search_id`
- `muse_observe`、`muse_iterate`、`muse_rank` 均符合 contract
- Assessment 字段完整且 evidence-backed
- Boundary 与 Explorer 状态能够正确恢复

## Git 状态说明

基础 contract parity 修复已通过 commit `4b61d23` 推送到 `origin/experiment`；本次两个 null parity 补丁随本报告纳入后续提交。已有的 blackbox/review 未跟踪文件未被修改。
