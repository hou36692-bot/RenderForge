/* RenderForge workbench.
 *
 * Every pane is a render of data the server derived from workspace/ -- there is no client-side model of the
 * pipeline, so what the tab shows is what `python -m renderforge status` would print. The only local state is
 * which case/tab/filter is selected.
 */
"use strict";

const STAGE_ORDER = ["survey", "export", "build", "identify", "emit-ue", "emit-unity", "shot", "metrics"];
const STAGE_LABEL = {
  survey: "截帧分析", export: "资源提取", build: "场景重建", "build-model": "模型空间重建",
  "ingest-unity": "Unity 摄入", "ingest-3dmigoto": "3DMigoto 摄入", identify: "资源匹配",
  shaders: "着色器分析", "emit-ue": "导入 Unreal", "emit-unity": "导入 Unity",
  shot: "截图", metrics: "结果验证",
};
const SOURCE_LABEL = { rdc: "RenderDoc", "3dmigoto": "3DMigoto", "unity-bundle": "Unity 包" };

const state = {
  cases: [],
  case: null,
  detail: null,
  assets: null,
  assetKind: "meshes",
  group: null,
  search: "",
  view: "overview",
  pollTimer: null,
};

/* ---------- utilities ---------- */

const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = "toast"; }, 3600);
}

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
  let body = null;
  try { body = await res.json(); } catch (_) { body = null; }
  if (!res.ok) throw new Error((body && body.error) || `HTTP ${res.status}`);
  return body;
}

function fmt(n) {
  if (n === null || n === undefined) return "—";
  return typeof n === "number" ? n.toLocaleString("en-US") : String(n);
}

function pct(x) {
  return x === null || x === undefined ? "—" : (x * 100).toFixed(1) + "%";
}

/* ---------- connection ---------- */

async function ping() {
  try {
    await api("/api/cases");
    $("#conn").className = "dot on";
    $("#conn-text").textContent = "已连接";
    return true;
  } catch (e) {
    $("#conn").className = "dot off";
    $("#conn-text").textContent = "已断开";
    return false;
  }
}

/* ---------- overview ---------- */

function renderCaseList() {
  const list = $("#case-list");
  list.replaceChildren();
  for (const c of state.cases) {
    const doneAll = !c.next;
    const stages = STAGE_ORDER.filter((s) => (c.stages || []).includes(s)).length;
    const card = h("div", {
      class: "case-card" + (state.case === c.case ? " active" : ""),
      onclick: () => selectCase(c.case),
    },
      h("div", { class: "row1" },
        h("span", { class: "name", text: c.case }),
        h("span", { class: "src", text: SOURCE_LABEL[c.source] || c.source || "?" })),
      h("div", { class: "meta" },
        `${c.game || "—"} · ${stages}/${STAGE_ORDER.length} 阶段` +
        (c.instances ? ` · ${fmt(c.instances)} 实例` : "")),
      h("div", { class: "next" + (doneAll ? " done" : ""), text: doneAll ? "全部阶段已完成" : `下一步：${STAGE_LABEL[c.next] || c.next}` }),
    );
    list.append(card);
  }
}

function renderCaseMain() {
  const pane = $("#case-main");
  const d = state.detail;
  pane.replaceChildren();
  if (!d) {
    pane.append(h("div", { class: "placeholder", text: "从左侧选择一个案例" }));
    return;
  }

  const stages = d.stages || {};
  const badges = STAGE_ORDER.map((s) => {
    const on = stages[s];
    return h("span", { class: "badge" + (on ? " ok" : ""), text: `${on ? "✓" : "·"} ${STAGE_LABEL[s] || s}` });
  });

  const det = d.detail || {};
  const build = det.build || {};
  const id = det.identify || {};
  const exp = det.export || {};

  const kv = h("dl", { class: "kv" },
    h("dt", { text: "来源" }), h("dd", { text: `${SOURCE_LABEL[d.source] || d.source}${d.game ? " · " + d.game : ""}` }),
    h("dt", { text: "输入" }), h("dd", { text: d.input || "—" }),
    h("dt", { text: "draw / 网格 / 实例" }),
    h("dd", { text: `${fmt(exp.draws !== undefined ? exp.draws : build.report && build.report.draws)} / ${fmt(build.meshes)} / ${fmt(build.instances)}` }),
    h("dt", { text: "VP 拟合" }), h("dd", { text: build.vp_match !== undefined ? pct(build.vp_match) : "—" }),
    h("dt", { text: "源资产身份" }),
    h("dd", { text: id.instances ? `${fmt(id.instancesMatched)} / ${fmt(id.instances)} 实例（${fmt(id.uniqueSourceObjects)} 个源对象）` : "未识别" }),
    h("dt", { text: "下一步" }),
    h("dd", { text: d.next && d.next.step ? `${STAGE_LABEL[d.next.step] || d.next.step} — ${d.next.why}` : "无（全部完成）" }),
  );

  const notes = (d.notes || []).length
    ? h("div", {}, h("h3", { text: "提示" }), h("ul", { class: "notes" }, (d.notes || []).map((n) => h("li", { text: n }))))
    : null;

  const report = build.report ? h("div", {},
    h("h3", { text: "构建报告" }),
    h("table", { class: "tbl" }, Object.entries(build.report).map(([k, v]) =>
      h("tr", {}, h("td", { text: k }), h("td", { text: fmt(v) })))),
  ) : null;

  // The reference frame is what makes a case recognisable at a glance: "which capture is this?" is the first
  // question anyone asks of a list of frame numbers. A case with no capture (a source-package case) has nothing
  // to show, so the slot explains that instead of leaving a hole.
  const preview = d.reference
    ? h("a", { class: "case-preview", href: d.reference, target: "_blank", title: "点击查看完整参考帧" },
        h("img", { src: d.reference, alt: `${d.case} 参考帧` }))
    : h("div", { class: "case-preview empty" },
        h("div", { class: "t", text: d.source === "unity-bundle" ? "无截帧（Unity 资源包来源）" : "无参考帧" }),
        h("div", { class: "s", text: d.source === "unity-bundle" ? "该案例由资源包直接构建" : "尚未导出参考帧" }));

  const parts = [
    h("div", { class: "case-head" },
      h("div", { class: "case-head-main" },
        h("h2", { text: d.case }),
        h("div", { class: "badges" }, badges)),
      preview),
    kv,
  ];
  if (notes) parts.push(notes);
  if (report) parts.push(report);
  pane.append(...parts);
}

/* ---------- flow ---------- */

function renderFlow() {
  const d = state.detail;
  const host = $("#flow-steps");
  host.replaceChildren();
  $("#flow-title").textContent = d ? `工作流 · ${d.case}` : "工作流";
  if (!d) {
    host.append(h("div", { class: "placeholder", text: "先选择一个案例" }));
    $("#flow-jobs-list").replaceChildren();
    return;
  }
  const running = new Set((d.jobs || []).filter((j) => j.state === "running").map((j) => j.name || ""));
  (d.plan || []).forEach((s, i) => {
    const rec = s.record || {};
    const isRunning = running.has(`${d.case}:${s.step}`);
    const cls = isRunning ? "running" : s.done ? "done" : (rec && rec.ok === false ? "failed" : "");
    const why = rec && rec.when ? `上次：${rec.when.replace("T", " ")}` : (s.done ? "产物已存在" : "尚未运行");
    let result = "";
    if (rec && rec.ok === false) result = rec.error || "失败";
    else if (rec) {
      const bits = Object.entries(rec)
        .filter(([k, v]) => !["ok", "when", "run", "note"].includes(k) && (typeof v === "number" || typeof v === "string"))
        .slice(0, 4).map(([k, v]) => `${k}=${v}`);
      result = bits.join("  ");
    }
    host.append(h("div", { class: `step ${cls}` },
      h("div", { class: "num", text: isRunning ? "▶" : String(i + 1) }),
      h("div", {}, h("div", { class: "name", text: STAGE_LABEL[s.step] || s.step }), h("div", { class: "why", text: why })),
      h("div", { class: "result", text: result, title: result }),
      h("div", { class: "actions" },
        h("button", {
          text: s.done ? "重跑" : "运行",
          title: s.writes ? s.writes.join(", ") : "",
          onclick: (ev) => { ev.stopPropagation(); startStep(s.step, s.done); },
        })),
    ));
  });
  renderJobs();
}

function renderJobs() {
  const host = $("#flow-jobs-list");
  host.replaceChildren();
  const js = (state.detail && state.detail.jobs) || [];
  if (!js.length) {
    host.append(h("div", { class: "why", text: "还没有该案例的作业记录" }));
    return;
  }
  for (const j of js) {
    host.append(h("div", { class: "job" },
      h("div", { text: j.name || j.id }),
      h("div", { class: `state ${j.state || ""}`, text: j.state || "?" }),
      h("div", { text: (j.argv || []).join(" ") }),
    ));
  }
}

async function startStep(step, force) {
  if (!state.case) return;
  try {
    const payload = { step, force: !!force };
    if (step === "run") {
      payload.to = null;
    }
    const r = await api(`/api/case/${encodeURIComponent(state.case)}/run`, { method: "POST", body: JSON.stringify(payload) });
    toast(`已启动：${step}（作业 ${r.job.id}）`);
    await refreshAll();
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------- assets ---------- */

function currentAssets() {
  const a = state.assets;
  if (!a) return [];
  let rows = a[state.assetKind] || [];
  if (state.search) {
    const q = state.search.toLowerCase();
    rows = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(q));
  }
  if (state.group) {
    if (state.assetKind === "meshes") rows = rows.filter((r) => (r.source || "(unidentified)") === state.group);
    else if (state.assetKind === "textures") rows = rows.filter((r) => (r.format || "") === state.group);
    else rows = rows.filter((r) => {
      const tags = [r.water && "水", r.translucent && "半透明", r.masked && "遮罩"].filter(Boolean).join(",");
      return (tags || "普通") === state.group;
    });
  }
  return rows;
}

function renderGroups() {
  const host = $("#asset-groups");
  host.replaceChildren();
  const a = state.assets;
  if (!a) return;
  let entries = [];
  if (state.assetKind === "meshes") {
    entries = (a.groups || []).map((g) => ({ key: g.name, label: g.name, n: `${g.meshes} 网格 · ${fmt(g.instances)} 实例` }));
  } else if (state.assetKind === "textures") {
    const byFmt = {};
    for (const t of a.textures || []) {
      const k = t.format || "?";
      byFmt[k] = (byFmt[k] || 0) + 1;
    }
    entries = Object.entries(byFmt).sort((x, y) => y[1] - x[1]).map(([k, n]) => ({ key: k, label: k, n: `${n} 张` }));
  } else {
    const byTag = {};
    for (const m of a.materials || []) {
      const tags = [m.water && "水", m.translucent && "半透明", m.masked && "遮罩"].filter(Boolean).join(",") || "普通";
      byTag[tags] = (byTag[tags] || 0) + 1;
    }
    entries = Object.entries(byTag).sort((x, y) => y[1] - x[1]).map(([k, n]) => ({ key: k, label: k, n: `${n} 个` }));
  }
  host.append(h("div", {
    class: "group" + (state.group === null ? " active" : ""), onclick: () => { state.group = null; renderAssets(); },
  }, h("div", { text: "全部" }), h("div", { class: "n", text: `${(a[state.assetKind] || []).length} 项` })));
  for (const e of entries.slice(0, 400)) {
    host.append(h("div", {
      class: "group" + (state.group === e.key ? " active" : ""),
      onclick: () => { state.group = e.key; renderAssets(); },
    }, h("div", { text: e.label, title: e.label }), h("div", { class: "n", text: e.n })));
  }
}

function renderAssets() {
  renderGroups();
  const grid = $("#asset-grid");
  grid.replaceChildren();
  const a = state.assets;
  if (!a) { grid.append(h("div", { class: "placeholder", text: "先选择一个案例" })); return; }
  if (a.ok === false) { grid.append(h("div", { class: "placeholder", text: a.error || "没有可浏览的资源" })); return; }
  const rows = currentAssets();
  if (!rows.length) { grid.append(h("div", { class: "placeholder", text: "没有匹配的资源" })); return; }

  for (const r of rows.slice(0, 600)) {
    let thumb, title, sub;
    if (state.assetKind === "meshes") {
      // data-src, not src: 535 tiles at once would queue 535 renders. The observer below loads a tile only when it
      // scrolls into view, which is also what makes the first paint fast.
      thumb = h("img", { "data-src": r.thumb, alt: "", class: "lazy" });
      title = r.source || r.id;
      sub = `${fmt(r.verts)} 顶点 · ${fmt(r.instances)} 实例${r.kinds.length ? " · " + r.kinds.join("/") : ""}`;
    } else if (state.assetKind === "textures") {
      thumb = r.url ? h("img", { src: r.url, loading: "lazy", alt: "" }) : h("span", { class: "s", text: "无文件" });
      title = `${r.w}x${r.h}`;
      sub = `${r.format} · ${r.id.split("::").pop()}`;
    } else {
      thumb = r.base_url ? h("img", { src: r.base_url, loading: "lazy", alt: "" }) : h("span", { class: "s", text: "无底色" });
      title = r.id;
      sub = [r.water && "水", r.translucent && "半透明", r.masked && "遮罩", r.normal && "法线"].filter(Boolean).join(" · ") || "普通";
    }
    const card = h("div", { class: "card" },
      h("div", { class: "thumb" }, thumb),
      h("div", { class: "cap" }, h("div", { class: "t", text: title, title }), h("div", { class: "s", text: sub })),
    );
    if (state.assetKind === "meshes") {
      // Hovering swaps the static thumbnail for the shared WebGL canvas (one context for the whole page); clicking
      // opens the full viewer. Both are optional: if the viewer cannot load, the card keeps its thumbnail.
      card.addEventListener("mouseenter", () => MeshViewer.hover(state.case, r.id, card));
      card.addEventListener("mouseleave", () => MeshViewer.unhover(card));
      card.addEventListener("click", () => MeshViewer.open(state.case, r.id, title));
    }
    grid.append(card);
  }
  observeLazy(grid);
}

let lazyObserver = null;
function observeLazy(root) {
  if (!("IntersectionObserver" in window)) {
    $$("img.lazy", root).forEach((img) => { img.src = img.dataset.src; });
    return;
  }
  if (lazyObserver) lazyObserver.disconnect();
  lazyObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const img = e.target;
      img.src = img.dataset.src;
      img.removeAttribute("data-src");
      lazyObserver.unobserve(img);
    }
  }, { root, rootMargin: "300px" });
  $$("img.lazy", root).forEach((img) => lazyObserver.observe(img));
}

/* ---------- result ---------- */

function renderResult() {
  const d = state.detail;
  const shots = $("#result-shots");
  const report = $("#result-report");
  shots.replaceChildren();
  report.replaceChildren();
  if (!d) { shots.append(h("div", { class: "placeholder", text: "先选择一个案例" })); return; }

  // URLs come from the server: building filesystem paths in the browser breaks on separators and escaping.
  if (d.reference) {
    shots.append(h("div", { class: "shot" }, h("h4", { text: "参考帧（游戏原帧）" }), h("img", { src: d.reference, alt: "reference" })));
  }
  const shotList = d.shots || [];
  for (const s of shotList) {
    shots.append(h("div", { class: "shot" }, h("h4", { text: s.file }), h("img", { src: s.url, alt: s.file })));
  }
  if (!shotList.length) {
    shots.append(h("div", { class: "placeholder", text: "还没有截图" }));
  }

  const det = d.detail || {};
  const build = det.build || {};
  report.append(h("h3", { text: "导入报告" }));
  const rows = [];
  if (det["emit-ue"]) rows.push(["Unreal 网格", fmt(det["emit-ue"].meshes)], ["Unreal 实例", fmt(det["emit-ue"].instances)],
    ["材质", fmt(det["emit-ue"].materials)], ["贴图", fmt(det["emit-ue"].textures)]);
  if (det["emit-unity"]) rows.push(["Unity 网格", fmt(det["emit-unity"].meshes)], ["Unity 实例", fmt(det["emit-unity"].instances)]);
  if (!rows.length) rows.push(["导入", "尚未导入引擎"]);
  report.append(h("table", { class: "tbl" }, rows.map(([k, v]) => h("tr", {}, h("td", { text: k }), h("td", { text: v })))));

  const m = d.metrics || det.metrics;
  if (m && m.ssim !== undefined) {
    report.append(h("h3", { text: "验证指标" }));
    report.append(h("table", { class: "tbl" }, [
      ["对比截图", m.compared], ["SSIM", m.ssim], ["MAE", m.mae], ["RMSE", m.rmse], ["p95 误差", m.p95_abs],
      ["覆盖率误差", m.coverage_error], ["mask IoU", m.mask_iou], ["已翻转", m.shot_flipped ? "是" : "否"],
    ].filter(([, v]) => v !== undefined && v !== null)
     .map(([k, v]) => h("tr", {}, h("td", { text: k }), h("td", { text: typeof v === "number" ? v.toFixed(4) : String(v) })))));
    report.append(h("div", { class: "diag info", text: m.scope || "" }));
  }
  if (build.report) {
    report.append(h("h3", { text: "构建统计" }));
    report.append(h("table", { class: "tbl" }, Object.entries(build.report).map(([k, v]) =>
      h("tr", {}, h("td", { text: k }), h("td", { text: fmt(v) })))));
  }
}

/* ---------- data flow ---------- */

async function loadCases() {
  const r = await api("/api/cases");
  state.cases = r.cases || [];
  renderCaseList();
}

async function selectCase(name) {
  state.case = name;
  state.group = null;
  state.assets = null;
  renderCaseList();
  await refreshAll();
}

async function refreshAll() {
  if (!state.case) return;
  try {
    const [detail, assets] = await Promise.all([
      api(`/api/case/${encodeURIComponent(state.case)}`),
      api(`/api/case/${encodeURIComponent(state.case)}/assets`).catch(() => null),
    ]);
    state.detail = detail;
    state.assets = assets;
    renderCaseMain();
    renderFlow();
    renderAssets();
    renderResult();
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------- wiring ---------- */

function setView(v) {
  state.view = v;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach((s) => s.classList.toggle("active", s.id === `view-${v}`));
  if (v === "assets") renderAssets();
  if (v === "flow") renderFlow();
  if (v === "result") renderResult();
}

function boot() {
  MeshViewer.wireDialog();
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("#refresh").addEventListener("click", async () => {
    await loadCases();
    await refreshAll();
    toast("已刷新");
  });
  $("#run-all").addEventListener("click", () => startStep("run", false));
  $("#asset-search").addEventListener("input", (e) => { state.search = e.target.value.trim(); renderAssets(); });
  $$("#asset-kind button").forEach((b) => b.addEventListener("click", () => {
    state.assetKind = b.dataset.kind;
    state.group = null;
    $$("#asset-kind button").forEach((x) => x.classList.toggle("active", x === b));
    renderAssets();
  }));
  $("#btn-metrics").addEventListener("click", async () => {
    if (!state.case) return;
    $("#btn-metrics").disabled = true;
    try {
      await api(`/api/case/${encodeURIComponent(state.case)}/metrics`, { method: "POST", body: "{}" });
      await refreshAll();
      toast("指标已更新");
    } catch (e) { toast(e.message, true); }
    finally { $("#btn-metrics").disabled = false; }
  });

  ping().then(loadCases);
  // live state: the job list and the case table both change while a job runs
  state.pollTimer = setInterval(async () => {
    if (document.hidden) return;
    const ok = await ping();
    if (!ok) return;
    try {
      await loadCases();
      if (state.case) {
        const detail = await api(`/api/case/${encodeURIComponent(state.case)}`);
        state.detail = detail;
        renderCaseMain();
        if (state.view === "flow") renderFlow();
        if (state.view === "result") renderResult();
      }
    } catch (_) { /* transient: the next tick will retry */ }
  }, 5000);
}

document.addEventListener("DOMContentLoaded", boot);
