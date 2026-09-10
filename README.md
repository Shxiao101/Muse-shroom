# Muse-shroom 0.7.2

本地 GitHub 搜索内核：CLI 负责可复现的 API 调用、SQLite 缓存和机械校验；需求理解和最终选择留给宿主 Agent（Codex、Claude、Cursor 等）。

不克隆、不运行候选仓库。Token 不写入数据库或日志，默认存进系统凭据存储。

## 安装

Python 3.10+：

```console
pipx install .
muse-shroom auth login
muse-shroom doctor
```

`auth login` 打开 GitHub Fine-grained Token 页，验证后写入 Windows Credential Manager、macOS Keychain 或 Linux Secret Service。`auth status` / `auth logout` 查看或删除。自动化环境可用 `GITHUB_TOKEN`，优先级更高。

开发安装：

```console
python -m pip install -e .
python -m pip install -e ".[mcp]"
muse-shroom --help
```

MCP 是可选 extra。安装后用 `muse-shroom-mcp` 或 `python -m muse_shroom.mcp_server` 以 stdio 启动。MCP 与 CLI 共用同一凭据和 SQLite 目录。

## 工作流

宿主 Agent 使用 [`skills/muse-shroom`](skills/muse-shroom/SKILL.md)：解释需求 → `search` →（深搜）按 `observation` `iterate` → `rank`。快搜跳过 iterate。MCP 可用时优先 `muse_search` / `muse_observe` / `muse_iterate` / `muse_rank`，否则走 CLI，策略相同。契约在 Skill 的 `references/`。

```console
muse-shroom search --request examples/music-ai.request.json --mode quick --output search.json
muse-shroom observe --search-id SEARCH_ID --output observe.json
muse-shroom iterate --search-id SEARCH_ID --refinement examples/focus-tools.hypothesis.json --output iterate.json
muse-shroom rank --search-id SEARCH_ID --selection selection.json --output rank.json
```

默认输出 JSON。输入请用 UTF-8 文件；Windows 不要 `Get-Content | muse-shroom`。`--output` 写完整 JSON，控制台只打回执。相同 request 和 mode 默认复用已完成的 `search_id`，新召回加 `--refresh`。`--data-dir` 覆盖数据目录。多轮必须显式传 `search_id`。

只读浏览已有 session：

```console
muse-shroom explorer
```

默认 `http://127.0.0.1:8765/`，只绑 loopback。非本机地址必须加 `--allow-remote`（无认证）。`rank` 会后台启动 Explorer 并返回 `explorer_url`，不自动开浏览器；`--no-explore` 或 `MUSE_SHROOM_NO_EXPLORER=1` 可关。没有 Explorer 时 Skill / MCP / CLI 功能不变。

## MCP 宿主配置

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

快搜：`search` 然后 `rank`。深搜中间按 `observation` 有限次 `iterate`。`rank` 接收宿主 Agent 的有序 `selection`，只校验证据归属和原文引用，生成 `items` 与 `display_order`，保留该顺序。代码不重排。`popular` / `gems` / `adjacent` 是主列表确定后的兼容投影。

`candidate_count` 是完整召回池，`candidates` 是评估 shortlist，可能不含池中每一项。`rank` 前可用 `candidates --scope all` 或 `inspect` 看未进 shortlist 的证据。细节见 [`docs/search-internals.md`](docs/search-internals.md)。

### 职责边界

- **GitHub 内核**：查询、去重、缓存、README 与元数据、关系扩散、证据记录、预算。
- **Boundary 分析**：机制、相关性、新颖性、覆盖等信号；不决定最终顺序或语义结论。
- **宿主 Agent**：理解目标、提出方向、选择候选、安排展示顺序、解释跨域迁移。

## 开发验证

```console
python -m unittest discover -s tests -v
python -m pip install -e ".[mcp]"
python -m unittest tests.test_mcp -v
python -m pip install -e ".[test]"
```

Core 测试不强制 MCP extra。专项 MCP 测试缺依赖应失败而非 skip。`MUSE_SHROOM_LIVE_SMOKE=1` 才跑实时 API smoke。

人工盲测与 Boundary gate 在 `evaluation/`。`replay --ci` 用已提交的 synthetic fixture 离线回归。`discovery_verdict: not_measured` 与整体 `needs_review` 是确定性 harness 的设计结果，不是回退。发布判断见 `evaluation/ab-protocol.md`。

## 范围

没有远程 MCP 服务、独立模型 API、云端 UI、账号或自动安装。`skills/muse-shroom` 可单独复制到支持 Skills 的宿主。
