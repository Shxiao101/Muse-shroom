const DIMENSIONS = ["relevance", "interesting", "evidence", "actionability", "diversity"];
const FIELDS = ["description", "rationale", "source_term", "quote"];

const main = document.getElementById("main");
const nav = document.getElementById("nav");
const progress = document.getElementById("progress");
const prevBtn = document.getElementById("prev");
const nextBtn = document.getElementById("next");

const state = {
  cases: [],
  ratings: new Map(),
  index: 0,
  status: "",
  error: "",
};

function keyOf(needId, repetition) {
  return `${needId}\t${repetition}`;
}

function caseKey(item) {
  return keyOf(item.need_id, item.repetition);
}

function parseJson(response, label) {
  if (!response.ok) {
    return response.json().then((body) => {
      throw new Error(body.message || `${label} failed (${response.status})`);
    });
  }
  return response.json();
}

function ratingFor(item) {
  return state.ratings.get(caseKey(item)) || null;
}

function emptyScores() {
  const scores = {};
  for (const name of DIMENSIONS) scores[name] = "";
  return scores;
}

function fieldText(value) {
  if (value === null || value === undefined || value === "") return "";
  return String(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function repoHref(entry) {
  const url = fieldText(entry.url);
  if (url.startsWith("https://github.com/") || url.startsWith("http://github.com/")) return url;
  const repo = fieldText(entry.repo);
  if (repo && repo.includes("/")) return `https://github.com/${repo}`;
  return "";
}

function renderEntry(entry) {
  const href = repoHref(entry);
  const repo = fieldText(entry.repo) || "(unnamed repo)";
  const title = href
    ? `<a class="repo" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(repo)}</a>`
    : `<span class="repo">${escapeHtml(repo)}</span>`;
  const fields = FIELDS.map((name) => {
    const text = fieldText(entry[name]);
    if (!text) return "";
    const cls = name === "quote" ? "quote" : "";
    return `<div class="field"><dt>${escapeHtml(name)}</dt><dd class="${cls}">${escapeHtml(text)}</dd></div>`;
  }).join("");
  return `<article class="card">${title}${fields || '<p class="empty">没有可显示的字段</p>'}</article>`;
}

function renderColumn(label, entries) {
  const items = Array.isArray(entries) ? entries : [];
  const body = items.length
    ? items.map(renderEntry).join("")
    : '<p class="empty">这份清单是空的。</p>';
  return `<section class="column" aria-label="list ${label}">
    <h2>${escapeHtml(label)} <span class="count">${items.length} 项</span></h2>
    ${body}
  </section>`;
}

function scoreSelect(list, dimension, value) {
  const options = ["", "1", "2", "3", "4", "5"].map((choice) => {
    const selected = String(value) === choice ? " selected" : "";
    const label = choice || "—";
    return `<option value="${choice}"${selected}>${label}</option>`;
  }).join("");
  return `<select name="${list}-${dimension}" aria-label="${list} ${dimension}">${options}</select>`;
}

function renderScores(current) {
  const a = current?.A || emptyScores();
  const b = current?.B || emptyScores();
  const preferred = current?.preferred || "";
  const head = DIMENSIONS.map((name) => `<th>${escapeHtml(name)}</th>`).join("");
  const row = (list, scores) => DIMENSIONS.map((name) => `<td>${scoreSelect(list, name, scores[name])}</td>`).join("");
  const pref = ["A", "B", "tie"].map((choice) => {
    const checked = preferred === choice ? " checked" : "";
    return `<label><input type="radio" name="preferred" value="${choice}"${checked}> ${choice}</label>`;
  }).join("");
  return `<section class="scores">
    <h2>打分</h2>
    <table class="score-table">
      <thead><tr><th></th>${head}</tr></thead>
      <tbody>
        <tr><th>A</th>${row("A", a)}</tr>
        <tr><th>B</th>${row("B", b)}</tr>
      </tbody>
    </table>
    <div class="preferred" role="radiogroup" aria-label="preferred">${pref}</div>
    <div class="actions">
      <button type="button" class="btn" id="save">保存</button>
      <button type="button" class="btn ghost" id="save-next">保存并下一对</button>
    </div>
    <p class="status ${state.error ? "error" : ""}" id="status">${escapeHtml(state.error || state.status)}</p>
  </section>`;
}

function renderNav() {
  nav.innerHTML = state.cases.map((item, index) => {
    const rated = state.ratings.has(caseKey(item)) ? " rated" : "";
    const active = index === state.index ? " active" : "";
    const repetition = item.repetition ?? 1;
    return `<button type="button" class="${rated}${active}" data-index="${index}">
      ${escapeHtml(item.need_id)} <span class="rep">r${escapeHtml(repetition)}</span>
    </button>`;
  }).join("");
}

function render() {
  const item = state.cases[state.index];
  const rated = state.ratings.size;
  progress.textContent = item
    ? `第 ${state.index + 1} / ${state.cases.length} 对 · 已打 ${rated}`
    : "没有 case";
  prevBtn.disabled = state.index <= 0;
  nextBtn.disabled = state.index >= state.cases.length - 1;
  renderNav();
  if (!item) {
    main.innerHTML = '<p class="empty">这个包里没有 case。</p>';
    return;
  }
  const lists = item.lists || {};
  main.innerHTML = `
    <h1>${escapeHtml(item.need_id)} · repetition ${escapeHtml(item.repetition ?? 1)}</h1>
    <section class="request"><h2>request</h2><p>${escapeHtml(fieldText(item.request))}</p></section>
    <div class="columns">
      ${renderColumn("A", lists.A)}
      ${renderColumn("B", lists.B)}
    </div>
    ${renderScores(ratingFor(item))}
  `;
  document.getElementById("save").addEventListener("click", () => save(false));
  document.getElementById("save-next").addEventListener("click", () => save(true));
}

function readForm() {
  const item = state.cases[state.index];
  const block = (list) => {
    const scores = {};
    for (const name of DIMENSIONS) {
      const raw = document.querySelector(`[name="${list}-${name}"]`).value;
      if (!raw) throw new Error(`${list} 的 ${name} 还没打`);
      scores[name] = Number(raw);
    }
    return scores;
  };
  const preferred = document.querySelector('input[name="preferred"]:checked');
  if (!preferred) throw new Error("还没有选择 preferred（A / B / tie）");
  return {
    need_id: item.need_id,
    repetition: item.repetition ?? 1,
    preferred: preferred.value,
    A: block("A"),
    B: block("B"),
  };
}

async function save(advance) {
  state.error = "";
  state.status = "";
  try {
    const row = readForm();
    state.ratings.set(caseKey(row), row);
    const payload = { evaluations: [...state.ratings.values()] };
    const response = await fetch("/api/ratings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await parseJson(response, "save");
    state.status = "已保存";
    if (advance && state.index < state.cases.length - 1) state.index += 1;
    render();
  } catch (error) {
    state.error = error.message;
    const status = document.getElementById("status");
    if (status) {
      status.textContent = state.error;
      status.classList.add("error");
    } else {
      render();
    }
  }
}

function go(delta) {
  const next = state.index + delta;
  if (next < 0 || next >= state.cases.length) return;
  state.error = "";
  state.status = "";
  state.index = next;
  render();
}

prevBtn.addEventListener("click", () => go(-1));
nextBtn.addEventListener("click", () => go(1));
nav.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-index]");
  if (!button) return;
  state.error = "";
  state.status = "";
  state.index = Number(button.dataset.index);
  render();
});

async function boot() {
  try {
    const pack = await parseJson(await fetch("/api/pack"), "pack");
    const ratings = await parseJson(await fetch("/api/ratings"), "ratings");
    state.cases = pack.cases || [];
    for (const item of ratings.evaluations || []) {
      state.ratings.set(caseKey(item), item);
    }
    const firstUnrated = state.cases.findIndex((item) => !state.ratings.has(caseKey(item)));
    state.index = firstUnrated >= 0 ? firstUnrated : 0;
    render();
  } catch (error) {
    progress.textContent = "加载失败";
    main.innerHTML = `<p class="status error">${escapeHtml(error.message)}</p>`;
  }
}

boot();
