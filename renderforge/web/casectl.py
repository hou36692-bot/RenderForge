"""Inspect a candidate input before it becomes a case, and create the case directory for it.

Why this exists: the workbench could only *browse* cases that a CLI command had already created, so "add a new
task" meant leaving the page. This module answers the two questions the page needs before it can offer that:

  * is this path a source the pipeline can ingest, and which one? -- a magic read for RenderDoc/Unity, a directory
    scan for a 3DMigoto FrameAnalysis dump; never a full parse, never a copy;
  * what would the case be called, and where would it go? -- the directory name has to be decided now, because
    engine asset folders derive from it and are not renamed later.

The three sources map to the same three `case.json` shapes the CLI writes:

  RenderDoc `.rdc`              -> source `rdc`,          `rdc`       (first step: survey)
  3DMigoto FrameAnalysis dir    -> source `3dmigoto`,     `frame_dir` (first step: ingest-3dmigoto)
  Unity bundle / serialized     -> source `unity-bundle`, `bundle`    (first step: ingest-unity)

Nothing here runs RenderDoc or UnityPy. It only names and seeds; the expensive ingest stays a normal pipeline job.
"""
import datetime
import json
import os
import re
import shutil
from pathlib import Path

from .. import caseinfo

ROOT = Path(__file__).resolve().parent.parent.parent
WS = ROOT / "workspace"
RDOC_MAGIC = b"RDOC"
UNITY_MAGIC = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"UnityArchive")
DRAW_FILE_RE = re.compile(r"^\d{6}-")

CASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")      # a directory name, not a display name
RESERVED = {"_jobs", "_logs", "_exports", "_selftest", "_scene_ue_space", "_scene_unity_space"}

SOURCE_START = {"rdc": "survey", "3dmigoto": "ingest-3dmigoto", "unity-bundle": "ingest-unity"}
SOURCE_KEY = {"rdc": "rdc", "3dmigoto": "frame_dir", "unity-bundle": "bundle"}


def look(path, source=None):
    """Describe a candidate input without touching it.

    `source` is one of rdc / 3dmigoto / unity-bundle; None or "auto" sniffs it. Returns
    {ok, why, source, input, size, capture, game_hint, suggested_id, start}.
    """
    source = None if source in (None, "", "auto") else str(source)
    if source not in (None, *SOURCE_START):
        return {"ok": False, "why": f"未知来源：{source}（可用：auto, {', '.join(SOURCE_START)}）"}
    p = Path(path or "")
    if not str(path or "").strip():
        return {"ok": False, "why": "先填一个本机路径"}
    if not p.is_absolute():
        p = ROOT / p
    try:
        p = p.resolve()
    except OSError as e:
        return {"ok": False, "why": f"无法解析路径：{e}"}
    if not p.exists():
        return {"ok": False, "why": f"路径不存在：{p}", "input": str(p)}

    if source == "rdc":
        return _look_rdc(p)
    if source == "3dmigoto":
        return _look_frame_dir(p)
    if source == "unity-bundle":
        return _look_unity(p)

    # auto: a .rdc name or RDOC header wins; then a FrameAnalysis directory; then a Unity container
    if p.is_file():
        if p.suffix.lower() == ".rdc" or _head(p, 4) == RDOC_MAGIC:
            return _look_rdc(p)
        if _unity_magic(p):
            return _look_unity(p)
        return {"ok": False, "input": str(p),
                "why": "无法识别这个文件：既没有 RenderDoc 的 RDOC 头，也没有 Unity 资源包头"
                       "（UnityFS / UnityWeb / UnityRaw / UnityArchive）"}
    if _frame_signature(p):
        return _look_frame_dir(p)
    if _unity_magic_in_dir(p):
        return _look_unity(p)
    return {"ok": False, "input": str(p),
            "why": "无法识别这个目录：没有 3DMigoto FrameAnalysis 的 log.txt + 逐 draw 转储，"
                   "也没有 Unity 资源包头"}


def _look_rdc(p):
    if not p.is_file():
        return {"ok": False, "input": str(p), "why": "RenderDoc 来源需要一个 .rdc 文件"}
    st = p.stat()
    try:
        with p.open("rb") as f:
            magic = f.read(4)
    except OSError as e:
        return {"ok": False, "why": f"读不了这个文件：{e}", "input": str(p), "size": st.st_size}
    if magic != RDOC_MAGIC:
        return {"ok": False, "why": "文件头不是 RDOC，不是 RenderDoc 截帧", "input": str(p), "size": st.st_size}
    game = caseinfo._game_from_input(str(p))
    desc = _capture_desc(p)
    if game:
        desc["game_hint"] = game
    return {"ok": True, "why": None, "source": "rdc", "input": str(p), "rdc": str(p), "size": st.st_size,
            "capture": desc, "game_hint": game, "suggested_id": _suggest_rdc_id(p, desc), "start": "survey"}


def _frame_signature(p):
    """Cheap proof that a directory is a 3DMigoto FrameAnalysis dump: log.txt plus at least one draw dump."""
    if not p.is_dir():
        return False
    try:
        with os.scandir(p) as it:
            names = {e.name for e in it}
    except OSError:
        return False
    return "log.txt" in names and any(DRAW_FILE_RE.match(n) for n in names)


def _scan_frame_dir(p):
    files = draws = 0
    has_log = False
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    if not e.is_file():
                        continue
                except OSError:
                    continue
                files += 1
                if e.name == "log.txt":
                    has_log = True
                elif DRAW_FILE_RE.match(e.name):
                    draws += 1
    except OSError:
        pass
    return {"files": files, "draws": draws, "log": has_log}


def _look_frame_dir(p):
    if not p.is_dir():
        return {"ok": False, "input": str(p), "why": "3DMigoto 来源需要一个 FrameAnalysis 目录"}
    scan = _scan_frame_dir(p)
    if not scan["log"]:
        return {"ok": False, "input": str(p), "why": "这个目录没有 log.txt，不像 3DMigoto FrameAnalysis 转储"}
    if not scan["draws"]:
        return {"ok": False, "input": str(p),
                "why": "这个目录没有逐 draw 的转储文件（NNNNNN-*），不像 3DMigoto FrameAnalysis 转储"}
    # 3DMigoto writes the dump itself in FrameAnalysis-*, but the capture tool (XXMI) puts the summary and the
    # reference frame one level up, beside the dump directory
    summary = _json_soft(p / "capture-summary.json") or _json_soft(p.parent / "capture-summary.json")
    game = caseinfo._game_from_input(str(p))
    capture = {"files": scan["files"], "draw_files": scan["draws"],
               "draw_calls": summary.get("draw_dispatch_call_count") if isinstance(summary, dict) else None,
               "reference": (p.parent / "final-output.png").is_file(),
               "summary": bool(summary), "game_hint": game}
    size = summary.get("raw_unique_payload_bytes") if isinstance(summary, dict) else None
    return {"ok": True, "why": None, "source": "3dmigoto", "input": str(p), "frame_dir": str(p),
            "size": size if isinstance(size, int) else None, "capture": capture, "game_hint": game,
            "suggested_id": _suggest_frame_id(p, game), "start": "ingest-3dmigoto"}


def _look_unity(p):
    if p.is_file():
        magic = _unity_magic(p)
        if not magic:
            return {"ok": False, "input": str(p),
                    "why": "文件头不是 Unity 资源包（UnityFS / UnityWeb / UnityRaw / UnityArchive）"}
        size, files = p.stat().st_size, 1
    elif p.is_dir():
        magic, size, files = None, None, 0
        try:
            entries = [q for q in p.iterdir() if q.is_file()]
        except OSError as e:
            return {"ok": False, "input": str(p), "why": f"读不了这个目录：{e}"}
        for q in entries:
            files += 1
            if magic is None:
                magic = _unity_magic(q)
        if not magic:
            return {"ok": False, "input": str(p),
                    "why": "目录里没有 Unity 资源包头（UnityFS / UnityWeb / UnityRaw / UnityArchive）"}
        try:
            size = sum(q.stat().st_size for q in entries)
        except OSError:
            size = None
    else:
        return {"ok": False, "input": str(p), "why": "Unity 来源需要一个资源包文件或目录"}
    game = caseinfo._game_from_input(str(p))
    capture = {"magic": magic, "files": files, "game_hint": game}
    return {"ok": True, "why": None, "source": "unity-bundle", "input": str(p), "bundle": str(p),
            "size": size, "capture": capture, "game_hint": game,
            "suggested_id": _suggest_unity_id(p, game), "start": "ingest-unity"}


def _head(p, n):
    try:
        with p.open("rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _unity_magic(p):
    """The container magic of a file, or None. These are the headers UnityPy.load recognises for bundles."""
    h = _head(p, 16)
    for m in UNITY_MAGIC:
        if h.startswith(m):
            return m.decode()
    return None


def _unity_magic_in_dir(p):
    try:
        return any(q.is_file() and _unity_magic(q) for q in p.iterdir())
    except OSError:
        return False


def _json_soft(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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


def _suggest_rdc_id(p, cap):
    """A readable, stable directory id: <game>-<frame> when the capture name says both, else a timestamp."""
    game = _short_game(cap.get("game_hint"))
    if game and cap.get("frame"):
        return f"{game}-f{cap['frame']}"
    if cap.get("frame"):
        return f"frame{cap['frame']}"
    return "capture-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _suggest_frame_id(p, game):
    """<game>-frameNN from the enclosing folder, else <game>-<dump timestamp>, else a sanitized folder name."""
    for name in (p.parent.name, p.name):
        m = re.search(r"frame[_-]?(\d+)", name, re.I)
        if m:
            return _clean_id(f"{game or 'fa'}-frame{m.group(1)}")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{6})", p.name)
    if m:
        return _clean_id(f"{game or 'fa'}-{''.join(m.groups())}")
    return _clean_id(f"{game or 'fa'}-{p.name}")


def _suggest_unity_id(p, game):
    stem = p.stem if p.is_file() else p.name
    return _clean_id(f"{game or 'unity'}-{stem}")


def _short_game(raw):
    """The profile id for a capture-name fragment ("WutheringWaves" -> "wuwa")."""
    game = (raw or "").strip().lower()
    game = re.sub(r"[^a-z0-9]+", "-", game).strip("-")
    for short, keys in (("wuwa", ("wuthering", "mingchao")), ("zzz", ("zenless", "zzz")),
                        ("genshin", ("genshin",)), ("hsr", ("starrail", "star-rail")), ("endfield", ("endfield",))):
        if any(k in game for k in keys):
            return short
    return game


def _clean_id(s):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-._")[:64]
    if not s or not s[0].isalnum():
        s = "case-" + s
    return s


def suggest_id(p, source=None):
    """The default directory id for an already-inspected path (see look())."""
    info = look(p, source)
    return info.get("suggested_id") or "capture-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def create(case_id, path=None, source=None, title=None, game=None, rdc=None):
    """Create workspace/<case_id>/case.json for an input. Never overwrites, never copies the capture.

    `rdc=` is kept for the older single-source caller; `path` + `source` is the general form.
    """
    if not CASE_RE.match(case_id or ""):
        raise ValueError(f"任务 id 只能用字母数字和 . _ -，且不能以符号开头：{case_id!r}")
    if case_id in RESERVED:
        raise ValueError(f"{case_id} 是保留名")
    d = WS / case_id
    if d.exists():
        raise ValueError(f"任务 {case_id} 已存在（目录 {d}）")
    if rdc and not source:
        source = "rdc"
    info = look(path or rdc, source)
    if not info["ok"]:
        raise ValueError(info["why"])
    d.mkdir(parents=True)
    case = {"source": info["source"], SOURCE_KEY[info["source"]]: info["input"],
            "created": datetime.datetime.now().isoformat(timespec="seconds")}
    if title:
        case["title"] = str(title).strip()[:80]
    if game:
        case["game"] = game
    # the project's own _json cache keys on mtime; a new directory simply has no entry, so no invalidation needed
    (d / "case.json").write_text(json.dumps(case, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"case": case_id, "case_dir": str(d), "path": str(d), "input": info["input"],
            "source": info["source"], "start": info["start"], "case_json": case,
            "capture": info["capture"], "size": info["size"]}


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
