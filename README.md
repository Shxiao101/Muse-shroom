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
3. CLI 先按概念覆盖探针富化最多 30 个候选的 README，再重排出最多 12 个短名单，只为短名单读取最新 Release。
4. Agent 只根据候选中的 evidence IDs 生成语义评价，功能结论必须引用 README 片段。
5. CLI 合并元数据、关系证据、类型质量规则和评价，输出热门、宝藏、跨界三个榜。

v0.4 的 request 将语义拆成三层：`problem_concepts` 描述真正要解决的问题，`mechanisms` 描述具体解决机制，`exploration_directions` 描述值得继续外扩的方向。旧版 `core_concepts` / `adjacent_concepts` 仍可读取，并会转换为新结构。

```console
muse-shroom search --request examples/music-ai.request.json --mode quick --output search.json
muse-shroom expand --search-id SEARCH_ID --refinement examples/music-ai.refinement.json --output expand.json
muse-shroom rank --search-id SEARCH_ID --assessments assessments.json --output rank.json
muse-shroom inspect Quackone/homr_gui --search-id SEARCH_ID
muse-shroom candidates --search-id SEARCH_ID --scope assessment
muse-shroom candidates --search-id SEARCH_ID --scope all
muse-shroom feedback Quackone/homr_gui --relevant yes --interesting yes --too-hard no
```

所有命令默认输出 JSON。JSON 输入请保存为 UTF-8 文件；Windows 不要使用 `Get-Content | muse-shroom`。`-` 只接受非交互 stdin。`--output` 把完整 JSON 写到文件，控制台只打印回执。相同 request 和 mode 默认复用已完成的 `search_id`，需要新召回时加 `--refresh`。`--format text` 仅用于人工查看。`--data-dir` 可覆盖平台标准数据目录，便于隔离测试。

## 结果约束

- 快搜最多生成 12 条受控查询；别名不会扩大 API 预算。同一概念组的多次字段查询会增强可信度，但 RRF 按概念组封顶。
- 搜索输出使用 schema v2；`candidate_count` 是完整召回数，`candidates` 只包含最多 12 个评审短名单。
- `boundary.recalled_mechanisms` 统计完整候选池中有证据的机制，`presented_mechanisms` 只统计当前短名单或最终榜单；同一机制下多个仓库只计一次。
- mechanism 只根据 description、Topics 或 README 的实际文本匹配；仓库名与 Star 不作为机制证据。公开候选使用统一的 `mechanism_match` evidence，`mechanisms[].evidence_ids` 可直接引用，不再内嵌第二套 evidence。
- `boundary.mechanism_origins` 将有证据的请求机制与经证据确认的 exploration direction 分组；`discovered_terms` 仍是未确认术语，不会自动升级为 mechanism。
- `explored_directions` / `unexplored_directions` 描述探索边界，`discovered_terms` 保存少量有机制证据的候选 Topics，供下一次人工 refinement 使用，但不会自动继续搜索。
- search、每次 expand、rank 都会在 SQLite 中追加 boundary snapshot；输出的 `boundary_delta` 是相对前一 snapshot 的新增机制、展示机制、方向和术语。
- 探针阶段同一 owner 最多 2 个；短名单按核心代表 3、小众宝藏 4、跨界灵感 2、概念桥接 3 分配。
- 低 Star 不能单独成为宝藏或桥接理由；必须有非泛化查询来源和 README/元数据相关证据。
- 搜索 JSON 不超过 30KB；超限时只压缩次要字段，并保留每个候选的首条概念证据及其 README SHA/行号。每个候选默认最多 3 条证据：metadata、concept_match（或有效 overview），以及检测到的 mechanism_match；没有 mechanism 时第三条保留 usage/installation。Release 放在 `latest_release`，不占 evidence 槽。
- 深搜从种子沿 README 链接、README 反向引用、Fork 和作者仓库扩散，并受请求预算限制。
- README 富化最多提取 5 条不可信证据片段；默认 JSON 每个候选最多保留 3 条证据。原文仅保存在 SQLite。
- 单独的 `skill` / `AI` / `agent` 不会作为核心查询；形态词应放在 `artifact_types`。中文概念整词保留。概念可以带最多 4 个 GitHub 常用别名，同一组别名只计一次分。
- 推荐理由必须引用已采集的 evidence ID；功能结论必须引用具体 README 片段，未知能力应写成 `unknown`。
- 榜单上限为热门 4、宝藏 4、跨界 2；质量不足时少给，不填充。
- Star 增长只有本地存在至少两个快照时才显示。
- 网络失败、5xx 或确认限流时，只有对应请求已有缓存才返回旧数据并标记 `stale`；401、404 和查询错误不会回退缓存。

## 开发验证

稳定测试使用冻结的 GitHub 响应和行为断言，不把特定仓库视为唯一正确答案：

```console
python -m unittest discover -s tests -v
```

设置 `MUSE_SHROOM_LIVE_SMOKE=1` 后可选运行实时 API 认证/契约 smoke test；稳定测试不会执行它。

人工盲测协议和 8 个模糊需求位于 `evaluation/`。运行 `python evaluation/run_ab.py capture` 可在隔离源码树中录制基线与当前版本的共同 GitHub 响应并生成匿名评审包，`replay` 可完全离线复跑。原始结果同时记录 mechanism count、presented mechanism count、mechanism redundancy、boundary gain 和 direction coverage；这些目前仅作诊断，不改变 release gate。诊断仓库只记录命中情况，不参与发布通过判定。

## 首版边界

首版没有 MCP、独立模型 API、Web UI、云服务、后台监控、自动安装项目或全量 GitHub 索引。`skills/github-inspiration-discovery` 可独立复制到支持 Skills 的宿主中。
