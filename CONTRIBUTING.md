# 参与

**Issue 最有用。** 装不上、搜出来的东西不对、README 看不懂、Agent 没按预期调用它，都写在 [Issues](https://github.com/Shxiao101/Muse-shroom/issues)。带上 `muse-shroom doctor` 输出的那行 JSON——它只有 Python 版本、数据库位置和 token 配没配好，不含 token 本身。

如果是搜索结果本身的问题，再带上那次的 `search_id`。会话记在本地 SQLite 里，`muse-shroom explorer` 能把那一轮发出的查询、召回的候选和最终清单都翻出来。

**改代码。**

```console
git clone https://github.com/Shxiao101/Muse-shroom
cd Muse-shroom
python -m pip install -e ".[mcp]"
```

改完至少跑一遍 `muse-shroom doctor`，再拿一条真实需求走完 search → rank。动到查询构造、打分或者取证的部分，说清楚你怎么确认它没有把别的东西弄坏。

**PR 说明写三件事**：改了什么、为什么、怎么验证的。合并前我会在本地完整跑一遍。
