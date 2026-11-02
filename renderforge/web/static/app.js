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
  // filters that stack on top of the group: locating on the frame and the naming facets
  filters: { inFrame: false, minArea: 0, kind: null, code: null, level: null, named: "all", unknownGroup: null },
  groupBy: "source",    // source | code | level | kind | unknown | area
  focus: null,          // mesh id selected by picking the reference frame
  focusHits: [],        // ranked hits from the last pick (so the panel shows alternatives, not one answer)
  caseQuery: "",        // case list filter (display name, id or game)
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

function caseTitle(c) {
  // c.title is the display name (case.json -> title); c.case is the directory id and never changes
  return (c && (c.title || c.case)) || "";
}

function renderCaseList() {
  const list = $("#case-list");
  list.replaceChildren();
  const q = (state.caseQuery || "").toLowerCase();
  const cases = q ? state.cases.filter((c) => (c.case + " " + caseTitle(c) + " " + (c.game || "")).toLowerCase().includes(q)) : state.cases;
  if (!cases.length) {
    list.append(h("div", { class: "placeholder small", text: q ? "没有匹配的任务" : "还没有任务" }));
    return;
  }
  for (const c of cases) {
    const doneAll = !c.next;
    const stages = STAGE_ORDER.filter((s) => (c.stages || []).includes(s)).length;
    const titled = c.title && c.title !== c.case;
    const card = h("div", {
      class: "case-card" + (state.case === c.case ? " active" : ""),
      onclick: () => selectCase(c.case),
    },
      h("div", { class: "row1" },
        h("span", { class: "name", text: caseTitle(c), title: c.case }),
        h("span", { class: "src", text: SOURCE_LABEL[c.source] || c.source || "?" })),
      // when a display name is set, keep the directory id visible: it is what the CLI and engine paths use
      titled ? h("div", { class: "case-id", text: c.case }) : null,
      h("div", { class: "meta" },
        `${c.game || "—"} · ${stages}/${STAGE_ORDER.length} 阶段` +
        (c.instances ? ` · ${fmt(c.instances)} 实例` : "")),
      h("div", { class: "next" + (doneAll ? " done" : ""), text: doneAll ? "全部阶段已完成" : `下一步：${STAGE_LABEL[c.next] || c.next}` }),
      h("button", {
        class: "case-rename ghost", text: "重命名", title: "只改显示名，不改目录名",
        onclick: (ev) => { ev.stopPropagation(); openRename(c.case); },
      }),
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
        h("h2", { text: caseTitle(d) }),
        d.title && d.title !== d.case ? h("div", { class: "case-id", text: `目录：${d.case}` }) : null,
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
    // Ingest steps normally read the path from case.json. Legacy directories do not record one, so pass what the
    // case detail resolved; the server already accepts these fields (frame_dir / bundle).
    const src = (state.detail && state.detail.input) || "";
    if (step === "ingest-3dmigoto" && src) payload.frame_dir = src;
    if (step === "ingest-unity" && src) payload.bundle = src;
    const r = await api(`/api/case/${encodeURIComponent(state.case)}/run`, { method: "POST", body: JSON.stringify(payload) });
    toast(`已启动：${step}（作业 ${r.job.id}）`);
    await refreshAll();
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------- assets ---------- */

function filtersActive() {
  const f = state.filters;
  return f.inFrame || f.minArea > 0 || f.kind || f.code || f.level || f.named !== "all" || f.unknownGroup;
}

function currentAssets() {
  const a = state.assets;
  if (!a) return [];
  if (state.focusSet && state.assetKind === "meshes") {
    return (a.meshes || []).filter((r) => state.focusSet.has(r.id));
  }
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
  // Facet filters stack (AND). They are meaningful on meshes only: a texture has no screen position.
  if (state.assetKind === "meshes" && filtersActive()) {
    const f = state.filters;
    rows = rows.filter((r) => {
      if (f.inFrame && !r.in_frame) return false;
      if (f.minArea && (r.screen || 0) < f.minArea) return false;
      if (f.kind && r.kind !== f.kind) return false;
      if (f.code && r.code !== f.code) return false;
      if (f.level && r.level !== f.level) return false;
      if (f.named === "yes" && !r.named) return false;
      if (f.named === "no" && r.named) return false;
      if (f.unknownGroup && r.unknown_group !== f.unknownGroup) return false;
      return true;
    });
  }
  return rows;
}

const GROUP_BY_LABEL = { source: "源资产名", code: "资产类别", level: "关卡目录", kind: "渲染方式",
                         unknown: "未知资产分组", area: "屏幕占比", format: "贴图格式", tag: "材质标签" };

function renderGroups() {
  const host = $("#asset-groups");
  host.replaceChildren();
  const a = state.assets;
  if (!a) return;
  const facets = a.facets || {};
  const rows = state.assetKind === "meshes" ? (a.meshes || []) : [];
  let entries = [];
  const by = state.groupBy;
  if (state.assetKind === "meshes" && by === "code") {
    entries = (facets.codes || []).map((c) => ({
      key: c.code, label: c.label ? `${c.code} · ${c.label}` : c.code,
      n: `${c.n} 网格`, title: c.evidence || `${c.n} 个网格使用 ${c.code} 代号` }));
    const uncoded = rows.filter((r) => !r.code).length;
    if (uncoded) entries.push({ key: "\u0000none", label: "无类别码", n: `${uncoded} 网格` });
  } else if (state.assetKind === "meshes" && by === "level") {
    entries = (facets.levels || []).map((l) => ({ key: l.level, label: l.level, n: `${l.n} 网格` }));
    const none = rows.filter((r) => !r.level).length;
    if (none) entries.push({ key: "\u0000none", label: "无关卡目录", n: `${none} 网格` });
  } else if (state.assetKind === "meshes" && by === "kind") {
    const label = { static: "静态", translucent: "透明 pass", baked: "烘焙（非刚体）", water: "水" };
    entries = (facets.kinds || []).map((k) => ({ key: k.kind, label: label[k.kind] || k.kind, n: `${k.n} 网格` }));
  } else if (state.assetKind === "meshes" && by === "unknown") {
    const counts = {};
    for (const r of rows) {
      if (r.named) continue;
      const g = r.unknown_group || "其他";
      counts[g] = (counts[g] || 0) + 1;
    }
    entries = Object.entries(counts).sort((x, y) => y[1] - x[1])
      .map(([k, n]) => ({ key: k, label: k.startsWith("ResourceId::") ? `共用贴图 ${k.split("::")[1]}` : k, n: `${n} 网格` }));
  } else if (state.assetKind === "meshes" && by === "area") {
    const buckets = [["画面 >10%", 0.1], ["1–10%", 0.01], ["0.1–1%", 0.001], ["<0.1%", 0]];
    entries = buckets.map(([label, lo], i) => {
      const hi = i ? buckets[i - 1][1] : Infinity;
      return { key: `${lo}`, label, n: `${rows.filter((r) => (r.screen || 0) >= lo && (r.screen || 0) < hi).length} 网格` };
    });
  } else if (state.assetKind === "meshes") {
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
  host.append(h("div", { class: "groups-label", text: `分组：${GROUP_BY_LABEL[by] || by}` }));
  host.append(h("div", {
    class: "group" + (state.group === null ? " active" : ""), onclick: () => { state.group = null; renderAssets(); },
  }, h("div", { text: "全部" }), h("div", { class: "n", text: `${(a[state.assetKind] || []).length} 项` })));
  for (const e of entries.slice(0, 400)) {
    let isActive = state.group === e.key;
    if (by === "code") isActive = state.filters.code === e.key;
    else if (by === "level") isActive = state.filters.level === e.key;
    else if (by === "kind") isActive = state.filters.kind === e.key;
    else if (by === "unknown") isActive = state.filters.unknownGroup === e.key;
    host.append(h("div", {
      class: "group" + (isActive ? " active" : ""), title: e.title || e.label,
      onclick: () => {
        if (by === "code") { state.filters.code = state.filters.code === e.key ? null : e.key; state.group = null; }
        else if (by === "level") { state.filters.level = state.filters.level === e.key ? null : e.key; state.group = null; }
        else if (by === "kind") { state.filters.kind = state.filters.kind === e.key ? null : e.key; state.group = null; }
        else if (by === "unknown") { state.filters.unknownGroup = state.filters.unknownGroup === e.key ? null : e.key; state.group = null; }
        else state.group = e.key;
        renderAssets();
      },
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
  const shown = rows.slice(0, 600);
  if (!rows.length) { grid.append(h("div", { class: "placeholder", text: "没有匹配的资源" })); return; }

  // Everything matching the filter is exported, not just the tiles rendered below: the cap keeps the DOM small
  // (a 900-mesh case), and exporting the visible page of a list would be a silent surprise.
  state.visibleRows = rows;
  updateExportBar(rows.length);
  updateFilterSummary(rows.length, (a.meshes || []).length);
  syncFilterChips();

  for (const r of shown) {
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
    const cap = h("div", { class: "cap" },
      h("div", { class: "t", text: title, title }), h("div", { class: "s", text: sub }));
    if (state.assetKind === "meshes") {
      // Facets on the card: what kind of thing this is (when the name says), and how much of the frame it covers.
      const tags = [];
      if (!r.named) tags.push(h("span", { class: "tag unknown", text: "未知" }));
      if (r.code) tags.push(h("span", { class: "tag code", text: r.code_label ? `${r.code}·${r.code_label}` : r.code,
                                        title: r.code_label ? `类别码 ${r.code}` : "类别码（未翻译）" }));
      if (r.kind !== "static") tags.push(h("span", { class: `tag kind ${r.kind}`, text: KIND_LABEL[r.kind] || r.kind }));
      if (r.screen > 0.001) tags.push(h("span", { class: "tag area", text: `画面 ${(r.screen * 100).toFixed(1)}%` }));
      if (tags.length) cap.append(h("div", { class: "tags" }, tags));
      cap.append(h("div", { class: "dl" },
        h("a", { href: r.export.obj, download: "", class: "dl-btn", title: "下载 OBJ（含顶点/UV/法线）",
                 onclick: (e) => e.stopPropagation(), text: "OBJ" }),
        h("a", { href: r.export.glb, download: "", class: "dl-btn", title: "下载 GLB（单文件，可直接拖进引擎/Blender）",
                 onclick: (e) => e.stopPropagation(), text: "GLB" })));
    } else if (state.assetKind === "textures" && r.export) {
      cap.append(h("div", { class: "dl" },
        h("a", { href: r.export, download: "", class: "dl-btn", title: "下载原始 PNG", text: "PNG" })));
    }
    const card = h("div", { class: "card" + (state.focus === r.id ? " focused" : "") },
      h("div", { class: "thumb" }, thumb), cap);
    if (state.assetKind === "meshes") {
      // Hovering swaps the static thumbnail for the shared WebGL canvas (one context for the whole page); clicking
      // selects it and shows its neighbours (material, textures) in the side panel. Double-clicking the little
      // "3D" corner opens the full viewer, so a click can mean "pick this" without losing the turntable.
      card.dataset.mesh = r.id;
      card.addEventListener("mouseenter", () => MeshViewer.hover(state.case, r.id, card));
      card.addEventListener("mouseleave", () => MeshViewer.unhover(card));
      card.addEventListener("click", (e) => {
        if (e.target.closest(".dl")) return;
        focusMesh(r.id, title);
      });
      card.addEventListener("dblclick", (e) => {
        if (e.target.closest(".dl")) return;
        MeshViewer.open(state.case, r.id, title);
      });
    } else if (state.assetKind === "textures" && r.url) {
      card.addEventListener("click", (e) => {
        if (e.target.closest(".dl")) return;
        openLightbox(`${r.w}x${r.h} · ${r.id.split("::").pop()}`, r.export || r.url);
      });
    }
    grid.append(card);
  }
  observeLazy(grid);
}

const KIND_LABEL = { static: "静态", translucent: "透明", baked: "烘焙", water: "水", placed: "世界放置" };

function syncFilterChips() {
  const f = state.filters;
  $$("#locate-bar .chip-toggle").forEach((b) => {
    let on = false;
    if (b.dataset.filter) on = !!f[b.dataset.filter];
    if (b.dataset.area) on = f.minArea === parseFloat(b.dataset.area);
    if (b.dataset.named) on = f.named === b.dataset.named;
    b.classList.toggle("on", on);
  });
  if ($("#group-by").value !== state.groupBy) $("#group-by").value = state.groupBy;
}

function updateFilterSummary(shown, total) {
  const f = state.filters;
  const bits = [];
  if (f.inFrame) bits.push("画面内");
  if (f.minArea) bits.push(`画面 >${(f.minArea * 100).toFixed(0)}%`);
  if (f.code) bits.push(f.code);
  if (f.level) bits.push(f.level);
  if (f.kind) bits.push(KIND_LABEL[f.kind] || f.kind);
  if (f.named === "no") bits.push("未知");
  if (f.named === "yes") bits.push("已识别");
  if (f.unknownGroup) bits.push(f.unknownGroup.startsWith("ResourceId::") ? "同贴图" : f.unknownGroup);
  const host = $("#filter-summary");
  host.replaceChildren();
  if (state.focusSet) {
    host.append(`点选命中 · `, h("b", { text: `${shown}` }), ` / ${total} 个网格`);
    return;
  }
  if (!bits.length) { host.textContent = `${total} 个网格`; return; }
  host.append(`${bits.join(" + ")} · `, h("b", { text: `${shown}` }), ` / ${total} 个网格`);
}

/* ---------- task management: rename, create from a capture ---------- */

function openRename(caseName) {
  const dlg = $("#rename");
  state.renameCase = caseName;
  const c = state.cases.find((x) => x.case === caseName);
  $("#rn-case").textContent = caseName;
  $("#rn-title").value = (c && c.title && c.title !== caseName) ? c.title : "";
  $("#rn-status").textContent = "";
  $("#rn-title").placeholder = `留空则显示目录名（${caseName}）`;
  if (!dlg.open) dlg.showModal();
  $("#rn-title").focus();
}

async function saveRename() {
  const name = state.renameCase;
  if (!name) return;
  const title = $("#rn-title").value.trim();
  $("#rn-save").disabled = true;
  try {
    await api(`/api/case/${encodeURIComponent(name)}/title`, { method: "POST", body: JSON.stringify({ title }) });
    await loadCases();
    if (state.case === name && state.detail) {
      // the detail pane prints the same title; refresh it without re-fetching every asset
      state.detail.title = title || name;
      renderCaseMain();
    }
    $("#rename").close();
    toast(title ? `已重命名为「${title}」` : `已恢复显示目录名 ${name}`);
  } catch (e) {
    $("#rn-status").textContent = e.message;
  } finally {
    $("#rn-save").disabled = false;
  }
}

function openNewCase() {
  const dlg = $("#newcase");
  $("#nc-path").value = "";
  $("#nc-source").value = "";
  $("#nc-id").value = "";
  $("#nc-title").value = "";
  $("#nc-info").replaceChildren();
  $("#nc-status").textContent = "";
  $("#nc-create").disabled = true;
  state.newCase = null;
  if (!dlg.open) dlg.showModal();
  $("#nc-path").focus();
}

function fmtSize(n) {
  if (!n && n !== 0) return "";
  if (n >= 1073741824) return `${(n / 1073741824).toFixed(2)} GB`;
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

function describeInput(r) {
  const cap = r.capture || {};
  const size = r.size ? ` · ${fmtSize(r.size)}` : "";
  if (r.source === "rdc") {
    return `RenderDoc 截帧${size}` + (cap.frame ? ` · 帧 ${cap.frame}` : "") +
      (cap.api ? ` · ${cap.api.toUpperCase()}` : "") + (cap.captured ? ` · 捕获于 ${cap.captured}` : "");
  }
  if (r.source === "3dmigoto") {
    return `3DMigoto 帧分析${size} · ${cap.files ?? "?"} 个文件` +
      (cap.draw_calls ? ` · ${cap.draw_calls} 次 draw/dispatch` : "") +
      (cap.reference ? " · 有参考帧 final-output.png" : " · 没有参考帧");
  }
  if (r.source === "unity-bundle") {
    return `Unity 资源包（${cap.magic}）${size} · ${cap.files ?? 1} 个文件`;
  }
  return r.source || "未知来源";
}

async function inspectCapture() {
  const p = $("#nc-path").value.trim();
  const source = $("#nc-source").value;
  const info = $("#nc-info");
  $("#nc-create").disabled = true;
  state.newCase = null;
  if (!p) { info.replaceChildren(h("div", { class: "why err", text: "先填本机路径" })); return; }
  info.replaceChildren(h("div", { class: "why", text: "检查中…" }));
  try {
    const r = await api(`/api/capture?path=${encodeURIComponent(p)}` +
      (source ? `&source=${encodeURIComponent(source)}` : ""));
    if (!r.ok) {
      info.replaceChildren(h("div", { class: "why err", text: r.why || "无法识别这个输入" }));
      return;
    }
    state.newCase = r;
    info.replaceChildren(h("div", { class: "why ok", text: describeInput(r) }));
    if (!$("#nc-id").value.trim()) $("#nc-id").value = r.suggested_id;
    $("#nc-create").disabled = false;
  } catch (e) {
    info.replaceChildren(h("div", { class: "why err", text: e.message }));
  }
}

async function createCase() {
  const id = $("#nc-id").value.trim();
  const title = $("#nc-title").value.trim();
  if (!state.newCase) { await inspectCapture(); if (!state.newCase) return; }
  $("#nc-create").disabled = true;
  $("#nc-status").textContent = "创建中…";
  try {
    const r = await api("/api/cases", { method: "POST",
      body: JSON.stringify({ case: id, source: state.newCase.source, path: state.newCase.input, title,
                             game: state.newCase.game_hint || undefined }) });
    $("#newcase").close();
    await loadCases();
    await selectCase(r.case);
    // creating the directory is cheap; the expensive first step is a normal job. Its name depends on the source,
    // and the input is passed along so it also works for a case whose manifest is incomplete.
    const payload = { step: r.start };
    if (r.start === "ingest-3dmigoto") payload.frame_dir = r.input;
    if (r.start === "ingest-unity") payload.bundle = r.input;
    try {
      await api(`/api/case/${encodeURIComponent(r.case)}/run`, { method: "POST", body: JSON.stringify(payload) });
      const started = { survey: "截帧分析", "ingest-3dmigoto": "3DMigoto 摄入", "ingest-unity": "Unity 摄入" }[r.start] || r.start;
      toast(`已创建 ${caseTitle({ case: r.case, title })}，${started}已开始`);
    } catch (e) {
      toast(`已创建 ${r.case}，但自动运行没启动：${e.message}`, true);
    }
    await refreshAll();
  } catch (e) {
    $("#nc-status").textContent = e.message;
    $("#nc-create").disabled = false;
  }
}

/* ---------- locate: pick on the reference frame, then follow the links ---------- */

function shortSource(src) {
  return src ? src.split(".").slice(-1)[0].split("/").slice(-1)[0] : null;
}

async function openPicker() {
  const d = state.detail;
  if (!d || !d.reference) { toast("该案例没有参考帧", true); return; }
  const cam = (state.assets && state.assets.camera) || {};
  if (cam.ok === false) { toast(cam.why || "该案例无法在画面上定位", true); return; }
  // load at the display size the workbench already serves (<=1600 px): a capture-resolution image would only be
  // re-decoded at the same on-screen size and would make each click wait on the browser's raster
  const url = d.reference;
  const dlg = $("#picker");
  const img = dlg.querySelector("img");
  dlg.querySelector(".picker-hits").replaceChildren(h("div", { class: "why", text: "在图上点一下，定位到那里的资产" }));
  dlg.querySelector(".picker-dims").textContent = "";
  img.src = url;
  img.decode().then(() => {
    dlg.querySelector(".picker-dims").textContent = `${img.naturalWidth} x ${img.naturalHeight} · 相机拟合 ${(cam.vp_match * 100).toFixed(1)}%`;
  }).catch(() => {});
  img.onclick = (ev) => {
    const r = img.getBoundingClientRect();
    const x = (ev.clientX - r.left) / r.width;
    const y = (ev.clientY - r.top) / r.height;
    pickAt(x, y);
  };
  if (!dlg.open) dlg.showModal();
}

async function pickAt(x, y) {
  const dlg = $("#picker");
  const hits = dlg.querySelector(".picker-hits");
  const mark = dlg.querySelector(".picker-mark");
  mark.style.display = "block";
  mark.style.left = `${x * 100}%`;
  mark.style.top = `${y * 100}%`;
  hits.replaceChildren(h("div", { class: "why", text: "定位中…" }));
  try {
    const r = await api(`/api/pick?case=${encodeURIComponent(state.case)}&x=${x.toFixed(4)}&y=${y.toFixed(4)}`);
    state.focusHits = r.hits || [];
    renderHits(r);
  } catch (e) {
    hits.replaceChildren(h("div", { class: "why err", text: e.message }));
  }
}

function renderHits(r) {
  const host = $("#picker").querySelector(".picker-hits");
  host.replaceChildren();
  if (!r.ok) {
    host.append(h("div", { class: "why err", text: r.why || "无法定位" }));
    return;
  }
  if (!r.hits.length) {
    host.append(h("div", { class: "why", text: "这个位置没有命中几何（可能是天空／粒子，或相机拟合偏差）" }));
    return;
  }
  host.append(h("div", { class: "why", text: `${r.candidates} 个候选实例，按距离排序（相机是拟合出来的，边缘可能有偏差）` }));
  // Hits within 1 m are camera effects (typically the reconstructed sky dome); listed after the real ones.
  const ordered = [...r.hits].sort((a, b) => (a.near_camera ? 1 : 0) - (b.near_camera ? 1 : 0) || a.depth_m - b.depth_m);
  for (const h0 of ordered) {
    const src = shortSource(h0.source);
    const row = h("div", { class: "hit" + (h0.near_camera ? " near" : "") },
      h("div", { class: "hit-main" },
        h("div", { class: "t", text: src || `未识别 · ${h0.mesh}` }),
        h("div", { class: "s", text: `${h0.depth_m.toFixed(1)} m · ${KIND_LABEL[h0.kind] || h0.kind} · 画面 ${(h0.screen_area * 100).toFixed(1)}%${h0.near_camera ? " · 贴近相机（可能是天空壳）" : ""}` })),
      h("button", { class: "ghost", text: "定位", onclick: () => focusMesh(h0.mesh, src || h0.mesh, { fromPick: true }) }));
    host.append(row);
  }
}

async function focusMesh(mesh, title, opts) {
  state.focus = mesh;
  state.focusTitle = title || mesh;
  if (opts && opts.fromPick) {
    // Coming from a pick: narrow the grid to what the click hit, so "locate" actually locates. The full list is one
    // "清除筛选" away, and the panel keeps the asset even if a later filter hides it.
    const hits = (state.focusHits || []).filter((h0) => !h0.near_camera);
    const keep = new Set(hits.map((h0) => h0.mesh));
    keep.add(mesh);
    state.focusSet = keep;
  }
  renderAssets();
  const panel = $("#asset-related");
  panel.classList.add("show");
  panel.replaceChildren(h("div", { class: "why", text: "读取关联…" }));
  try {
    const rel = await api(`/api/related?case=${encodeURIComponent(state.case)}&mesh=${encodeURIComponent(mesh)}`);
    renderRelated(rel, title);
  } catch (e) {
    panel.replaceChildren(h("div", { class: "why err", text: e.message }));
  }
  // a pick closed the dialog; the card is now in a narrowed grid, so bring it into view
  const card = $(`.card[data-mesh="${CSS.escape(mesh)}"]`);
  if (card) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  if (opts && opts.fromPick) $("#picker").close();
}

function textureUrl(texId) {
  const t = ((state.assets || {}).textures || []).find((x) => x.id === texId);
  return t ? { url: t.url, export: t.export, w: t.w, h: t.h, format: t.format } : null;
}

function renderRelated(rel, title) {
  const panel = $("#asset-related");
  panel.replaceChildren();
  const close = h("button", { class: "ghost related-close", text: "×", title: "关闭",
                              onclick: () => { panel.classList.remove("show"); state.focus = null; renderAssets(); } });
  panel.append(h("div", { class: "related-head" },
    h("div", {}, h("div", { class: "t", text: shortSource(rel.source) || title || rel.mesh }),
      h("div", { class: "s", text: `${rel.mesh} · ${fmt(rel.instances)} 实例 · 画面 ${(rel.screen_area * 100).toFixed(2)}%` })),
    close));
  const only = (ids, label) => {
    if (!ids.length) return;
    panel.append(h("div", { class: "related-sec" },
      h("div", { class: "related-label", text: `${label}（${ids.length}）` }),
      h("div", { class: "related-chips" }, ids.slice(0, 60).map((id) =>
        h("span", { class: "chip", title: id, onclick: () => focusMesh(id, id, {}) },
          ((state.assets.meshes.find((m) => m.id === id) || {}).source || id))))));
  };
  // textures first: they are what makes an asset recognisable, and they are downloadable at full size
  if (rel.textures.length) {
    panel.append(h("div", { class: "related-label", text: `材质贴图（${rel.textures.length}）` }));
    const grid = h("div", { class: "related-tex" });
    for (const t of rel.textures) {
      const info = textureUrl(t);
      const ti = rel.texture_info[t] || {};
      grid.append(h("a", { class: "tx", href: info && info.export, download: "", title: `${t} · ${ti.w}x${ti.h} ${ti.format || ""}` },
        info && info.url ? h("img", { src: info.url, loading: "lazy", alt: "" }) : h("span", { class: "s", text: "无预览" }),
        h("span", { class: "s", text: `${ti.w || "?"}x${ti.h || "?"}` })));
    }
    panel.append(grid);
  }
  panel.append(h("div", { class: "related-sec" },
    h("div", { class: "related-label", text: "材质" }),
    h("div", { class: "related-chips" }, rel.materials.map((m) =>
      h("span", { class: "chip mono", text: m, title: m })))));
  only(rel.same_material, "同材质的网格");
  only(rel.same_texture, "同贴图的网格");
  const loc = state.focusHits.find((h0) => h0.mesh === rel.mesh);
  panel.append(h("div", { class: "related-actions" },
    h("button", { text: "只看同材质+同贴图", onclick: () => {
      const keep = new Set([rel.mesh, ...rel.same_material, ...rel.same_texture]);
      state.filters.inFrame = false; state.filters.code = null; state.filters.level = null;
      state.filters.kind = null; state.filters.unknownGroup = null; state.filters.named = "all";
      state.filters.minArea = 0;
      state.focusSet = keep;
      state.focusSet = keep;
      state.visibleRows = (state.assets.meshes || []).filter((m) => keep.has(m.id));
      renderAssetsWithSet();
    } }),
    h("button", { class: "primary", text: "导出这个资产", onclick: () => exportMeshIds([rel.mesh]) })));
}

function renderAssetsWithSet() {
  // A filter that is not one of the facet chips (an explicit set chosen from the relation panel). Kept in `state`
  // so the export bar and the grid agree on what "current selection" means.
  renderGroups();
  const grid = $("#asset-grid");
  grid.replaceChildren();
  const rows = state.visibleRows || [];
  updateExportBar(rows.length);
  for (const r of rows.slice(0, 600)) {
    const title = r.source || r.id;
    const card = h("div", { class: "card" + (state.focus === r.id ? " focused" : "") },
      h("div", { class: "thumb" }, h("img", { "data-src": r.thumb, alt: "", class: "lazy" })),
      h("div", { class: "cap" },
        h("div", { class: "t", text: title, title }),
        h("div", { class: "s", text: `${fmt(r.verts)} 顶点 · ${fmt(r.instances)} 实例` }),
        h("div", { class: "dl" },
          h("a", { href: r.export.obj, download: "", class: "dl-btn", text: "OBJ", onclick: (e) => e.stopPropagation() }),
          h("a", { href: r.export.glb, download: "", class: "dl-btn", text: "GLB", onclick: (e) => e.stopPropagation() }))));
    card.addEventListener("click", (e) => { if (!e.target.closest(".dl")) focusMesh(r.id, title); });
    card.addEventListener("dblclick", (e) => { if (!e.target.closest(".dl")) MeshViewer.open(state.case, r.id, title); });
    grid.append(card);
  }
  observeLazy(grid);
}

/* ---------- export ---------- */

function updateExportBar(n) {
  const meshes = state.assetKind === "meshes";
  $("#export-bar").classList.toggle("hidden", !meshes);
  const label = meshes ? `导出 ${n} 个网格` : "";
  $("#btn-export-obj").textContent = meshes ? `OBJ · ${n}` : "OBJ";
  $("#btn-export-obj").title = `${label}（OBJ + 材质 + 贴图，打包为 zip）`;
  $("#btn-export-glb").title = `${label}（GLB，不含贴图，打包为 zip）`;
  $("#btn-export-glb").textContent = meshes ? `GLB · ${n}` : "GLB";
}

async function exportMeshes(fmt) {
  if (!state.case || !state.visibleRows || !state.visibleRows.length) return;
  await exportMeshIds(state.visibleRows.map((r) => r.id), fmt);
}

async function exportMeshIds(ids, fmt) {
  if (!state.case || !ids || !ids.length) return;
  const btn = fmt === "obj" ? $("#btn-export-obj") : $("#btn-export-glb");
  if (btn) btn.disabled = true;
  try {
    const r = await api(`/api/case/${encodeURIComponent(state.case)}/export`,
      { method: "POST", body: JSON.stringify({ fmt, meshes: ids.join(","), textures: fmt === "obj" }) });
    toast(`已开始导出 ${ids.length} 个网格（作业 ${r.job.id}）`);
    pollExport(r.job.id);
  } catch (e) {
    if (btn) btn.disabled = false;
    toast(e.message, true);
  }
}

let exportTimer = null;
function pollExport(jid) {
  clearInterval(exportTimer);
  const box = $("#export-status");
  box.className = "export-status show";
  box.textContent = "导出中…";
  exportTimer = setInterval(async () => {
    let st;
    try { st = await api(`/api/export/${encodeURIComponent(jid)}`); }
    catch (_) { return; }
    if (st.state === "running" || st.state === "unknown (process gone, no exit marker)") {
      box.textContent = `导出中… ${st.seconds ? Math.round(st.seconds) + " s" : ""}`;
      return;
    }
    clearInterval(exportTimer);
    $("#btn-export-obj").disabled = false;
    $("#btn-export-glb").disabled = false;
    if (st.state === "done") {
      const mb = (st.bytes || 0) / 1048576;
      box.innerHTML = "";
      box.append(`已导出 ${st.meshes} 个网格 · ${st.textures} 张贴图 · ${mb.toFixed(1)} MB `,
        h("a", { href: `/export/download?id=${encodeURIComponent(jid)}`, download: "", class: "dl-btn primary",
                 text: "下载 zip" }));
      toast("导出完成");
    } else {
      box.className = "export-status show err";
      box.textContent = `导出失败：${st.error || st.state}`;
      toast("导出失败", true);
    }
  }, 800);
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
  const addShot = (label, url) => {
    if (!url) return;
    const img = h("img", { src: url, alt: label, loading: "lazy", title: "点击放大" });
    img.addEventListener("click", () => openLightbox(label, url));
    shots.append(h("div", { class: "shot" },
      h("h4", {}, h("span", { text: label }), h("span", { class: "zoom", text: "点击放大" })),
      img));
  };
  addShot("参考帧（游戏原帧）", d.reference);
  const shotList = d.shots || [];
  for (const s of shotList) addShot(s.file, s.url);
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

/* ---------- lightbox ---------- */

function openLightbox(name, url) {
  const dlg = $("#lightbox");
  const body = dlg.querySelector(".lightbox-body");
  const img = body.querySelector("img");
  body.classList.remove("zoomed");
  img.src = url;
  img.alt = name;
  dlg.querySelector(".name").textContent = name;
  dlg.querySelector(".dims").textContent = "";
  // The served image is a display-sized copy (<=1600 px), so report what is actually on screen rather than a
  // capture resolution the viewer is not showing.
  img.decode().then(() => {
    dlg.querySelector(".dims").textContent = `${img.naturalWidth} x ${img.naturalHeight}`;
  }).catch(() => {});
  if (!dlg.open) dlg.showModal();
}

function wireLightbox() {
  const dlg = $("#lightbox");
  const body = dlg.querySelector(".lightbox-body");
  const img = body.querySelector("img");
  img.addEventListener("click", () => body.classList.toggle("zoomed"));
  dlg.querySelector(".lightbox-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  // keyboard: Esc closes (native), +/- zoom in and out of the fit state
  dlg.addEventListener("keydown", (e) => {
    if (e.key === "+" || e.key === "=") { body.classList.add("zoomed"); e.preventDefault(); }
    if (e.key === "-") { body.classList.remove("zoomed"); e.preventDefault(); }
  });
  dlg.addEventListener("close", () => { img.src = ""; });
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
  wireLightbox();
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("#refresh").addEventListener("click", async () => {
    await loadCases();
    await refreshAll();
    toast("已刷新");
  });
  $("#run-all").addEventListener("click", () => startStep("run", false));
  $("#btn-export-obj").addEventListener("click", () => exportMeshes("obj"));
  $("#btn-export-glb").addEventListener("click", () => exportMeshes("glb"));
  $("#btn-pick").addEventListener("click", openPicker);
  $("#btn-new-case").addEventListener("click", openNewCase);
  $("#nc-inspect").addEventListener("click", inspectCapture);
  $("#nc-path").addEventListener("change", inspectCapture);
  $("#nc-source").addEventListener("change", () => { if ($("#nc-path").value.trim()) inspectCapture(); });
  $("#nc-create").addEventListener("click", createCase);
  $("#newcase .newcase-close").addEventListener("click", () => $("#newcase").close());
  $("#rename .rename-close").addEventListener("click", () => $("#rename").close());
  $("#rn-save").addEventListener("click", saveRename);
  $("#rn-title").addEventListener("keydown", (e) => { if (e.key === "Enter") saveRename(); });
  $("#nc-title").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("#nc-create").disabled) createCase(); });
  $("#case-filter").addEventListener("input", (e) => { state.caseQuery = e.target.value.trim(); renderCaseList(); });
  $("#picker .picker-close").addEventListener("click", () => $("#picker").close());
  $("#picker").addEventListener("click", (e) => { if (e.target.id === "picker") $("#picker").close(); });
  $("#group-by").addEventListener("change", (e) => {
    state.groupBy = e.target.value;
    state.group = null;
    renderAssets();
  });
  $$("#locate-bar .chip-toggle").forEach((b) => b.addEventListener("click", () => {
    const f = state.filters;
    if (b.dataset.filter) f.inFrame = !f.inFrame;
    if (b.dataset.area) {
      const v = parseFloat(b.dataset.area);
      f.minArea = f.minArea === v ? 0 : v;
    }
    if (b.dataset.named) f.named = f.named === b.dataset.named ? "all" : b.dataset.named;
    state.focusSet = null;          // an explicit facet filter replaces a pick's narrowed set
    syncFilterChips();
    renderAssets();
  }));
  $("#btn-clear-filters").addEventListener("click", () => {
    state.filters = { inFrame: false, minArea: 0, kind: null, code: null, level: null, named: "all", unknownGroup: null };
    state.group = null;
    state.focusSet = null;
    state.search = "";
    $("#asset-search").value = "";
    syncFilterChips();
    renderAssets();
  });
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
