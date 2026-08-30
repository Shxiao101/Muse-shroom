const main = document.getElementById("main");
const drawer = document.getElementById("drawer");
const drawerBody = document.getElementById("drawer-body");
const drawerMask = document.getElementById("drawer-mask");
const debugFlag = document.getElementById("debug-flag");

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

function parseRoute() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const [path, queryString] = hash.split("?");
  const query = new URLSearchParams(queryString || location.search.replace(/^\?/, ""));
  current.debug = query.get("debug") === "1" || new URLSearchParams(location.search).get("debug") === "1";
  debugFlag.classList.toggle("hidden", !current.debug);
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "s" && parts[1]) return { view: "detail", id: parts[1] };
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
  return (values || []).map((item) => `<span class="pill ${cls || ""}">${esc(item)}</span>`).join(" ") || "<span class='muted'>—</span>";
}

function statusLabel(status) {
  return ({ searched: "已搜索", iterating: "探索中", ranked: "已排名", incomplete: "不完整" }[status] || status);
}

async function render() {
  const route = parseRoute();
  try {
    if (route.view === "list") await renderList();
    else await renderDetail(route.id);
  } catch (error) {
    main.innerHTML = `<div class="panel"><h1>无法加载 Explorer</h1><p>${esc(error.message)}</p></div>`;
  }
}

async function renderList() {
  current.searchId = null;
  const data = await api("/api/searches");
  if (!data.searches.length) {
    main.innerHTML = `<h1>本地搜索</h1><p class="sub">还没有 search session。用 CLI / MCP / Skill 跑完一次 search 后再打开 Explorer。</p>`;
    return;
  }
  main.innerHTML = `
    <h1>本地搜索</h1>
    <p class="sub">选择一个 session 查看 Boundary 如何扩张。Explorer 只读，不会调用 GitHub。</p>
    <table class="table">
      <thead><tr><th>请求</th><th>模式</th><th>状态</th><th>轮次</th><th>机制</th><th>结果</th><th>时间</th></tr></thead>
      <tbody>
        ${data.searches.map((item) => `
          <tr>
            <td><a href="#/s/${esc(item.search_id)}">${esc(item.request)}</a></td>
            <td>${esc(item.mode)}</td>
            <td>${esc(statusLabel(item.status))}</td>
            <td>${item.iteration}</td>
            <td>${item.mechanism_count}</td>
            <td>${item.result_count}</td>
            <td>${esc((item.updated_at || "").replace("T", " ").slice(0, 16))}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

async function renderDetail(searchId) {
  if (current.searchId !== searchId) current.at = "final";
  current.searchId = searchId;
  const atParam = current.at && current.at !== "final" ? `&at=${encodeURIComponent(current.at)}` : "";
  const [summary, boundary, timeline, result] = await Promise.all([
    api(`/api/searches/${searchId}`),
    api(`/api/searches/${searchId}/boundary?${atParam.replace(/^&/, "")}`),
    api(`/api/searches/${searchId}/iterations`),
    api(`/api/searches/${searchId}/result?${atParam.replace(/^&/, "")}`),
  ]);
  current.boundary = boundary;
  current.result = result;
  const continueText = `Continue this Muse-shroom search with search_id=${searchId}. First observe, then iterate only if the user asked for more and can_iterate is true.`;
  main.innerHTML = `
    <p><a href="#/">← 搜索列表</a></p>
    <div class="header-card">
      <h1>${esc(summary.request)}</h1>
      <div class="meta">
        <span class="pill">${esc(summary.mode)}</span>
        <span class="pill">${esc(statusLabel(summary.status))}</span>
        <span class="pill">next: ${esc(summary.next_action)}</span>
        ${summary.can_iterate ? `<span class="pill presented">can_iterate</span>` : ""}
        <span class="pill">iteration ${summary.iteration}</span>
      </div>
      <div class="actions">
        <button class="btn ghost" data-copy="${esc(searchId)}">复制 search_id</button>
        <button class="btn" data-copy="${esc(continueText)}">Continue in Agent</button>
      </div>
    </div>
    <h2>Boundary Overview</h2>
    <div class="overview">
      <div class="box"><h3>Problem</h3>${pills((summary.problem_concepts || []).map((item) => item.term))}</div>
      <div class="box"><h3>Requested mechanisms</h3>${pills((summary.mechanisms || []).map((item) => item.term), "requested")}</div>
      <div class="box"><h3>Exploration</h3>${pills((summary.exploration_directions || []).map((item) => item.term))}</div>
      <div class="box"><h3>Recalled</h3>${pills(boundary.overview.recalled, "discovered")}</div>
      <div class="box"><h3>Presented</h3>${pills(boundary.overview.presented, "presented")}</div>
      <div class="box"><h3>Unexplored</h3>${pills(boundary.overview.unexplored, "unexplored")}</div>
      <div class="box"><h3>Rejected</h3>${pills(boundary.overview.rejected, "rejected")}</div>
      <div class="box"><h3>Negative</h3>${pills(boundary.overview.negative, "negative")}</div>
    </div>
    ${boundary.overview.discovered_terms?.length ? `<p class="sub">Discovered terms（未证实，不作为机制节点）：${esc(boundary.overview.discovered_terms.join(" · "))}</p>` : ""}
    <h2>Iteration Timeline</h2>
    <div class="timeline" id="timeline"></div>
    <h2>Exploration Graph</h2>
    <div class="filters" id="filters"></div>
    <svg id="graph" viewBox="0 0 1100 420" role="img" aria-label="problem to mechanism to repository graph"></svg>
    <section id="ranked-results" ${current.at === "final" ? "" : "hidden"}>
      <h2>Ranked Results</h2>
      <div class="results" id="results"></div>
    </section>
  `;
  drawTimeline(timeline);
  drawFilters();
  drawGraph(boundary);
  drawResults(result);
  main.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.getAttribute("data-copy"));
      button.textContent = "已复制";
      setTimeout(() => {
        button.textContent = button.getAttribute("data-copy") === searchId ? "复制 search_id" : "Continue in Agent";
      }, 1200);
    });
  });
}

function drawTimeline(timeline) {
  const root = document.getElementById("timeline");
  const steps = timeline.steps || [];
  if (!steps.length) {
    root.innerHTML = `<p class="sub">还没有 boundary snapshot。</p>`;
    return;
  }
  root.innerHTML = steps.map((step, index) => {
    const ending = step.kind === "stop" || step.kind === "refuse";
    const at = step.kind === "initial" ? "initial" : step.kind === "rank" ? "final" : step.kind === "iteration" ? `iteration-${step.iteration}` : "";
    const active = at && ((current.at === at) || (current.at === "final" && index === steps.length - 1 && !ending));
    const title = step.kind === "initial" ? "Initial"
      : step.kind === "rank" ? "Rank"
      : step.kind === "stop" ? "Hard stop"
      : step.kind === "refuse" ? "Refused continue"
      : `Iteration ${step.iteration}`;
    const gain = ending
      ? (step.stop_reasons || []).join(", ") || step.kind
      : ((step.new_mechanisms || []).slice(0, 3).join(", ") || (step.boundary_gain ? "gain" : "no gain"));
    return `<button type="button" class="step ${ending ? "stop" : ""} ${active ? "active" : ""}" ${at ? `data-at="${at}"` : ""}>
      <strong>${esc(title)}</strong>
      <small>${esc(gain)}</small>
      ${!ending && step.stop_reasons?.length ? `<small>hard: ${esc(step.stop_reasons.join(", "))}</small>` : ""}
      ${step.stop_signals?.length ? `<small>signal: ${esc(step.stop_signals.join(", "))}</small>` : ""}
    </button>`;
  }).join("");
  root.querySelectorAll("[data-at]").forEach((button) => {
    button.addEventListener("click", async () => {
      current.at = button.getAttribute("data-at");
      await renderDetail(current.searchId);
    });
  });
}

function drawFilters() {
  const root = document.getElementById("filters");
  const groups = [
    ["anchor", "edge", "leap", "wildcard"],
    ["requested", "discovered", "presented", "unexplored", "rejected", "negative"],
  ];
  root.innerHTML = groups.flat().map((name) => `
    <label><input type="checkbox" data-filter="${name}" ${current.filters[name] ? "checked" : ""}> ${name}</label>
  `).join("");
  root.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      current.filters[input.getAttribute("data-filter")] = input.checked;
      drawGraph(current.boundary);
      drawResults(current.result);
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
  const lines = edges.map((edge) => {
    const a = placed[edge.source];
    const b = placed[edge.target];
    if (!a || !b) return "";
    const mid = (a.x + b.x) / 2;
    return `<path d="M ${a.x} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}" fill="none" stroke="#3a3428" stroke-width="1.2"/>`;
  }).join("");
  const dots = Object.values(placed).map((node) => {
    const color = node.kind === "repository"
      ? ( { anchor: "#7eb8c9", edge: "#8fbf88", leap: "#e0a45a", wildcard: "#c089c4" }[node.boundary_role] || "#c4a35a")
      : node.kind === "mechanism" ? "#c4a35a" : "#9a917f";
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
      const id = group.getAttribute("data-id");
      const label = group.getAttribute("data-label");
      if (kind === "repository") openRepo(label);
      if (kind === "mechanism") openMechanism(id);
    });
  });
}

function drawResults(result) {
  const root = document.getElementById("results");
  if (!result.ranked) {
    root.innerHTML = `<p class="sub">尚未 rank。display_order 为空；Agent 完成评估后再看最终推荐。</p>`;
    return;
  }
  const items = (result.items || []).filter((item) => {
    if (item.boundary_role && current.filters[item.boundary_role] === false) return false;
    return true;
  });
  root.innerHTML = items.map((item, index) => `
    <article class="card" data-repo="${esc(item.repo)}">
      <div>
        <span class="pill ${esc(item.boundary_role || "")}">${esc(item.boundary_role || "role")}</span>
        <div class="pill">${esc(item.bucket || "")}</div>
        <div class="pill">#${index + 1}</div>
      </div>
      <div>
        <h3>${esc(item.repo)}</h3>
        <p>${esc(item.description || "")}</p>
        <p class="why">${esc(item.why_different || "")}</p>
        ${item.new_mechanisms?.length ? `<div>${pills(item.new_mechanisms, "discovered")}</div>` : ""}
      </div>
    </article>
  `).join("");
  root.querySelectorAll("[data-repo]").forEach((card) => {
    card.addEventListener("click", () => openRepo(card.getAttribute("data-repo")));
  });
}

function openDrawer(html) {
  drawerBody.innerHTML = html;
  drawer.hidden = false;
  drawerMask.hidden = false;
  drawer.classList.remove("hidden");
  drawerMask.classList.remove("hidden");
}

function closeDrawer() {
  drawer.hidden = true;
  drawerMask.hidden = true;
  drawer.classList.add("hidden");
  drawerMask.classList.add("hidden");
}

async function openRepo(repo) {
  const detail = await api(`/api/searches/${current.searchId}/repos/${repo}`);
  openDrawer(`
    <h2>${esc(detail.repo)}</h2>
    <p><a href="${esc(detail.url)}" target="_blank" rel="noreferrer">${esc(detail.url)}</a></p>
    <p>${esc(detail.description || "")}</p>
    <div class="meta">
      <span class="pill">${detail.stars ?? "?"} stars</span>
      <span class="pill ${esc(detail.boundary_role || "")}">${esc(detail.boundary_role || "")}</span>
      <span class="pill">${esc(detail.artifact_type || "")}</span>
    </div>
    <h3>Why different</h3>
    <p>${esc(detail.why_different || "—")}</p>
    <h3>New mechanisms</h3>
    <p>${pills(detail.new_mechanisms, "discovered")}</p>
    <h3>Mechanisms</h3>
    <p>${pills((detail.mechanisms || []).map((item) => item.name))}</p>
    <h3>Assessment</h3>
    <p>relevance ${esc(detail.assessment?.relevance ?? "—")} · transferability ${esc(detail.assessment?.transferability ?? "—")}</p>
    <h3>Reasons</h3>
    ${(detail.reasons || []).map((item) => `<p>${esc(item.text)}</p>`).join("") || "<p>—</p>"}
    <h3>Risks</h3>
    ${(detail.risks || []).map((item) => `<p>${esc(item.text)}</p>`).join("") || "<p>—</p>"}
    <h3>Evidence</h3>
    <div class="evidence">${esc(JSON.stringify(detail.evidence || [], null, 2))}</div>
    <p class="sub">不会 clone 或安装该仓库。</p>
  `);
}

function openMechanism(id) {
  const node = (current.boundary.mechanisms || []).find((item) => item.id === id);
  if (!node) return;
  openDrawer(`
    <h2>${esc(node.name)}</h2>
    <p>origin: ${esc(node.origin)}</p>
    <p>first iteration: ${esc(node.first_iteration)}</p>
    <div class="meta">${(node.states || []).map((state) => `<span class="pill ${esc(state)}">${esc(state)}</span>`).join("")}</div>
    <h3>匹配仓库</h3>
    ${(node.evidence || []).map((item) => `<p>${esc(item.repo)} · ${(item.sources || []).join(", ")} · ${(item.matched_terms || []).join(", ")}</p>`).join("") || "<p>—</p>"}
    ${node.confirmed ? "" : "<p class='sub'>未证实为 mechanism（无 description / topic / README evidence）。</p>"}
  `);
}

document.getElementById("drawer-close").addEventListener("click", closeDrawer);
drawerMask.addEventListener("click", closeDrawer);
window.addEventListener("hashchange", render);
render();
