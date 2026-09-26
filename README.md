# Muse-shroom 0.13.2

```
灵感菇哩菇哩菇哩哇擦灵感菇灵感菇
```

探索一个需求在 GitHub 上的解法边界，找能激发新思路的项目，而不只是最直接的答案。每条结果带一个边界角色和 README 原文证据。

- `anchor` 是这类需求下的常见选择，用作参照。
- `edge` 解决相近的问题，实现方式不同。
- `leap` 已经不在常见的解法路线上。
- `wildcard` 表面与需求无关，某个机制可以迁移。

后两类需要 ⌈深搜/deep⌋ 模式才能找到。⌈深搜⌋ 依据每一轮的结果决定下一步方向，前两轮允许提出一个跨领域的假设。⌈快搜/quick⌋ 模式下只搜索一轮。

搜索、缓存和取证都由本地 GitHub 搜索内核完成，CLI 负责可复现的 API 调用、SQLite 缓存和机械校验。宿主 Agent 读懂需求并挑出结果，决定展示顺序。

GitHub token 默认存放在系统的凭据管理中。

## 环境要求

Python 3.10+，以及一个支持 MCP 的宿主 Agent。

## 安装与登录

```console
pipx install "muse-shroom[mcp] @ git+https://github.com/Shxiao101/Muse-shroom"
muse-shroom auth login
muse-shroom doctor
```

使用 `auth login` 打开 GitHub 的 Fine-grained Token 创建页，名称和用途已经预填，默认九十天到期。将生成的 token 贴回终端，验证通过后写入 Windows 凭据管理器、macOS Keychain 或 Linux Secret Service。

使用 `doctor` 输出一行 JSON，包含 Python 版本、数据库位置以及 token 状态。出现 `"github_token":"missing"` 表示尚未登录。

自动化环境可以直接设置 `GITHUB_TOKEN` 环境变量，它的优先级高于已保存的凭据。

使用 `auth status` 查看状态，`auth logout` 删除。

## 在 Agent 中使用

Codex 的配置写进 `~/.codex/config.toml`。

```toml
[mcp_servers.muse-shroom]
command = "muse-shroom-mcp"
```

Claude Code 用一条命令注册。

```console
claude mcp add muse-shroom -- muse-shroom-mcp
```

Cursor 写进 `.cursor/mcp.json`。

```json
{
  "mcpServers": {
    "muse-shroom": {
      "command": "muse-shroom-mcp"
    }
  }
}
```

配置完成后用日常语言提出需求即可，比如「用 Muse-shroom 找能让本地 AI 记忆可查看的工具」。

## 工作流

Agent 按 [`skills/muse-shroom`](skills/muse-shroom/SKILL.md) 执行，问一次会经过这些步骤。

1. Agent 把你的话整理成问题概念和机制，发起搜索。
2. 程序发出十二条查询，回收七十余个候选，取回 README 作为证据，交出十二条待评估。⌈快搜⌋ 到此为止。
3. ⌈深搜⌋ 时 Agent 读完每轮结果再决定下一步方向，最多三轮。
4. Agent 按平常方式自己也搜一遍，把找到的仓库交回程序取证。
5. 程序逐条核对引用是否与记录下来的 README 原文一致，通过的进入最终清单，顺序由 Agent 给出。

## 已经展示过的仓库

排序过的仓库会被记录下来。同一需求再次搜索时，结果以新项目为主，此前给出过的只在清单末尾列出链接，不再重复推荐。需要一并查看时，告诉 Agent「包括以前看过的」。记录保存在数据目录中，宿主配置里设置 `MUSE_SHROOM_DATA_DIR` 即可换一份新的记录。

## 浏览结果（可选）

```console
muse-shroom explorer
```

默认地址 `http://127.0.0.1:8765/`，只监听本机。允许其他机器访问需要加 `--allow-remote`，该模式没有认证。`rank` 完成后会在后台启动 Explorer 并返回地址，不会自动打开浏览器，`--no-explore` 可以关闭。

## 范围

内部实现见 [`docs/search-internals.md`](docs/search-internals.md)。[`skills/muse-shroom`](skills/muse-shroom/SKILL.md) 可以单独复制到其他支持 Skills 的宿主。每个版本改了什么见 [CHANGELOG.md](CHANGELOG.md)，提 issue 或改动见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

[MIT LICENSE](LICENSE)。
