# Muse-shroom 0.4.10

Muse-shroom 是一个“让当前 Agent 帮你打破 GitHub 信息茧房”的本地搜索内核。它把可复现的 GitHub API 调用、SQLite 缓存、关系扩散和确定性排名放进 Python CLI，把自然语言理解和语义评价留给 Codex、Claude、Cursor 等宿主 Agent。

它不会克隆或运行候选仓库，也不会把 GitHub Token 写入数据库或日志。默认使用系统凭据存储保存 Token。

## 安装

需要 Python 3.10+。安装后运行一次交互式登录：

```console
pipx install .
muse-shroom auth login
muse-shroom doctor
```

`auth login` 会打开 GitHub Fine-grained Token 创建页，在终端中隐藏读取 Token，验证成功后保存到 Windows Credential Manager、macOS Keychain 或 Linux Secret Service。可用以下命令管理：

```console
muse-shroom auth status
muse-shroom auth logout
```

自动化环境仍可设置 `GITHUB_TOKEN`；它的优先级高于系统凭据存储。开发态可运行：

```console
python -m pip install -e .
python -m pip install -e ".[mcp]"
muse-shroom --help
```

MCP 是可选 extra。安装 `[mcp]` 后可用 `muse-shroom-mcp` 或 `python -m muse_shroom.mcp_server` 以 stdio 启动。

## 工作流

宿主 Agent 使用 [`skills/github-inspiration-discovery`](skills/github-inspiration-discovery/SKILL.md)：解释需求 → `search` →（深搜）按 `observation` `iterate` → `rank`。快搜是 `search` 然后 `rank`。MCP 可用时优先调用 `muse_search` / `muse_observe` / `muse_iterate` / `muse_rank`；否则走 CLI。策略相同。

v0.4 请求把语义拆成 `problem_concepts`、`mechanisms`、`exploration_directions`。契约在 Skill 的 `references/` 下。

```console
muse-shroom search --request examples/music-ai.request.json --mode quick --output search.json
muse-shroom observe --search-id SEARCH_ID --output observe.json
muse-shroom iterate --search-id SEARCH_ID --refinement examples/focus-tools.hypothesis.json --output iterate.json
muse-shroom rank --search-id SEARCH_ID --assessments assessments.json --output rank.json
```

所有命令默认输出 JSON。JSON 输入请保存为 UTF-8 文件；Windows 不要使用 `Get-Content | muse-shroom`。`--output` 把完整 JSON 写到文件，控制台只打印回执。相同 request 和 mode 默认复用已完成的 `search_id`，需要新召回时加 `--refresh`。`--data-dir` 可覆盖平台数据目录。MCP 与 CLI 共用同一凭据存储和 SQLite 目录；多轮工具必须显式传 `search_id`。

本地只读 Explorer 浏览已有 session 的 Boundary、迭代和最终推荐，不发起 search / iterate / rank：

```console
muse-shroom explorer
```

默认打开 `http://127.0.0.1:8765/`，只绑定 loopback。Explorer 子命令支持 `--host`、`--port`、`--no-browser`；数据目录仍用全局 `--data-dir`（`muse-shroom --data-dir DIR explorer`）。绑定 `0.0.0.0` 等非本机地址必须显式加 `--allow-remote`（无认证，会暴露本地搜索数据）。Skill / MCP / CLI 不依赖 Explorer。`?debug=1` 才显示 selection_order、score components 和 query history。

## MCP 宿主配置

安装 `muse-shroom[mcp]` 后，本地 stdio 入口是 `muse-shroom-mcp`（或 `python -m muse_shroom.mcp_server`）。可选 `--data-dir`。进程只读取已有 GitHub 凭据，不返回 token。

Codex（`~/.codex/config.toml`）：

```toml
[mcp_servers.muse-shroom]
command = "muse-shroom-mcp"
```

Claude Code：

```console
claude mcp add muse-shroom -- muse-shroom-mcp
```

Cursor（`.cursor/mcp.json`）：

```json
{
  "mcpServers": {
    "muse-shroom": {
      "command": "muse-shroom-mcp"
    }
  }
}
```

## 结果

- 快搜一次 `search` 后 `rank`（`next_action` 为 `rank` 再为 `done`）；深搜在中间按 `observation` 做有限次 `iterate`。
- 深搜会按 novelty、confirmability 和 evidence priority 选择少量中等置信度新机制进入独立 confirmation stage，并按 problem、observed anchor、seed 顺序逐条查询和及时停止；只有新的 core-use-case、多仓库一致支持或明确跨域迁移证据才会提升。`confirmation_queue`、`mechanism_confirmations` 及 attempted/skipped/yield 统计与普通 iteration 分开记录。
- rank 由一个 Boundary-first composer 直接生成 `items` + `display_order`，共同优化 Anchor、Edge、Leap、Wildcard、新机制覆盖与重复控制。`popular` / `gems` / `adjacent` 只是在主列表确定后生成的兼容投影，不参与排序。
- 评估必须引用候选上的 evidence ID；功能结论必须引用 README 片段。
- 实现细节见 [`docs/search-internals.md`](docs/search-internals.md)。

## 开发验证

稳定测试使用冻结的 GitHub 响应和行为断言，不把特定仓库视为唯一正确答案。Core 测试不强制安装 MCP extra：

```console
python -m unittest discover -s tests -v
```

MCP 是 CLI 用户的 optional extra。专项 MCP 测试必须先安装 extra；缺依赖或 SDK API 不兼容时应失败，而不是 skip：

```console
python -m pip install -e ".[mcp]"
python -m unittest tests.test_mcp -v
```

完整本地验证也可 `python -m pip install -e ".[test]"` 后再跑 `discover`。后续若增加 CI，MCP job 需要显式安装 `.[mcp]` 并运行 `python -m unittest tests.test_mcp -v`。

设置 `MUSE_SHROOM_LIVE_SMOKE=1` 后可选运行实时 API 认证/契约 smoke test；稳定测试不会执行它。

人工盲测协议和 8 个模糊需求位于 `evaluation/`。运行 `python evaluation/run_ab.py capture` 可在隔离源码树中录制基线与当前版本的共同 GitHub 响应并生成匿名评审包，`replay` 可完全离线复跑。Boundary release gate 分开报告 8 个 development case 与 6 个 holdout case；`python evaluation/run_boundary_eval.py capture` 录制完整 agentic 流程，之后用 `replay` 离线重放并自动生成 verdict。Golden Cases 只参与结果评分，不会注入搜索策略，`python evaluation/check_boundary_leakage.py` 会阻止 holdout 答案进入生产 phrase hints；`replay --ci` 可在 fresh clone 中使用已提交的 synthetic fixture 离线回归。人工 A/B release gate 仍独立运行。

### 如何读 release verdict

Boundary release gate 把两类证据分开报告，不再合成一个数字：

- **mechanics**（`mechanics_verdict`）—— evidence-backed promotion、duplicate query rate、query 演化、repetition、invalid gain。确定性 harness 能完整决定，必须 `pass`。
- **discovery**（`discovery_verdict`）—— mainstream coverage、meaningful new mechanism、cross-domain transfer。需要真实宿主 Agent 参与判断。

评测 harness 使用确定性 hypothesis 策略（`evaluation/version_worker.py`），该策略只能提升当前 observation 已给出的证据，**按构造无法产生跨域跳跃**。因此在这个 harness 下 `discovery_verdict` 恒为 `not_measured`，`cross_mechanism_status` 恒为 `not_measured`。

**这是设计结果，不是缺陷。** 整体 verdict 因此长期停在 `needs_review`：两个 mechanics gate 都 `pass` 已经是当前 harness 能给出的最强状态。要拿到整体 `pass`，必须先有 host-in-the-loop 的 discovery 评测，那部分尚未实现。不要把 `not_measured` 或 `needs_review` 当成质量回归来排查。

同理，confirmation 精度有多个分母不同的指标：`confirmation_precision` 只统计命中 Golden 答案的确认，`blind_precision` 只统计人工盲标为 meaningful 的确认。单看任何一个都会低估实际质量。

## 首版边界

没有远程 MCP 服务、独立模型 API、云端 Web UI、账号系统、后台监控、自动安装项目或全量 GitHub 索引。本地 `muse-shroom explorer` 只读浏览已有 session。`skills/github-inspiration-discovery` 可独立复制到支持 Skills 的宿主中。
