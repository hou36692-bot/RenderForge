"""Case status layer: what is in workspace/<case>/, how far it got, and what to run next.

A case is a directory under workspace/. `case.json` is the manifest the CLI writes; directories made before this
layer existed have none, so everything here is *inferred from the files on disk* and cross-checked against
case.json when it exists. Nothing is deleted or rewritten by scanning.

Stage vocabulary (fixed, used by status / run / MCP):

  survey     survey/survey.json                     draws listed, scene pass not yet chosen
  export     export/extract.json                    Extract Package (RenderDoc, or 3DMigoto ingest)
  build      build/scene.json                       Scene Package (world matrices + camera)
  identify   draw_identity.json                     source-asset identity for the export
  emit-ue    build/ue_import_result.json            imported into UE
  emit-unity unity_pkg/unity_import_result.json     imported into Unity
  shot       shots/*_ue.png, shots/*_unity.png      rendered screenshots
  metrics    metrics.json                           numeric comparison against the reference frame

`source` is inferred from the extract when case.json is silent: a 3DMigoto ingest writes
`source: 3dmigoto` and a FrameAnalysis path, a Unity-bundle ingest writes its bundle path, everything else
with a `.rdc` is a RenderDoc capture.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"

STAGES = ["survey", "export", "build", "identify", "emit-ue", "emit-unity", "shot", "metrics"]

# games that have a SourceProbe dump-index on this machine, so `identify` is actually runnable (not just possible)
SOURCE_INDEX_GAMES = {"wuwa"}


def _json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _extract_meta(d):
    """Cheap metadata from an Export Package (extract.json), without loading it twice."""
    ex = _json(d / "export" / "extract.json") or {}
    src = ex.get("source")
    api = ex.get("api", "")
    rdc = ex.get("rdc", "")
    if src == "3dmigoto" or "3DMigoto" in str(api):
        source = "3dmigoto"
    elif src == "unity-bundle":
        source = "unity-bundle"
    elif rdc.lower().endswith(".rdc"):
        source = "rdc"
    else:
        source = "unknown"
    return {"source": source, "input": rdc, "api": api,
            "draws": len(ex.get("draws") or []), "meshes": len(ex.get("meshes") or {}),
            "textures": len(ex.get("textures") or {})}


def _newest_mtime(p):
    """Newest file under a path (a directory of screenshots counts as fresh if any image is newer)."""
    p = Path(p)
    if p.is_file():
        return p.stat().st_mtime
    return max((q.stat().st_mtime for q in p.rglob("*") if q.is_file()), default=0.0)


def _game_from_input(inp):
    """Guess the game profile id from a capture path (profiles in toolbox/games.json carry match hints)."""
    low = (inp or "").lower()
    hints = {"wuwa": ("wutheringwave", "wuthering waves", "mingchao"), "zzz": ("zenless", "zzz"),
             "genshin": ("genshin", "yuanhen"), "hsr": ("starrail", "star rail"), "endfield": ("endfield",)}
    for gid, keys in hints.items():
        if any(k in low for k in keys):
            return gid
    return None


def scan(name, case_root=None):
    """One case -> dict (JSON-serialisable): source, stages present, key numbers, inferred next step."""
    d = Path(case_root) if case_root else WS / name
    case = _json(d / "case.json") or {}
    stages = {s: False for s in STAGES}
    detail = {}

    survey = d / "survey" / "survey.json"
    if survey.exists():
        stages["survey"] = True
        s = _json(survey, {}) or {}
        detail["survey"] = {"draws": len(s.get("draws") or [])}
    # older directories kept the survey under extract/
    elif (d / "extract" / "survey.json").exists():
        stages["survey"] = True
        detail["survey"] = {"draws": len((_json(d / "extract" / "survey.json", {}) or {}).get("draws") or []),
                            "dir": "extract"}

    meta = _extract_meta(d)
    if (d / "export" / "extract.json").exists():
        stages["export"] = True
        detail["export"] = dict(meta)
        if not stages["survey"]:
            # an export proves a scene pass was chosen, even when the survey output was moved or cleaned up
            stages["survey"] = True
            detail["survey"] = {"draws": None, "inferred": True}

    scene = None
    if (d / "build" / "scene.json").exists():
        stages["build"] = True
        scene = _json(d / "build" / "scene.json", {}) or {}
        rp = scene.get("report") or {}
        detail["build"] = {"meshes": len(scene.get("meshes") or {}), "materials": len(scene.get("materials") or {}),
                           "instances": len(scene.get("instances") or []), "textures": len(scene.get("textures") or {}),
                           "vp_match": scene.get("vp_match"), "space": scene.get("space", "ue"),
                           "report": rp}

    ident = _json(d / "draw_identity.json")
    if ident:
        stages["identify"] = True
        detail["identify"] = {k: ident.get(k) for k in
                              ("draws", "drawsMatched", "instances", "instancesMatched", "uniqueSourceObjects")}

    ue = _json(d / "build" / "ue_import_result.json")
    if not (ue and ue.get("ok")):
        # a Unity-space scene is converted before import, so its result lands in _scene_ue_space/
        ue = _json(d / "_scene_ue_space" / "ue_import_result.json")
    if ue and ue.get("ok"):
        stages["emit-ue"] = True
        detail["emit-ue"] = ue
    un = _json(d / "unity_pkg" / "unity_import_result.json")
    if un and un.get("ok"):
        stages["emit-unity"] = True
        detail["emit-unity"] = un
    # the engine project's own content is the real evidence: it can be deleted independently of the result json
    ue_dir = ROOT / "ue_project" / "RenderForgeUE" / "Content" / "RenderForge" / name / "Maps" / "Recon.umap"
    unity_dir = ROOT / "unity_project" / "RenderForgeUnity" / "Assets" / "RF" / name / "Recon.unity"
    stale = [w for w in (artifact_state(name, stages, stages["emit-ue"], ue_dir=ue_dir),
                         artifact_state(name, stages, stages["emit-unity"], unity_dir=unity_dir)) if w]
    # a rebuild rewrites build/ from scratch, which also removes the import result recorded there: downstream
    # artifacts older than the scene they were made from no longer describe it
    scene_p = d / "build" / "scene.json"
    if scene_p.exists():
        t = scene_p.stat().st_mtime
        older = [s for s, p in (("emit-unity", d / "unity_pkg" / "unity_import_result.json"),
                                ("shot", d / "shots"))
                 if stages[s] and p.exists() and _newest_mtime(p) < t]
        if older:
            stale.append(f"{', '.join(older)} predate the current build/scene.json; re-run those steps "
                         f"(a rebuild rewrites the scene, so anything emitted from the old one is out of date)")

    shots = sorted(p.name for p in (d / "shots").glob("*.png") if not p.name.startswith("compare")) if (d / "shots").exists() else []
    if not shots and (d / "unity_shot.png").exists():   # zzz-era layout
        shots = ["unity_shot.png"]
    if shots:
        stages["shot"] = True
        detail["shot"] = {"files": shots}
    m = _json(d / "metrics.json")
    if m:
        stages["metrics"] = True
        detail["metrics"] = {k: m.get(k) for k in ("reference", "compared", "ssim", "mae", "coverage_error")}

    # source: case.json is authoritative, else inferred from the export, else from stray files
    source = case.get("source")
    if not source:
        source = "unity-bundle" if case.get("bundle") else meta["source"]
    inp = case.get("rdc") or case.get("frame_dir") or case.get("bundle") or meta.get("input")
    game = case.get("game") or _game_from_input(inp)
    target = case.get("target")

    return {
        "case": name, "path": str(d), "has_case_json": (d / "case.json").exists(),
        "source": source, "game": game, "target": target, "input": inp,
        "input_exists": Path(inp).exists() if inp else None,
        "stages": stages, "detail": detail,
        "next": _next_step(source, stages, game, target, stale),
        "notes": _notes(name, d, case, stages, detail, scene) + stale,
    }


def _next_step(source, stages, game, target, stale=()):
    """The single most useful next action, as {step, cmd, why}. Never claims an optional step is required."""
    c = "{case}"
    if stale:
        # the recorded import no longer exists in the engine, or predates the scene: redo it before anything downstream
        if "predate" in stale[0]:
            first = stale[0].split(",")[0].strip()
            step = first if first in ("emit-ue", "emit-unity", "shot") else "emit-ue"
        else:
            step = "emit-unity" if "Unity" in stale[0] else "emit-ue"
        flag = {"emit-ue": "ue-import", "emit-unity": "unity-import", "shot": "shot"}[step]
        return {"step": step, "cmd": f"python -m renderforge {flag} {c}", "why": stale[0]}
    # every required stage is done: report the best optional step this case can still take, else nothing
    if stages["export"] and not stages["identify"] and source in ("rdc", "3dmigoto") and game in SOURCE_INDEX_GAMES:
        return {"step": "identify", "cmd": f"python -m renderforge identify {c} --index <SourceProbe dump-index dir> --apply",
                "why": "optional: name meshes after the game's own assets (needs a source index for this game)"}
    if stages["export"] and stages["shot"] and not stages["metrics"]:
        return {"step": "metrics", "cmd": f"python -m renderforge metrics {c}",
                "why": "optional: no metrics.json: the comparison is still only a side-by-side image"}
    if target != "unity" and stages["emit-ue"] and not stages["emit-unity"]:
        return {"step": "emit-unity", "cmd": f"python -m renderforge unity-import {c}",
                "why": "optional: the same scene has not been emitted to Unity"}
    return {"step": None, "cmd": None, "why": "every stage this case can run without new input data is done"}
    if not stages["export"] and source == "rdc":
        if not stages["survey"]:
            return {"step": "survey", "cmd": f"python -m renderforge survey {c} --rdc <file.rdc>",
                    "why": "no survey/survey.json: list the draws and find the scene pass"}
        return {"step": "export", "cmd": f"python -m renderforge export {c}",
                "why": "the case has no Extract Package (export/extract.json)"}
    if not stages["build"]:
        if source == "3dmigoto":
            return {"step": "build-model", "cmd": f"python -m renderforge build-model {c} --game {game or '<game>'}",
                    "why": "no build/scene.json: place the model-space meshes"}
        if source == "unity-bundle":
            return {"step": "ingest-unity", "cmd": f"python -m renderforge ingest-unity {c} --bundle <file>",
                    "why": "no build/scene.json: read the Unity bundle into a Scene Package"}
        return {"step": "build", "cmd": f"python -m renderforge build {c}",
                "why": "no build/scene.json: solve world matrices and the camera"}
    if not (stages["emit-ue"] or stages["emit-unity"]):
        tgt = target or "unreal"
        return {"step": f"emit-{'ue' if tgt == 'unreal' else 'unity'}",
                "cmd": f"python -m renderforge {'ue-import' if tgt == 'unreal' else 'unity-import'} {c}",
                "why": "the Scene Package has not been emitted to an engine yet"}
    if not (stages["shot"] or stages["emit-unity"]):
        return {"step": "shot", "cmd": f"python -m renderforge shot {c}",
                "why": "imported, but no screenshot exists"}
    if not stages["metrics"] and stages["export"] and stages["shot"]:
        return {"step": "metrics", "cmd": f"python -m renderforge metrics {c}",
                "why": "no metrics.json: the comparison is still only a side-by-side image"}
    return {"step": None, "cmd": None, "why": "every stage this case can run without new input data is done"}


def artifact_state(name, stages, emit_ok, ue_dir=None, unity_dir=None):
    """A case.json result is evidence, not proof: the engine content can be deleted or renamed independently.
    Returns a warning string when the recorded import no longer has a scene on disk."""
    if not emit_ok:
        return None
    if ue_dir is not None and not ue_dir.exists():
        return ("case.json records a successful UE import, but its map is gone from the UE project "
                "(Content/RenderForge/<case>/Maps/Recon.umap); re-run ue-import before shot")
    if unity_dir is not None and not unity_dir.exists():
        return ("case.json records a successful Unity import, but its scene is gone from the Unity project "
                "(Assets/RF/<case>/Recon.unity); re-run unity-import")
    return None


def _notes(name, d, case, stages, detail, scene):
    out = []
    if not (d / "case.json").exists():
        out.append("no case.json (legacy directory): source/game were inferred from files; re-run the stage that "
                   "writes it, or leave it as is")
    if d.name == "_scene_ue_space" or name.startswith("_"):
        out.append("scratch directory, not a case")
    if scene is not None:
        rp = scene.get("report") or {}
        skipped = (rp.get("skipped") or 0) + (rp.get("extra_skipped") or 0)
        if skipped:
            out.append(f"build skipped {skipped} draw(s) (billboards / unfittable / no post-VS data)")
        if (scene.get("vp_match") or 0) < 0.8 and scene.get("vp"):
            out.append(f"VP fit is weak ({scene.get('vp_match'):.2f})")
        if rp.get("baked"):
            out.append(f"{rp['baked']} draw(s) baked from clip space (non-rigid); geometry is frozen at the captured pose")
    if detail.get("identify"):
        i = detail["identify"]
        if i.get("instances"):
            out.append(f"source identity covers {i.get('instancesMatched')}/{i.get('instances')} instances")
    return out


def scan_all():
    """Every directory under workspace/ that looks like a case (has any stage artifact or a case.json).

    Names starting with `_` are RenderForge's own scratch (`_jobs`, `_logs`, `_selftest`) or an agent's probe
    directory, never a case; listing them would make `status` and the MCP surface report work that is not a
    reconstruction."""
    cases = []
    for p in sorted(WS.iterdir()):
        if not p.is_dir() or p.name.startswith((".", "_")):
            continue
        if not (p / "case.json").exists() and not any((p / x).exists() for x in
                                                      ("export", "build", "survey", "unity_pkg", "shots")):
            continue
        cases.append(scan(p.name))
    return cases


def summary(cases):
    """Human-readable table + a compact JSON form for machines."""
    rows = []
    for c in cases:
        st = c["stages"]
        done = [s for s in STAGES if st[s]]
        rows.append({"case": c["case"], "source": c["source"], "game": c["game"],
                     "stages": done, "next": (c["next"] or {}).get("step"),
                     "draws": (c["detail"].get("export") or {}).get("draws"),
                     "meshes": (c["detail"].get("build") or {}).get("meshes"),
                     "instances": (c["detail"].get("build") or {}).get("instances"),
                     "identity": c["detail"].get("identify")})
    return rows


def format_table(rows):
    w = max(len(r["case"]) for r in rows) if rows else 4
    out = [f"{'case'.ljust(w)}  {'source':10} {'game':9} {'stages':46} {'draws':>6} {'inst':>7}  next"]
    for r in rows:
        stages = " ".join(("+" if s in r["stages"] else "-") + s for s in STAGES)
        inst = r["instances"] if r["instances"] is not None else ""
        out.append(f"{r['case'].ljust(w)}  {str(r['source'])[:10]:10} {str(r['game'] or '-'):9} {stages:46} "
                   f"{r['draws'] if r['draws'] is not None else '':>6} {inst:>7}  {r['next'] or 'done'}")
    return out


def backfill(write=False):
    """Write case.json for legacy directories from what the files prove; never invents a path that is not there."""
    made = []
    for p in sorted(WS.iterdir()):
        if not p.is_dir() or (p / "case.json").exists() or p.name.startswith((".", "_")):
            continue
        c = scan(p.name)
        if not c["stages"]["export"] and not c["stages"]["build"]:
            continue
        case = {}
        if c["source"] in ("rdc", "3dmigoto") and c["input"]:
            case["source"] = c["source"]
            case["rdc" if c["source"] == "rdc" else "frame_dir"] = c["input"]
        if c["game"]:
            case["game"] = c["game"]
        case["inferred"] = True
        if write:
            (p / "case.json").write_text(json.dumps(case, indent=1, ensure_ascii=False), encoding="utf-8")
        made.append((p.name, case))
    return made
