const main = document.getElementById("main");
const drawer = document.getElementById("drawer");
const drawerBody = document.getElementById("drawer-body");
const drawerMask = document.getElementById("drawer-mask");
const debugFlag = document.getElementById("debug-flag");
const langToggle = document.getElementById("lang-toggle");
const themeToggle = document.getElementById("theme-toggle");
const tagline = document.getElementById("tagline");
const skipLink = document.querySelector(".skip");

/* ---------------------------------------------------------------------------
 * i18n — UI chrome only.
 * Repository content (description, topics, README excerpts) and Agent-written
 * text (use_case, category, reasons) are data and are never translated.
 * ------------------------------------------------------------------------- */

const STRINGS = {
  zh: {
    tagline: "只读浏览 Search Boundary · 不发起搜索",
    langButton: "中文",
    langLabel: "语言",
    skip: "跳到主要内容",
    loadFailed: "无法加载 Explorer",
    back: "返回",
    backToList: "← 搜索列表",
    backToResults: "← 结果",
    backToOverview: "← 概览",

    listTitle: "本地搜索",
    listSub: "选择一个 session，看它探索到哪里、又漏掉了什么。Explorer 只读，不会调用 GitHub。",
    listEmpty: "还没有 search session。用 CLI / MCP / Skill 跑完一次 search 后再打开 Explorer。",
    colRequest: "请求", colMode: "模式", colStatus: "状态",
    colIteration: "轮次", colMechanism: "机制", colResult: "结果", colTime: "时间",

    navResults: "结果", navFrontier: "未探索", navOverview: "概览",
    copyId: "复制 search_id", copyContinue: "在 Agent 中继续", copied: "已复制",

    overviewProblem: "问题", overviewRequested: "指定机制", overviewExploration: "探索方向",
    overviewRecalled: "已召回", overviewPresented: "已展示",
    timelineTitle: "迭代过程", timelineEmpty: "还没有 boundary snapshot。",
    graphTitle: "探索图谱",

    resultsTitle: "结果", resultsEmpty: "尚未 rank。Agent 完成评估后再看最终推荐。",
    resultsSub: "每张卡片是一个仓库。点开可以看它为什么不同，以及支持这个判断的证据。",
    historicalNotice: "正在查看历史快照，这一轮还没有最终排名。",
    backToFinal: "回到最终结果",
    allRoles: "全部",
    roleEmpty: "这次搜索没有属于这个角色的结果。",
    themeAuto: "跟随系统", themeLight: "浅色", themeDark: "深色", themeLabel: "配色",
    stars: "star", noDescription: "没有仓库描述",

    whyTitle: "为什么不同", introduces: "引入机制",
    evidenceTitle: "证据", fromReadme: "来自该仓库 README",
    useCaseTitle: "用途", categoryLabel: "分类", difficultyLabel: "上手难度",
    risksTitle: "风险", mechanismsTitle: "机制", scoresTitle: "评分（debug）",
    openOnGitHub: "在 GitHub 打开", noEvidence: "没有记录证据。",
    notCloned: "Explorer 不会 clone 或安装任何仓库。",

    frontierTitle: "这次搜索没去的地方",
    frontierSub: "未探索方向是下一次搜索的起点。复制后交给你的 Agent 即可继续。",
    frontierUnexplored: "未探索方向", frontierCovered: "已探索",
    frontierRejected: "已排除", frontierNegative: "明确排除",
    frontierEmpty: "没有留下未探索方向。",
    copyDirection: "复制请求",
    frontierReadonly: "Explorer 不会自己发起搜索，只把请求交给你。",

    roleAnchor: "Anchor", roleEdge: "Edge", roleLeap: "Leap", roleWildcard: "Wildcard",
    glossAnchor: "该领域公认的主流项目",
    glossEdge: "同类问题的邻近做法",
    glossLeap: "用不同机制解决同一问题",
    glossWildcard: "跨域迁移，风险与惊喜并存",

    statusSearched: "已搜索", statusIterating: "探索中",
    statusRanked: "已排名", statusIncomplete: "不完整",
    none: "—",
  },
  en: {
    tagline: "Read-only view of the search boundary · never starts a search",
    langButton: "EN",
    langLabel: "Language",
    skip: "Skip to main content",
    loadFailed: "Could not load the Explorer",
    back: "Back",
    backToList: "← All searches",
    backToResults: "← Results",
    backToOverview: "← Overview",

    listTitle: "Local searches",
    listSub: "Pick a session to see where it explored and what it missed. The Explorer is read-only and never calls GitHub.",
    listEmpty: "No search sessions yet. Run a search through the CLI, MCP or Skill first.",
    colRequest: "Request", colMode: "Mode", colStatus: "Status",
    colIteration: "Rounds", colMechanism: "Mechanisms", colResult: "Results", colTime: "Updated",

    navResults: "Results", navFrontier: "Not explored", navOverview: "Overview",
    copyId: "Copy search_id", copyContinue: "Continue in Agent", copied: "Copied",

    overviewProblem: "Problem", overviewRequested: "Requested mechanisms", overviewExploration: "Exploration directions",
    overviewRecalled: "Recalled", overviewPresented: "Presented",
    timelineTitle: "Iterations", timelineEmpty: "No boundary snapshot yet.",
    graphTitle: "Exploration graph",

    resultsTitle: "Results", resultsEmpty: "Not ranked yet. Come back once the Agent finishes its assessment.",
    resultsSub: "Each card is one repository. Open it to see why it is different and the evidence behind that claim.",
    historicalNotice: "You are viewing an earlier snapshot. This round had no final ranking yet.",
    backToFinal: "Back to final results",
    allRoles: "All",
    roleEmpty: "No results in this role for this search.",
    themeAuto: "Follow system", themeLight: "Light", themeDark: "Dark", themeLabel: "Theme",
    stars: "stars", noDescription: "No repository description",

    whyTitle: "Why this is different", introduces: "Introduces",
    evidenceTitle: "Evidence", fromReadme: "from this repository's README",
    useCaseTitle: "What it's for", categoryLabel: "Category", difficultyLabel: "Difficulty",
    risksTitle: "Risks", mechanismsTitle: "Mechanisms", scoresTitle: "Scores (debug)",
    openOnGitHub: "Open on GitHub", noEvidence: "No evidence recorded.",
    notCloned: "The Explorer never clones or installs a repository.",

    frontierTitle: "Where this search didn't go",
    frontierSub: "An unexplored direction is the start of the next search. Copy one and hand it to your Agent.",
    frontierUnexplored: "Not explored", frontierCovered: "Covered",
    frontierRejected: "Ruled out", frontierNegative: "Explicitly excluded",
    frontierEmpty: "No unexplored directions were left.",
    copyDirection: "Copy request",
    frontierReadonly: "The Explorer never starts a search itself — it hands the request to you.",

    roleAnchor: "Anchor", roleEdge: "Edge", roleLeap: "Leap", roleWildcard: "Wildcard",
    glossAnchor: "the established mainstream option",
    glossEdge: "a nearby approach to the same problem",
    glossLeap: "solves it by a different mechanism",
    glossWildcard: "a cross-domain transfer — risk and surprise",

    statusSearched: "Searched", statusIterating: "Exploring",
    statusRanked: "Ranked", statusIncomplete: "Incomplete",
    none: "—",
  },
};

const LANG_KEY = "muse-shroom-explorer-lang";

function detectLang() {
  try {
    const stored = localStorage.getItem(LANG_KEY);
    if (stored === "zh" || stored === "en") return stored;
  } catch (error) {
    /* private windows and blocked site data throw on access; fall through */
  }
  const nav = (navigator.language || "").toLowerCase();
  return nav.startsWith("zh") ? "zh" : "en";
}

let lang = detectLang();

function t(key) {
  return (STRINGS[lang] && STRINGS[lang][key]) ?? STRINGS.zh[key] ?? key;
}

function applyLangChrome() {
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  // Both toolbar toggles show the CURRENT setting, never the next one:
  // a moon means dark is active, so "中文" must mean Chinese is active.
  langToggle.textContent = t("langButton");
  langToggle.setAttribute("aria-label", `${t("langLabel")}: ${t("langButton")}`);
  langToggle.setAttribute("title", `${t("langLabel")}: ${t("langButton")}`);
  tagline.textContent = t("tagline");
  if (skipLink) skipLink.textContent = t("skip");
  applyTheme();
}

function setLang(next) {
  lang = next;
  try {
    localStorage.setItem(LANG_KEY, next);
  } catch (error) {
    /* switching still works for this session even when storage is unavailable */
  }
  applyLangChrome();
  const scroll = window.scrollY;
  render().then(() => window.scrollTo(0, scroll));
}

/* ---------------------------------------------------------------------------
 * Theme — auto (follow the OS) / light / dark, the way a system toggle works.
 * Light is the base palette in CSS; dark overrides tokens only.
 * ------------------------------------------------------------------------- */

const THEME_KEY = "muse-shroom-explorer-theme";
const THEME_ORDER = ["auto", "light", "dark"];

function detectTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (THEME_ORDER.includes(stored)) return stored;
  } catch (error) {
    /* blocked site data throws on access; fall back to following the OS */
  }
  return "auto";
}

let theme = detectTheme();

function applyTheme() {
  const root = document.documentElement;
  if (theme === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  themeToggle.setAttribute("data-mode", theme);
  const label = `${t("themeLabel")}: ${t(`theme${theme.charAt(0).toUpperCase()}${theme.slice(1)}`)}`;
  themeToggle.setAttribute("aria-label", label);
  themeToggle.setAttribute("title", label);
}

function cycleTheme() {
  theme = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length];
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (error) {
    /* the cycle still works for this session without persistence */
  }
  applyTheme();
  // The graph paints into an <svg> with resolved colours, so it has to repaint.
  if (current.boundary) drawGraph(current.boundary);
}

function cssVar(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/* ------------------------------------------------------------------------- */

let current = {
  searchId: null,
  at: "final",
  filters: {
    anchor: true, edge: true, leap: true, wildcard: true,
    requested: true, discovered: true, presented: true, unexplored: true, rejected: true, negative: true,
  },
  debug: false,
  boundary: null,
  result: null,
};

const ROLES = ["anchor", "edge", "leap", "wildcard"];

function roleName(role) {
  return t(`role${role.charAt(0).toUpperCase()}${role.slice(1)}`);
}

function roleGloss(role) {
  return t(`gloss${role.charAt(0).toUpperCase()}${role.slice(1)}`);
}

function parseRoute() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const [path, queryString] = hash.split("?");
  const query = new URLSearchParams(queryString || location.search.replace(/^\?/, ""));
  current.debug = query.get("debug") === "1" || new URLSearchParams(location.search).get("debug") === "1";
  debugFlag.classList.toggle("hidden", !current.debug);
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "s" && parts[1]) {
    const id = parts[1];
    // Snapshot time-travel lives in the route so it survives navigation between
    // the overview, results and frontier pages and stays linkable.
    current.routeAt = query.get("at") || "";
    current.at = current.routeAt || "final";
    if (parts[2] === "results") {
      const role = ROLES.includes(parts[3]) ? parts[3] : "";
      return { view: "results", id, role };
    }
    if (parts[2] === "frontier") return { view: "frontier", id };
    // owner/name contains a slash, so take everything after "repo"
    if (parts[2] === "repo" && parts.length > 3) {
      return { view: "repo", id, repo: parts.slice(3).join("/") };
    }
    return { view: "overview", id };
  }
  return { view: "list" };
}

async function api(path) {
  const glue = path.includes("?") ? "&" : "?";
  const url = current.debug ? `${path}${glue}debug=1` : path;
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.error || response.statusText);
  return data;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function pills(values, cls) {
  return (values || []).map((item) => `<span class="pill ${cls || ""}">${esc(item)}</span>`).join(" ")
    || `<span class="muted">${t("none")}</span>`;
}

function statusLabel(status) {
  return ({
    searched: t("statusSearched"), iterating: t("statusIterating"),
    ranked: t("statusRanked"), incomplete: t("statusIncomplete"),
  }[status] || status);
}

function roleChip(role) {
  if (!role) return "";
  return `<span class="pill ${esc(role)}" title="${esc(roleGloss(role))}">${esc(roleName(role))}</span>`;
}

function sessionNav(searchId, active) {
  const item = (view, label) => {
    const href = view === "overview" ? `#/s/${encodeURIComponent(searchId)}` : `#/s/${encodeURIComponent(searchId)}/${view}`;
    const isActive = active === view;
    return `<a class="navlink ${isActive ? "active" : ""}" href="${href}"${isActive ? ' aria-current="page"' : ""}>${esc(label)}</a>`;
  };
  return `<nav class="sessionnav" aria-label="${esc(t("navOverview"))}">
    ${item("overview", t("navOverview"))}
    ${item("results", t("navResults"))}
    ${item("frontier", t("navFrontier"))}
  </nav>`;
}

function copyButton(text, label, cls) {
  return `<button type="button" class="btn ${cls || "ghost"}" data-copy="${esc(text)}" data-label="${esc(label)}">${esc(label)}</button>`;
}

function wireCopyButtons(root) {
  root.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const label = button.getAttribute("data-label");
      try {
        await navigator.clipboard.writeText(button.getAttribute("data-copy"));
        button.textContent = t("copied");
      } catch (error) {
        button.textContent = label;
        return;
      }
      setTimeout(() => { button.textContent = label; }, 1200);
    });
  });
}

async function render() {
  const route = parseRoute();
  try {
    if (route.view === "list") await renderList();
    else if (route.view === "results") await renderResults(route.id, route.role);
    else if (route.view === "repo") await renderRepo(route.id, route.repo);
    else if (route.view === "frontier") await renderFrontier(route.id);
    else await renderOverview(route.id);
  } catch (error) {
    main.innerHTML = `<div class="panel"><h1>${esc(t("loadFailed"))}</h1><p>${esc(error.message)}</p></div>`;
  }
}

/* ---- session list -------------------------------------------------------- */

async function renderList() {
  current.searchId = null;
  const data = await api("/api/searches");
  if (!data.searches.length) {
    main.innerHTML = `<h1>${esc(t("listTitle"))}</h1><p class="sub">${esc(t("listEmpty"))}</p>`;
    return;
  }
  main.innerHTML = `
    <h1>${esc(t("listTitle"))}</h1>
    <p class="sub">${esc(t("listSub"))}</p>
    <div class="scroller">
    <table class="table">
      <thead><tr>
        <th>${esc(t("colRequest"))}</th><th>${esc(t("colMode"))}</th><th>${esc(t("colStatus"))}</th>
        <th>${esc(t("colIteration"))}</th><th>${esc(t("colMechanism"))}</th>
        <th>${esc(t("colResult"))}</th><th>${esc(t("colTime"))}</th>
      </tr></thead>
      <tbody>
        ${data.searches.map((item) => `
          <tr>
            <td><a href="#/s/${encodeURIComponent(item.search_id)}">${esc(item.request)}</a></td>
            <td>${esc(item.mode)}</td>
            <td>${esc(statusLabel(item.status))}</td>
            <td class="num">${item.iteration}</td>
            <td class="num">${item.mechanism_count}</td>
            <td class="num">${item.result_count}</td>
            <td class="num">${esc((item.updated_at || "").replace("T", " ").slice(0, 16))}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    </div>`;
}

/* ---- overview ------------------------------------------------------------ */

async function renderOverview(searchId) {
  if (current.searchId !== searchId) current.at = "final";
  current.searchId = searchId;
  if (current.routeAt) current.at = current.routeAt;
  const atParam = current.at && current.at !== "final" ? `?at=${encodeURIComponent(current.at)}` : "";
  const [summary, boundary, timeline, result] = await Promise.all([
    api(`/api/searches/${searchId}`),
    api(`/api/searches/${searchId}/boundary${atParam}`),
    api(`/api/searches/${searchId}/iterations`),
    api(`/api/searches/${searchId}/result${atParam}`),
  ]);
  current.boundary = boundary;
  current.result = result;
  const resultCount = (result.items || []).length;
  const unexploredCount = (boundary.overview.unexplored || []).length;
  const continueText = `Continue this Muse-shroom search with search_id=${searchId}. First observe, then iterate only if the user asked for more and can_iterate is true.`;
  main.innerHTML = `
    <p><a class="back" href="#/">${esc(t("backToList"))}</a></p>
    <div class="header-card">
      <h1>${esc(summary.request)}</h1>
      <div class="meta">
        <span class="pill">${esc(summary.mode)}</span>
        <span class="pill">${esc(statusLabel(summary.status))}</span>
        <span class="pill">iteration ${summary.iteration}</span>
        ${summary.can_iterate ? `<span class="pill presented">can_iterate</span>` : ""}
      </div>
      <div class="actions">
        ${copyButton(searchId, t("copyId"))}
        ${copyButton(continueText, t("copyContinue"), "")}
      </div>
    </div>
    ${sessionNav(searchId, "overview")}
    <div class="jump">
      <a class="jumpcard" href="#/s/${encodeURIComponent(searchId)}/results">
        <strong>${esc(t("navResults"))}</strong><span class="num">${resultCount}</span>
      </a>
      <a class="jumpcard" href="#/s/${encodeURIComponent(searchId)}/frontier">
        <strong>${esc(t("navFrontier"))}</strong><span class="num">${unexploredCount}</span>
      </a>
    </div>
    <div class="overview">
      <div class="box"><h3>${esc(t("overviewProblem"))}</h3>${pills((summary.problem_concepts || []).map((i) => i.term))}</div>
      <div class="box"><h3>${esc(t("overviewRequested"))}</h3>${pills((summary.mechanisms || []).map((i) => i.term), "requested")}</div>
      <div class="box"><h3>${esc(t("overviewExploration"))}</h3>${pills((summary.exploration_directions || []).map((i) => i.term))}</div>
      <div class="box"><h3>${esc(t("overviewRecalled"))}</h3>${pills(boundary.overview.recalled, "discovered")}</div>
      <div class="box"><h3>${esc(t("overviewPresented"))}</h3>${pills(boundary.overview.presented, "presented")}</div>
    </div>
    <h2>${esc(t("timelineTitle"))}</h2>
    <div class="timeline" id="timeline"></div>
    <h2>${esc(t("graphTitle"))}</h2>
    <div class="filters" id="filters"></div>
    <div class="scroller">
      <svg id="graph" viewBox="0 0 1100 420" role="img" aria-label="${esc(t("graphTitle"))}"></svg>
    </div>
  `;
  drawTimeline(timeline);
  drawFilters();
  drawGraph(boundary);
  wireCopyButtons(main);
}

/* ---- results ------------------------------------------------------------- */

function resultCard(item, searchId) {
  const href = `#/s/${encodeURIComponent(searchId)}/repo/${item.repo.split("/").map(encodeURIComponent).join("/")}`;
  const meta = [
    item.stars != null ? `${item.stars} ${t("stars")}` : "",
    item.language || "",
    item.artifact_type || "",
  ].filter(Boolean);
  const topics = (item.topics || []).slice(0, 4);
  // Composed from fields, never from why_different: that string is engine prose
  // that already concatenates the mechanism list with a reason sentence.
  const mechanism = (item.new_mechanisms || [])[0];
  return `<a class="card" href="${href}">
    <h3>${esc(item.repo)}</h3>
    <p class="card-desc">${esc(item.description || t("noDescription"))}</p>
    ${meta.length ? `<p class="card-meta">${meta.map((m) => esc(m)).join(" · ")}</p>` : ""}
    ${topics.length ? `<p class="card-topics">${topics.map((x) => `<span class="topic">${esc(x)}</span>`).join("")}</p>` : ""}
    <p class="card-foot">
      ${roleChip(item.boundary_role)}
      ${mechanism ? `<span class="pill discovered">${esc(mechanism)}</span>` : ""}
    </p>
  </a>`;
}

/* The role bar is the entrance to each role's own page. The gloss lives on that
   page rather than in a legend, so the explanation sits with the results it
   explains. */
function roleBar(searchId, items, activeRole) {
  const base = `#/s/${encodeURIComponent(searchId)}/results`;
  const counts = ROLES.reduce((acc, role) => {
    acc[role] = items.filter((item) => item.boundary_role === role).length;
    return acc;
  }, {});
  const link = (role, label, count, cls) => {
    const href = role ? `${base}/${role}` : base;
    const active = (activeRole || "") === (role || "");
    return `<a class="rolelink ${cls} ${active ? "active" : ""} ${count === 0 && role ? "empty" : ""}"
      href="${href}"${active ? ' aria-current="page"' : ""}${role ? ` title="${esc(roleGloss(role))}"` : ""}>
      <span>${esc(label)}</span><span class="count">${count}</span></a>`;
  };
  return `<nav class="rolebar" aria-label="${esc(t("resultsTitle"))}">
    ${link("", t("allRoles"), items.length, "all")}
    ${ROLES.map((role) => link(role, roleName(role), counts[role], role)).join("")}
  </nav>`;
}

async function renderResults(searchId, role) {
  current.searchId = searchId;
  const atParam = current.at && current.at !== "final" ? `?at=${encodeURIComponent(current.at)}` : "";
  const result = await api(`/api/searches/${searchId}/result${atParam}`);
  current.result = result;
  const header = `
    <p><a class="back" href="#/s/${encodeURIComponent(searchId)}">${esc(t("backToOverview"))}</a></p>
    ${sessionNav(searchId, "results")}
    <h1>${esc(role ? roleName(role) : t("resultsTitle"))}</h1>`;
  // A historical snapshot never carries the final ranking: read_model returns
  // ranked=false for any `at` other than final, and the section stays hidden so
  // an earlier round is never shown holding results it had not produced yet.
  if (current.at !== "final") {
    main.innerHTML = `${header}
      <p class="sub">${esc(t("historicalNotice"))}</p>
      <p><a class="back" href="#/s/${encodeURIComponent(searchId)}/results">${esc(t("backToFinal"))}</a></p>
      <section id="ranked-results" ${current.at === "final" ? "" : "hidden"}></section>`;
    return;
  }
  const all = result.items || [];
  if (!result.ranked || !all.length) {
    main.innerHTML = `${header}<p class="sub">${esc(t("resultsEmpty"))}</p>`;
    return;
  }
  const shown = role ? all.filter((item) => item.boundary_role === role) : all;
  const lede = role
    ? `<p class="role-lede">${esc(roleGloss(role))}</p>`
    : `<p class="sub">${esc(t("resultsSub"))}</p>`;
  main.innerHTML = `${header}
    ${lede}
    ${roleBar(searchId, all, role)}
    <section id="ranked-results" ${current.at === "final" ? "" : "hidden"}>
      ${shown.length
        ? `<div class="cards">${shown.map((item) => resultCard(item, searchId)).join("")}</div>`
        : `<p class="sub">${esc(t("roleEmpty"))}</p>`}
    </section>`;
}

/* ---- repo detail --------------------------------------------------------- */

function evidenceText(entry) {
  const facts = entry.facts || {};
  if (typeof facts.text === "string" && facts.text.trim()) return facts.text;
  if (Array.isArray(facts.mechanisms) && facts.mechanisms.length) {
    return facts.mechanisms.map((m) => m.text).filter(Boolean).join(" · ");
  }
  return "";
}

function isRepositoryAuthored(entry) {
  const facts = entry.facts || {};
  if (facts.untrusted_source) return true;
  return String(entry.kind || "").includes("readme");
}

function evidenceBlock(entry) {
  const text = evidenceText(entry);
  if (!text) return "";
  const marker = isRepositoryAuthored(entry)
    ? `<span class="source-note">${esc(t("fromReadme"))}</span>` : "";
  return `<blockquote class="quote"><p>${esc(text)}</p>${marker}</blockquote>`;
}

async function renderRepo(searchId, repo) {
  current.searchId = searchId;
  const detail = await api(`/api/searches/${searchId}/repos/${repo}`);
  const byId = new Map((detail.evidence || []).map((entry) => [entry.id, entry]));
  // latest_release is an object ({tag_name, published_at, html_url, ...}), not a string.
  const release = detail.latest_release && typeof detail.latest_release === "object"
    ? detail.latest_release.tag_name : detail.latest_release;
  const meta = [
    detail.stars != null ? `${detail.stars} ${t("stars")}` : "",
    detail.language || "",
    detail.artifact_type || "",
    release || "",
  ].filter(Boolean);
  const reasons = detail.reasons || [];
  const assessment = detail.assessment || {};
  main.innerHTML = `
    <p><a class="back" href="#/s/${encodeURIComponent(searchId)}/results">${esc(t("backToResults"))}</a></p>
    <article class="repo">
      <header class="repo-head">
        <h1>${esc(detail.repo)}</h1>
        <p class="lede">${esc(detail.description || t("noDescription"))}</p>
        <p class="meta">${roleChip(detail.boundary_role)}${meta.map((m) => `<span class="pill">${esc(m)}</span>`).join("")}</p>
        ${detail.url ? `<p><a class="external" href="${esc(detail.url)}" target="_blank" rel="noreferrer noopener">${esc(t("openOnGitHub"))}</a></p>` : ""}
      </header>

      <section>
        <h2>${esc(t("whyTitle"))}</h2>
        ${(detail.new_mechanisms || []).length
          ? `<p class="introduces"><span class="label">${esc(t("introduces"))}</span> ${pills(detail.new_mechanisms, "discovered")}</p>` : ""}
        ${reasons.length ? `<p class="reason">${esc(reasons[0].text || "")}</p>` : ""}
      </section>

      <section>
        <h2>${esc(t("evidenceTitle"))}</h2>
        ${reasons.length ? reasons.map((reason) => `
          <div class="claim">
            <p>${esc(reason.text || "")}</p>
            ${(reason.evidence_ids || []).map((id) => byId.has(id) ? evidenceBlock(byId.get(id)) : "").join("")}
          </div>`).join("") : `<p class="sub">${esc(t("noEvidence"))}</p>`}
      </section>

      ${assessment.use_case || assessment.category || assessment.difficulty ? `
      <section>
        <h2>${esc(t("useCaseTitle"))}</h2>
        ${assessment.use_case ? `<p>${esc(assessment.use_case)}</p>` : ""}
        <p class="meta">
          ${assessment.category ? `<span class="pill">${esc(t("categoryLabel"))}: ${esc(assessment.category)}</span>` : ""}
          ${assessment.difficulty ? `<span class="pill">${esc(t("difficultyLabel"))}: ${esc(assessment.difficulty)}</span>` : ""}
        </p>
      </section>` : ""}

      ${(detail.risks || []).length ? `
      <section>
        <h2>${esc(t("risksTitle"))}</h2>
        ${detail.risks.map((risk) => `<p>${esc(risk.text || risk)}</p>`).join("")}
      </section>` : ""}

      ${(detail.mechanisms || []).length ? `
      <section>
        <h2>${esc(t("mechanismsTitle"))}</h2>
        <p>${pills((detail.mechanisms || []).map((item) => item.name))}</p>
      </section>` : ""}

      ${current.debug && detail.scores ? `
      <section>
        <h2>${esc(t("scoresTitle"))}</h2>
        <pre class="evidence">${esc(JSON.stringify(detail.scores, null, 2))}</pre>
      </section>` : ""}

      <p class="sub">${esc(t("notCloned"))}</p>
    </article>`;
}

/* ---- frontier ------------------------------------------------------------ */

function directionRequest(term, searchId) {
  return lang === "zh"
    ? `继续这次 Muse-shroom 搜索（search_id=${searchId}），把探索方向设为「${term}」。先 observe，再决定是否 iterate。`
    : `Continue this Muse-shroom search (search_id=${searchId}) with exploration direction "${term}". Observe first, then iterate only if it still has budget.`;
}

async function renderFrontier(searchId) {
  current.searchId = searchId;
  const boundary = await api(`/api/searches/${searchId}/boundary`);
  current.boundary = boundary;
  const overview = boundary.overview || {};
  const unexplored = overview.unexplored || [];
  main.innerHTML = `
    <p><a class="back" href="#/s/${encodeURIComponent(searchId)}">${esc(t("backToOverview"))}</a></p>
    ${sessionNav(searchId, "frontier")}
    <h1>${esc(t("frontierTitle"))}</h1>
    <p class="sub">${esc(t("frontierSub"))}</p>

    <h2>${esc(t("frontierUnexplored"))}</h2>
    ${unexplored.length ? `<ul class="directions">
      ${unexplored.map((term) => `<li>
        <span class="term">${esc(term)}</span>
        ${copyButton(directionRequest(term, searchId), t("copyDirection"))}
      </li>`).join("")}
    </ul>
    <p class="sub small">${esc(t("frontierReadonly"))}</p>`
    : `<p class="sub">${esc(t("frontierEmpty"))}</p>`}

    <h2>${esc(t("frontierCovered"))}</h2>
    <p>${pills(overview.presented, "presented")}</p>

    ${(overview.rejected || []).length ? `
      <h2>${esc(t("frontierRejected"))}</h2><p>${pills(overview.rejected, "rejected")}</p>` : ""}
    ${(overview.negative || []).length ? `
      <h2>${esc(t("frontierNegative"))}</h2><p>${pills(overview.negative, "negative")}</p>` : ""}
  `;
  wireCopyButtons(main);
}

/* ---- overview sub-widgets (unchanged behaviour) -------------------------- */

function drawTimeline(timeline) {
  const root = document.getElementById("timeline");
  if (!root) return;
  const steps = timeline.steps || [];
  if (!steps.length) {
    root.innerHTML = `<p class="sub">${esc(t("timelineEmpty"))}</p>`;
    return;
  }
  const searchId = current.searchId;
  root.innerHTML = steps.map((step, index) => {
    const ending = step.kind === "stop" || step.kind === "refuse";
    const at = step.kind === "initial" ? "initial"
      : step.kind === "rank" ? "final"
      : step.kind === "iteration" ? `iteration-${step.iteration}` : "";
    const active = at && ((current.at === at) || (current.at === "final" && index === steps.length - 1 && !ending));
    const title = step.kind === "initial" ? "Initial"
      : step.kind === "rank" ? "Rank"
      : step.kind === "stop" ? "Hard stop"
      : step.kind === "refuse" ? "Refused continue"
      : `Iteration ${step.iteration}`;
    const gain = ending
      ? (step.stop_reasons || []).join(", ") || step.kind
      : ((step.new_mechanisms || []).slice(0, 3).join(", ") || (step.boundary_gain ? "gain" : "no gain"));
    const body = `<strong>${esc(title)}</strong>
      <small>${esc(gain)}</small>
      ${step.queries?.length ? `<small>queries: ${esc(step.queries.map((i) => i.term || i.query).join(" · "))}</small>` : ""}`;
    if (!at) return `<div class="step ${ending ? "stop" : ""}">${body}</div>`;
    const href = at === "final"
      ? `#/s/${encodeURIComponent(searchId)}`
      : `#/s/${encodeURIComponent(searchId)}?at=${encodeURIComponent(at)}`;
    return `<a class="step ${active ? "active" : ""}" href="${href}"${active ? ' aria-current="true"' : ""}>${body}</a>`;
  }).join("");
}

function drawFilters() {
  const root = document.getElementById("filters");
  if (!root) return;
  const groups = [
    ROLES,
    ["requested", "discovered", "presented", "unexplored", "rejected", "negative"],
  ];
  root.innerHTML = groups.flat().map((name) => `
    <label><input type="checkbox" data-filter="${name}" ${current.filters[name] ? "checked" : ""}> ${esc(name)}</label>
  `).join("");
  root.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      current.filters[input.getAttribute("data-filter")] = input.checked;
      drawGraph(current.boundary);
    });
  });
}

function nodeAllowed(node) {
  const states = node.states || [];
  const role = node.boundary_role;
  if (role && current.filters[role] === false) return false;
  if (states.length && states.every((state) => current.filters[state] === false)) return false;
  return node.default_visible !== false;
}

function drawGraph(boundary) {
  const svg = document.getElementById("graph");
  if (!svg || !boundary) return;
  const graph = boundary.graph || { nodes: [], edges: [] };
  const nodes = graph.nodes.filter(nodeAllowed);
  const visible = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target));
  const columns = { problem: [], direction: [], mechanism: [], repository: [] };
  nodes.forEach((node) => (columns[node.kind] || columns.mechanism).push(node));
  const colX = { problem: 90, direction: 90, mechanism: 420, repository: 860 };
  const placed = {};
  function place(kind, x) {
    const list = columns[kind] || [];
    list.forEach((node, index) => {
      const y = list.length === 1 ? 200 : 40 + (index * (340 / Math.max(1, list.length - 1)));
      placed[node.id] = { ...node, x, y };
    });
  }
  place("problem", colX.problem);
  const directionStart = 40 + (columns.problem.length ? 48 * columns.problem.length : 0);
  (columns.direction || []).forEach((node, index) => {
    placed[node.id] = { ...node, x: colX.direction, y: Math.min(380, directionStart + index * 36) };
  });
  place("mechanism", colX.mechanism);
  place("repository", colX.repository);
  // Dots use the --dot-* tokens rather than the role text tokens: a filled dot is
  // a non-text graphic (3:1) and can be far more vivid than text that needs 4.5:1.
  const stroke = cssVar("--graph-line", "#3a3428");
  const roleColor = {
    anchor: cssVar("--dot-anchor", "#7eb8c9"), edge: cssVar("--dot-edge", "#8fbf88"),
    leap: cssVar("--dot-leap", "#e0a45a"), wildcard: cssVar("--dot-wildcard", "#c089c4"),
  };
  const gold = cssVar("--dot-mechanism", "#c4a35a");
  const mutedColor = cssVar("--dot-other", "#9a917f");
  const lines = edges.map((edge) => {
    const a = placed[edge.source];
    const b = placed[edge.target];
    if (!a || !b) return "";
    const mid = (a.x + b.x) / 2;
    return `<path d="M ${a.x} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}" fill="none" stroke="${stroke}" stroke-width="1.2"/>`;
  }).join("");
  const dots = Object.values(placed).map((node) => {
    const color = node.kind === "repository"
      ? (roleColor[node.boundary_role] || gold)
      : node.kind === "mechanism" ? gold : mutedColor;
    const label = node.label.length > 28 ? `${node.label.slice(0, 26)}…` : node.label;
    return `<g class="node" data-kind="${esc(node.kind)}" data-id="${esc(node.id)}" data-label="${esc(node.label)}" transform="translate(${node.x},${node.y})">
      <circle r="${node.kind === "repository" ? 11 : 9}" fill="${color}"></circle>
      <text x="14" y="4">${esc(label)}</text>
    </g>`;
  }).join("");
  svg.innerHTML = `${lines}${dots}`;
  svg.querySelectorAll(".node").forEach((group) => {
    group.style.cursor = "pointer";
    group.addEventListener("click", () => {
      const kind = group.getAttribute("data-kind");
      if (kind === "repository") {
        location.hash = `#/s/${encodeURIComponent(current.searchId)}/repo/${group.getAttribute("data-label").split("/").map(encodeURIComponent).join("/")}`;
      }
      if (kind === "mechanism") openMechanism(group.getAttribute("data-id"));
    });
  });
}

/* ---- mechanism drawer ---------------------------------------------------- */

function openDrawer(html) {
  drawerBody.innerHTML = html;
  drawer.hidden = false;
  drawerMask.hidden = false;
  drawer.classList.remove("hidden");
  drawerMask.classList.remove("hidden");
  document.getElementById("drawer-close").focus();
}

function closeDrawer() {
  drawer.hidden = true;
  drawerMask.hidden = true;
  drawer.classList.add("hidden");
  drawerMask.classList.add("hidden");
}

function openMechanism(id) {
  const node = (current.boundary?.mechanisms || []).find((item) => item.id === id);
  if (!node) return;
  openDrawer(`
    <h2>${esc(node.name)}</h2>
    <p>origin: ${esc(node.origin)}</p>
    <div class="meta">${(node.states || []).map((s) => `<span class="pill ${esc(s)}">${esc(s)}</span>`).join("")}</div>
    ${(node.evidence || []).map((item) => `<p>${esc(item.repo)} · ${(item.sources || []).join(", ")}</p>`).join("") || `<p>${t("none")}</p>`}
  `);
}

/* ---- boot ---------------------------------------------------------------- */

document.getElementById("drawer-close").addEventListener("click", closeDrawer);
drawerMask.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !drawer.hidden) closeDrawer();
});
langToggle.addEventListener("click", () => setLang(lang === "zh" ? "en" : "zh"));
themeToggle.addEventListener("click", cycleTheme);
window.addEventListener("hashchange", () => { render(); });

applyLangChrome();
render();
