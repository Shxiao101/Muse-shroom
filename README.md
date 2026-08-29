# Muse-shroom

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
- rank 输出 `popular` / `gems` / `adjacent`，以及 `display_order`。按该顺序解释 `boundary_role`、`new_mechanisms`、`why_different`。
- 评估必须引用候选上的 evidence ID；功能结论必须引用 README 片段。
- 实现细节见 [`docs/search-internals.md`](docs/search-internals.md)。

## 开发验证

稳定测试使用冻结的 GitHub 响应和行为断言，不把特定仓库视为唯一正确答案：

```console
python -m unittest discover -s tests -v
```

设置 `MUSE_SHROOM_LIVE_SMOKE=1` 后可选运行实时 API 认证/契约 smoke test；稳定测试不会执行它。

人工盲测协议和 8 个模糊需求位于 `evaluation/`。运行 `python evaluation/run_ab.py capture` 可在隔离源码树中录制基线与当前版本的共同 GitHub 响应并生成匿名评审包，`replay` 可完全离线复跑。原始结果同时记录 mechanism count、presented mechanism count、mechanism redundancy、boundary gain 和 direction coverage；这些目前仅作诊断，不改变 release gate。诊断仓库只记录命中情况，不参与发布通过判定。

## 首版边界

没有远程 MCP 服务、独立模型 API、Web UI、云服务、后台监控、自动安装项目或全量 GitHub 索引。`skills/github-inspiration-discovery` 可独立复制到支持 Skills 的宿主中。
