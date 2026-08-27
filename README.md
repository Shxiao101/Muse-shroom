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
muse-shroom --help
```

## 工作流

1. Agent 根据 [`examples/music-ai.request.json`](examples/music-ai.request.json) 生成结构化需求。
2. 快搜调用一次 `search` 和一次 `rank`；深搜在两者之间调用 `expand`。
3. Agent 只根据候选中的 evidence IDs 生成语义评价。
4. CLI 合并元数据、关系证据、类型质量规则和评价，输出热门、宝藏、跨界三个榜。

```console
muse-shroom search --request examples/music-ai.request.json --mode quick
muse-shroom expand --search-id SEARCH_ID --refinement examples/music-ai.refinement.json
muse-shroom rank --search-id SEARCH_ID --assessments assessments.json
muse-shroom inspect Quackone/homr_gui --search-id SEARCH_ID
muse-shroom feedback Quackone/homr_gui --relevant yes --interesting yes --too-hard no
echo '{"repo":"Quackone/homr_gui","relevant":true,"interesting":true,"too_hard":false}' | muse-shroom feedback --input -
```

所有命令默认输出 JSON。`--format text` 仅用于人工查看。`--data-dir` 可覆盖平台标准数据目录，便于隔离测试。原计划中的 `repo-radar` 命令作为兼容别名继续可用。

## 结果约束

- 快搜最多生成 12 条受控查询，首轮富化 30 个候选。
- 深搜从种子沿 README 链接、README 反向引用、Fork 和作者仓库扩散，并受请求预算限制。
- 推荐理由必须引用已采集的 evidence ID；未知能力应写成 `unknown`，不能从仓库名猜测。
- 榜单上限为热门 4、宝藏 4、跨界 2；质量不足时少给，不填充。
- Star 增长只有本地存在至少两个快照时才显示。
- API 失败时只有对应请求已有缓存才返回旧数据，并明确标记 `stale`、缓存时间和未完成阶段。

## 开发验证

测试完全使用冻结的 GitHub 响应，不依赖实时排名：

```console
python -m unittest discover -s tests -v
```

设置 `REPO_RADAR_LIVE_SMOKE=1` 后可选运行实时 API 认证/契约 smoke test；稳定测试不会执行它。

## 首版边界

首版没有 MCP、独立模型 API、Web UI、云服务、后台监控、自动安装项目或全量 GitHub 索引。`skills/github-inspiration-discovery` 可独立复制到支持 Skills 的宿主中。
