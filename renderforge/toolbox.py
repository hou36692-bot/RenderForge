"""Toolbox registry: load, doctor (what is installed here), plan (which tools for game + target), docs (generate TOOLBOX.md)."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TB = ROOT / "toolbox"
STATUS_ORDER = ["verified-here", "verified-synthetic", "verified-prior", "installed", "candidate", "reference", "rejected"]
STATUS_ICON = {"verified-here": "OK", "verified-synthetic": "OK*", "verified-prior": "OK(prior)", "installed": "inst", "candidate": "cand", "reference": "ref", "rejected": "no"}


def load():
    reg = json.loads((TB / "registry.json").read_text(encoding="utf-8"))
    meta = json.loads((TB / "github_meta.json").read_text(encoding="utf-8")) if (TB / "github_meta.json").exists() else {}
    tools = {t["id"]: t for t in reg["tools"]}
    for t in tools.values():
        t["_gh"] = meta.get(t.get("repo", ""), {})
    return reg, tools, json.loads((TB / "games.json").read_text(encoding="utf-8"))["games"], json.loads((TB / "recipes.json").read_text(encoding="utf-8"))["recipes"]


def _resolve(p):
    p = os.path.expandvars(p)
    q = Path(p) if Path(p).is_absolute() else ROOT / p
    if "*" in str(q):
        return next(iter(q.parent.glob(q.name)), None) if q.parent.exists() else None
    return q if q.exists() else None


def doctor():
    reg, tools, games, _ = load()
    rows = []
    for t in tools.values():
        loc = t.get("local")
        if not loc:
            continue
        found = _resolve(loc["detect"])
        ver = ""
        if found and loc.get("version_cmd"):
            try:
                ver = subprocess.run([c.replace("{path}", str(found)) for c in loc["version_cmd"]], capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[0]
            except Exception:  # noqa: BLE001
                ver = "?"
        rows.append((t["id"], bool(found), str(found or loc["detect"]), ver))
    # python packages
    for pkg in ("UnityPy", "numpy", "PIL"):
        try:
            m = __import__(pkg)
            rows.append((f"py:{pkg}", True, getattr(m, "__version__", ""), ""))
        except ImportError:
            rows.append((f"py:{pkg}", False, "not installed", ""))
    return rows


def plan(game, target):
    reg, tools, games, recipes = load()
    g = games.get(game)
    if not g:
        return [f"unknown game '{game}'. known: {', '.join(games)}"]
    out = [f"Game: {g['name']}  engine={g['engine']}  target={target}", f"Packaging: {g.get('packaging', '?')}", f"Install: {g.get('install', '?')}", ""]
    caps = []
    for c in g.get("capture", []):
        caps.append("capture:rdc" if c == "renderdoc" else "capture:3dmigoto-frame-analysis")
    out.append("Capture routes: " + ", ".join(f"{c} [{tools[c]['status']}]" for c in g.get("capture", [])))
    out.append("Source readers: " + ", ".join(f"{c} [{tools[c]['status']}]" for c in g.get("source", [])))
    out.append("")
    chosen = []
    for rid, r in recipes.items():
        if target not in r["targets"]:
            continue
        if any(n in caps for n in r["needs"]) or rid.startswith(("R3", "R4", "R5")):
            if rid.startswith("R3") and g["engine"] != "unreal":
                continue
            if rid.startswith("R4") and g["engine"] != "unity":
                continue
            chosen.append(rid)
    for rid in chosen:
        r = recipes[rid]
        out.append(f"{rid}: {r['title']}")
        for s in r["steps"]:
            st = ", ".join(f"{t}[{STATUS_ICON.get(tools[t]['status'], '?')}]" for t in s["tools"])
            out.append(f"   - {s['do']}  ->  {st}")
        if r.get("caveat"):
            out.append(f"   ! {r['caveat']}")
        out.append("")
    out.append("Verified so far: " + ("; ".join(g["verified"]) if g["verified"] else "nothing"))
    out.append("Gaps:")
    out.extend(f"   * {x}" for x in g["gaps"])
    return out


def _gh_line(t):
    g = t.get("_gh", {})
    lic = g.get("license") or t.get("license_text") or "-"
    last = (g.get("pushed_at") or "")[:10] or "-"
    rel = g.get("latest_release") or t.get("pypi") and "PyPI" or "-"
    return lic, last, str(rel)[:24]


def docs():
    reg, tools, games, recipes = load()
    L = []
    w = L.append
    w("# RenderForge 工具能力地图\n")
    w("> 由 `python -m renderforge docs` 从 `toolbox/registry.json`、`games.json`、`recipes.json` 生成，请改数据文件而不是本文件。\n")
    w("状态说明：**OK** = 本工作区对真实数据实际跑过；**OK\\*** = 在我生成的合成数据上跑过；**OK(prior)** = 你之前的项目里有成功证据，本次未重跑；"
      "**inst** = 本机已安装但未使用；**cand** = 候选，未安装；**ref** = 只作参考。\n")
    for layer, desc in reg["layers"].items():
        ts = [t for t in tools.values() if t["layer"] == layer]
        if not ts:
            continue
        w(f"\n## {layer} — {desc}\n")
        w("| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |")
        w("|---|---|---|---|---|---|---|---|")
        for t in sorted(ts, key=lambda x: STATUS_ORDER.index(x["status"])):
            lic, last, rel = _gh_line(t)
            note = (t.get("notes") or "").replace("|", "/").replace("\n", " ")
            w(f"| [{t['name']}]({t['url']}) | {t['kind']} | {STATUS_ICON[t['status']]} | {t['integration']} | {lic} | {last} | {rel} | {note} |" if t.get("url") else
              f"| {t['name']} | {t['kind']} | {STATUS_ICON[t['status']]} | {t['integration']} | {lic} | {last} | {rel} | {note} |")
    w("\n## 游戏适配档案\n")
    w("| 游戏 | 引擎 | 截帧路线 | 资源读取 | 已验证 | 缺口 |")
    w("|---|---|---|---|---|---|")
    for gid, g in games.items():
        w(f"| {g['name']} (`{gid}`) | {g['engine']} | {', '.join(g.get('capture', []))} | {', '.join(g.get('source', []))} | "
          f"{'；'.join(g['verified']) or '无'} | {'；'.join(g['gaps'])} |")
    w("\n## 工作流（recipes）\n")
    for rid, r in recipes.items():
        w(f"### {rid}: {r['title']}")
        for s in r["steps"]:
            w(f"1. {s['do']} — `{', '.join(s['tools'])}`" + (f"　`{s['cmd']}`" if s.get("cmd") else ""))
        if r.get("caveat"):
            w(f"\n> 注意：{r['caveat']}")
        w("")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "TOOLBOX.md").write_text("\n".join(L), encoding="utf-8")
    return len(L)
