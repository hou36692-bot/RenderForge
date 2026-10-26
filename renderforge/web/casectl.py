"""Inspect a RenderDoc capture before it becomes a case, and create the case directory for it.

Why this exists: the workbench could only *browse* cases that a CLI command had already created, so "add a new
task" meant leaving the page. This module answers the two questions the page needs before it can offer that:

  * is this file really a RenderDoc capture (`RDOC` magic)? -- a cheap read of 8 bytes, not a second full parse;
  * what would the case be called, and where would it go? -- the directory name has to be decided now, because
    engine asset folders derive from it and are not renamed later.

Nothing here runs RenderDoc. It only names and seeds; the expensive survey/export stays a normal pipeline job.
"""
import datetime
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WS = ROOT / "workspace"
RDOC_MAGIC = b"RDOC"

CASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")      # a directory name, not a display name
RESERVED = {"_jobs", "_logs", "_exports", "_selftest", "_scene_ue_space", "_scene_unity_space"}


def look(path):
    """Describe a candidate capture without touching anything: {ok, why, rdc, size, capture, suggested_id}."""
    p = Path(path)
    if not p.is_absolute():
        p = (ROOT / p)
    try:
        p = p.resolve()
    except OSError as e:
        return {"ok": False, "why": f"无法解析路径：{e}"}
    if not p.is_file():
        return {"ok": False, "why": f"文件不存在：{p}"}
    st = p.stat()
    if p.suffix.lower() != ".rdc":
        return {"ok": False, "why": "不是 .rdc 文件（目前只能从 RenderDoc 截帧新建任务）", "rdc": str(p), "size": st.st_size}
    try:
        with p.open("rb") as f:
            magic = f.read(4)
    except OSError as e:
        return {"ok": False, "why": f"读不了这个文件：{e}", "rdc": str(p), "size": st.st_size}
    if magic != RDOC_MAGIC:
        return {"ok": False, "why": "文件头不是 RDOC，不是 RenderDoc 截帧", "rdc": str(p), "size": st.st_size}
    return {"ok": True, "why": None, "rdc": str(p), "size": st.st_size, "capture": _capture_desc(p),
            "suggested_id": suggest_id(p)}


def _capture_desc(p):
    """Frame number and capture time from the file name, when it carries them (RenderDoc's default naming does)."""
    out = {}
    m = re.search(r"frame(\d+)", p.stem, re.I)
    if m:
        out["frame"] = int(m.group(1))
    m = re.search(r"(\d{8})[_-](\d{6})", p.stem)
    if m:
        try:
            dt = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            out["captured"] = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    m = re.search(r"(?:^|[_-])(dx11|dx12|vulkan|gl)(?:[_-]|$)", p.stem, re.I)
    if m:
        out["api"] = m.group(1).lower()
    m = re.search(r"^(.*?)(?:[_-](?:dx11|dx12|vulkan|gl))", p.stem, re.I)
    if m:
        out["game_hint"] = m.group(1)
    return out


def suggest_id(p):
    """A readable, stable directory id: <game>-<frame> when the capture name says both, else a timestamp.

    The directory id is not the display name (that is `case.json -> title` and can be anything); it is the thing
    engine asset paths and screenshot file names are built from, so it is chosen once and then left alone."""
    cap = _capture_desc(p)
    game = (cap.get("game_hint") or "").strip().lower()
    game = re.sub(r"[^a-z0-9]+", "-", game).strip("-")
    # the raw hint is often long ("wutheringwaves"); the profiles know the short ids
    for short, keys in (("wuwa", ("wuthering", "mingchao")), ("zzz", ("zenless", "zzz")),
                        ("genshin", ("genshin",)), ("hsr", ("starrail", "star-rail")), ("endfield", ("endfield",))):
        if any(k in game for k in keys):
            game = short
            break
    if game and cap.get("frame"):
        return f"{game}-f{cap['frame']}"
    if cap.get("frame"):
        return f"frame{cap['frame']}"
    return "capture-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def create(case_id, rdc, title=None, game=None):
    """Create workspace/<case_id>/case.json for a capture. Never overwrites an existing case, never copies the .rdc."""
    if not CASE_RE.match(case_id or ""):
        raise ValueError(f"任务 id 只能用字母数字和 . _ -，且不能以符号开头：{case_id!r}")
    if case_id in RESERVED:
        raise ValueError(f"{case_id} 是保留名")
    d = WS / case_id
    if d.exists():
        raise ValueError(f"任务 {case_id} 已存在（目录 {d}）")
    info = look(rdc)
    if not info["ok"]:
        raise ValueError(info["why"])
    d.mkdir(parents=True)
    case = {"source": "rdc", "rdc": info["rdc"], "created": datetime.datetime.now().isoformat(timespec="seconds")}
    if title:
        case["title"] = str(title).strip()[:80]
    if game:
        case["game"] = game
    # the project's own _json cache keys on mtime; a new directory simply has no entry, so no invalidation needed
    (d / "case.json").write_text(json.dumps(case, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"case": case_id, "path": str(d), "case_json": case, "capture": info["capture"], "size": info["size"]}


def rename_title(case_id, title):
    """Set the human-facing name only. The directory, engine asset folders and recorded file names do not change."""
    from .. import pipeline
    d = WS / case_id
    if not d.is_dir():
        raise KeyError(f"no such case: {case_id}")
    c = pipeline.load_case(case_id)
    title = (title or "").strip()[:80]
    if title:
        c["title"] = title
    else:
        c.pop("title", None)
    pipeline.save_case(case_id, c)
    return {"case": case_id, "title": c.get("title")}


def delete(case_id):
    """Remove a case directory. Only for a case with no build/export of value? No: the caller decides.
    This exists so the page never shells out; it refuses anything that is not a case directory."""
    d = WS / case_id
    if not CASE_RE.match(case_id or "") or d.name.startswith("_"):
        raise ValueError(f"not a case directory: {case_id}")
    if not (d / "case.json").exists():
        raise ValueError(f"{case_id} has no case.json; refusing to delete a directory that may not be a case")
    shutil.rmtree(d)
    return {"case": case_id, "deleted": True}
