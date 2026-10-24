"""Step graph for a case: what each stage does, in which order, and what it leaves on disk.

A *step* is one named action with a declared purpose; the order depends on the case's source (a RenderDoc capture
starts with survey, a Unity bundle starts with ingest-unity, a 3DMigoto dump starts with build-model). Each step
records its outcome into `case.json` -> `steps.<name>` so the next session can tell what ran, when, and what it
produced without re-deriving it from files.

Resumability is keyed on the artifacts a step writes (`writes`): `run` skips a step whose artifacts already exist
unless the caller asks for it, so `run --from build` after an export failure is a cheap continuation, not a redo.
"""
import datetime
import json
import shutil
from pathlib import Path

from . import caseinfo

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"


def _case_dir(case):
    return WS / case


def load_case(case):
    return caseinfo._json(_case_dir(case) / "case.json", {}) or {}


def ensure_case(case):
    """Legacy directories have no case.json. Before a step writes one, seed it from what the files prove, so the
    capture path (and therefore a later `run --from survey`) is not lost."""
    if (_case_dir(case) / "case.json").exists():
        return
    info = caseinfo.scan(case)
    seed = {}
    if info["source"] in ("rdc", "3dmigoto") and info["input"]:
        seed["source"] = info["source"]
        seed["rdc" if info["source"] == "rdc" else "frame_dir"] = info["input"]
    if info["source"] == "unity-bundle":
        seed["source"] = "unity-bundle"
    if info["game"]:
        seed["game"] = info["game"]
    if seed:
        seed["inferred"] = True
        save_case(case, {**seed, **(load_case(case) or {})})


def save_case(case, data):
    p = _case_dir(case) / "case.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _ok(case, step, **detail):
    ensure_case(case)
    c = load_case(case)
    steps = c.setdefault("steps", {})
    prev = steps.get(step) or {}
    steps[step] = {"ok": True, "when": datetime.datetime.now().isoformat(timespec="seconds"),
                   "run": int(prev.get("run", 0)) + 1, **detail}
    save_case(case, c)


def _fail(case, step, err):
    ensure_case(case)
    c = load_case(case)
    c.setdefault("steps", {})[step] = {"ok": False, "when": datetime.datetime.now().isoformat(timespec="seconds"),
                                       "error": str(err)[:2000]}
    save_case(case, c)


# ---------------------------------------------------------------- steps

def step_survey(case, rdc=None, **kw):
    from . import rd_run
    c = load_case(case)
    if rdc:
        c["rdc"] = str(Path(rdc).resolve())
        save_case(case, c)
    rdc = c.get("rdc") or kw.get("input")
    if not rdc:
        raise ValueError("survey needs the capture path: --rdc <file.rdc>")
    out = _case_dir(case) / "survey"
    rc = rd_run.run("rf_extract.py", {"mode": "survey", "rdc": rdc, "out": str(out)})
    from .cli import scene_passes
    survey = caseinfo._json(out / "survey.json") or {}
    groups = scene_passes(out / "survey.json")
    _ok(case, "survey", rc=rc, draws=len(survey.get("draws") or []), candidates=len(groups))
    return {"rc": rc, "candidates": groups[:6]}


def step_export(case, range=None, rt_count=None, no_instanced_index=False, eids=None, out_name="export", **kw):
    from . import rd_run
    from .cli import scene_passes
    c = load_case(case)
    out = _case_dir(case) / out_name
    job = {"mode": "export", "rdc": c.get("rdc"), "out": str(out)}
    if range:
        lo, hi = range.split("-") if isinstance(range, str) else range
        job["range"] = [int(lo), int(hi)]
    elif eids:
        job["eids"] = list(eids)
    else:
        sp = _case_dir(case) / "survey" / "survey.json"
        if not sp.exists():
            raise ValueError("export needs survey/survey.json (run survey) or an explicit --range")
        groups = scene_passes(sp)
        g = next((x for x in groups if rt_count is None or x["rt_count"] == rt_count), groups[0])
        job["eids"] = g["eids"]
        c["scene_pass"] = {"rt_count": g["rt_count"], "size": g["size"], "draws": g["n"]}
        save_case(case, c)
    if no_instanced_index:
        job["no_instanced_index"] = True
    if out.exists():
        shutil.rmtree(out)
    rc = rd_run.run("rf_extract.py", job)
    ex = caseinfo._json(out / "extract.json") or {}
    _ok(case, "export", rc=rc, draws=len(ex.get("draws") or []), meshes=len(ex.get("meshes") or {}),
        pass_rt_count=(c.get("scene_pass") or {}).get("rt_count"), dir=out_name)
    return {"rc": rc, "out": str(out), "draws": len(ex.get("draws") or []), "meshes": len(ex.get("meshes") or {})}


def step_ingest_3dmigoto(case, frame_dir=None, game=None, **kw):
    from .ingest_3dmigoto import ingest
    c = load_case(case)
    frame_dir = frame_dir or c.get("frame_dir")
    if not frame_dir:
        raise ValueError("ingest-3dmigoto needs --frame-dir <FrameAnalysis-...>")
    c.update({"source": "3dmigoto", "frame_dir": str(Path(frame_dir).resolve())})
    if game:
        c["game"] = game
    save_case(case, c)
    ref = Path(frame_dir).resolve().parent / "final-output.png"
    r = ingest(frame_dir, _case_dir(case) / "export", reference_png=ref)
    _ok(case, "ingest-3dmigoto", **(r if isinstance(r, dict) else {"result": str(r)}))
    return r


def step_ingest_unity(case, bundle, **kw):
    from .ingest_unity import ingest_bundle
    c = load_case(case)
    c.update({"source": "unity-bundle", "bundle": str(Path(bundle).resolve())})
    save_case(case, c)
    sc = ingest_bundle(bundle, _case_dir(case) / "build")
    _ok(case, "ingest-unity", meshes=len(sc["meshes"]), materials=len(sc["materials"]),
        textures=len(sc["textures"]), instances=len(sc["instances"]))
    return {"meshes": len(sc["meshes"]), "materials": len(sc["materials"]), "textures": len(sc["textures"]),
            "instances": len(sc["instances"]), "report": sc.get("report")}


def step_build(case, rt_count=None, extra_export=None, extra_pass="extra", **kw):
    from .build import build
    c = load_case(case)
    rtc = rt_count or (c.get("scene_pass") or {}).get("rt_count")
    filt = (lambda d: len(d["rts"]) == rtc) if rtc else None
    # a rebuild must reproduce the options the case was built with, or it silently drops a merged pass
    if extra_export is None:
        extra_export = (c.get("build") or {}).get("extra_export")
    sc = build(_case_dir(case) / "export", _case_dir(case) / "build", filt,
               extra_export=extra_export, extra_pass=extra_pass)
    c = load_case(case)
    c["build"] = {"extra_export": extra_export, "extra_pass": extra_pass}
    save_case(case, c)
    _ok(case, "build", vp=sc.get("vp"), vp_match=round(sc.get("vp_match") or 0.0, 4),
        meshes=len(sc["meshes"]), instances=len(sc["instances"]), materials=len(sc["materials"]),
        report=sc.get("report"), extra_export=extra_export)
    return {"vp": sc.get("vp"), "vp_match": sc.get("vp_match"), "meshes": len(sc["meshes"]),
            "materials": len(sc["materials"]), "textures": len(sc["textures"]), "instances": len(sc["instances"]),
            "report": sc.get("report")}


def step_build_model(case, game=None, max_extent=3.0, min_tris=100, **kw):
    import numpy as np
    from .build import build_model_space
    from .solve import Extract
    c = load_case(case)
    games = caseinfo._json(ROOT / "toolbox" / "games.json", {}).get("games", {})
    g = games.get(game or c.get("game", ""), {})
    ex = Extract(_case_dir(case) / "export")

    def pick(d):
        m = ex.meshes[d["mesh"]]
        names = {x["name"]: i for i, x in enumerate(m["attrs"])}
        if "POSITION0" not in names:
            return False
        p = ex.mesh_attr(d["mesh"], names["POSITION0"])[:, :3]
        return float(np.ptp(p, axis=0).max()) < max_extent and m["indices"] // 3 >= min_tris

    sc = build_model_space(_case_dir(case) / "export", _case_dir(case) / "build", g.get("source_space", "unity"),
                           pick=pick, basis=g.get("model_basis"))
    _ok(case, "build-model", game=game or c.get("game"), meshes=len(sc["meshes"]),
        instances=len(sc["instances"]), report=sc.get("report"))
    return {"meshes": len(sc["meshes"]), "materials": len(sc["materials"]), "instances": len(sc["instances"]),
            "report": sc.get("report")}


def step_ue_import(case, max_instances=None, emissive=False, **kw):
    from .ue_run import run
    from .space import convert_scene
    scene_dir = convert_scene(_case_dir(case) / "build", _case_dir(case) / "_scene_ue_space", "ue")
    extra = {"RF_SCENE": str(Path(scene_dir) / "scene.json"), "RF_CASE": _ue_name(case)}
    if max_instances:
        extra["RF_MAX_INSTANCES"] = str(max_instances)
    if emissive:
        extra["RF_EMISSIVE"] = "1"
    rc, logp = run("import_scene.py", extra, log=f"ue_import_{case}.log")
    res = caseinfo._json(Path(scene_dir) / "ue_import_result.json", {}) or {}
    if not res.get("ok"):
        _fail(case, "emit-ue", f"rc={rc} log={logp}")
        raise RuntimeError(f"UE import failed (rc={rc}); see {logp}")
    _ok(case, "emit-ue", **res)
    return res


def step_unity_import(case, **kw):
    from .emit_unity import emit
    from .unity_run import run
    pkg = _case_dir(case) / "unity_pkg"
    emit(_case_dir(case) / "build", pkg, _ue_name(case))
    shots = _case_dir(case) / "shots"
    shots.mkdir(exist_ok=True)
    rc, logp = run("RFImportScene.Run", {"RF_UNITY_PKG": str(pkg),
                                         "RF_UNITY_SHOT": str(shots / f"{case}_unity.png")},
                   log=f"unity_import_{case}.log", graphics=True)
    res = caseinfo._json(pkg / "unity_import_result.json", {}) or {}
    if not res.get("ok"):
        _fail(case, "emit-unity", f"rc={rc} log={logp}")
        raise RuntimeError(f"Unity import failed (rc={rc}); see {logp}")
    _ok(case, "emit-unity", **res)
    return res


def step_shot(case, ticks=900, cmd=None, **kw):
    from .ue_run import run_game_shot
    name = f"{case}_ue"
    c = load_case(case)
    console = cmd or (c.get("shot") or {}).get("console") or f"fov {c.get('hfov', 100)}"
    rc, logp = run_game_shot(_ue_name(case), name, wait_ticks=ticks, console=console)
    src = ROOT / "ue_project" / "RenderForgeUE" / "Saved" / "Screenshots" / "WindowsEditor" / f"{name}.png"
    dst = _case_dir(case) / "shots"
    dst.mkdir(exist_ok=True)
    if not src.exists():
        hint = _ue_hint(logp)
        _fail(case, "shot", f"no screenshot; see {logp}" + (f" | {hint}" if hint else ""))
        raise RuntimeError(f"no screenshot produced; see {logp}" + (f" | {hint}" if hint else ""))
    shutil.copy(src, dst / src.name)
    c["shot"] = {"console": console, "ticks": ticks}   # a re-shot months later must use the same fov
    save_case(case, c)
    _ok(case, "shot", file=src.name, console=console, log=str(logp))
    return {"shot": str(dst / src.name), "console": console}


def step_identify(case, index=None, ids=None, apply=True, **kw):
    from .identify import identify_draws, identify_export, apply_draws
    if not index:
        raise ValueError("identify needs --index <SourceProbe dump-index directory>")
    if ids:
        res = identify_draws(ids, index)
    else:
        res = identify_export(_case_dir(case) / "export", index)
    p = _case_dir(case) / "draw_identity.json"
    p.write_text(json.dumps(res, indent=1), encoding="utf-8")
    applied = None
    sp = _case_dir(case) / "build" / "scene.json"
    if sp.exists() and apply:
        applied = apply_draws(sp, res)
    c = load_case(case)
    c["identify"] = {"index": str(Path(index).resolve())}
    save_case(case, c)
    _ok(case, "identify", index=str(Path(index).resolve()), drawsMatched=res["drawsMatched"], draws=res["draws"],
        instancesMatched=res["instancesMatched"], instances=res["instances"], applied=applied)
    return {"drawsMatched": res["drawsMatched"], "draws": res["draws"], "instancesMatched": res["instancesMatched"],
            "instances": res["instances"], "uniqueSourceObjects": res["uniqueSourceObjects"], "applied": applied}


def step_metrics(case, shot=None, shot_name=None, **kw):
    from .metrics import compare_case
    shot = shot or shot_name
    if shot and shot.endswith(".png") and not Path(shot).exists():
        cand = _case_dir(case) / "shots" / shot       # a bare file name means "under this case's shots/"
        shot = str(cand) if cand.exists() else shot
    if not shot:
        # no shot named: score the most recent screenshot of *this case* (newest engine import wins, so a Unity
        # re-emit is not silently scored against an older UE render)
        recs = [p for p in (_case_dir(case) / "shots").glob("*.png")
                if p.stem in (f"{case}_ue", f"{case}_unity") or p.name == "unity_shot.png"]
        if recs:
            shot = str(max(recs, key=lambda p: p.stat().st_mtime))
    res = compare_case(_case_dir(case), shot_name=shot)
    # both engine shots are worth keeping when they exist: the numbers differ enough that scoring only the one
    # that happens to be newest would hide a regression in the other
    others = _other_shots(case, res.get("compared"))
    if others:
        import json
        p = _case_dir(case) / "metrics.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["also"] = others
        p.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
        res["also"] = others
    _ok(case, "metrics", ssim=res.get("ssim"), mae=res.get("mae"), coverage_error=res.get("coverage_error"),
        shot_flipped=res.get("shot_flipped"), also=list(others))
    return res


# ---------------------------------------------------------------- step graph

def _other_shots(case, primary):
    """Score the case's other engine screenshot too, without leaving its json or ssim map behind."""
    from .metrics import compare_case
    scratch = ROOT / "workspace" / "_jobs" / "_metrics_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    out = {}
    for p in sorted((_case_dir(case) / "shots").glob("*.png")):
        if p.stem not in (f"{case}_ue", f"{case}_unity") or p.name == primary:
            continue
        # a scratch output path: compare_case also writes metrics_ssim.png beside its json, and the primary shot owns
        # that file inside the case
        tmp = scratch / f"{case}_{p.stem}.json"
        try:
            o = compare_case(_case_dir(case), shot_name=str(p), out_json=tmp)
        except (OSError, ValueError):
            continue
        finally:
            tmp.unlink(missing_ok=True)
        out[o["compared"]] = {k: o.get(k) for k in ("ssim", "mae", "rmse", "p95_abs", "coverage_error",
                                                    "mask_iou", "shot_flipped")}
    return out


def _shot_file(case):
    """The screenshot a step produced, if the case.json step record names one that still exists."""
    rec = (load_case(case).get("steps") or {}).get("shot") or {}
    name = rec.get("file")
    return _case_dir(case) / "shots" / name if name and (_case_dir(case) / "shots" / name).exists() else None


def _graph(source):
    """Ordered steps for a source. `writes` decides whether a step is already done."""
    if source == "3dmigoto":
        return [("build-model", step_build_model, ["build/scene.json"])]
    if source == "unity-bundle":
        return [("ingest-unity", step_ingest_unity, ["build/scene.json"])]
    rows = [("survey", step_survey, ["survey/survey.json"]),
            ("export", step_export, ["export/extract.json"]),
            ("build", step_build, ["build/scene.json"])]
    return rows


def _emit_graph(target):
    if target == "unity":
        return [("emit-unity", step_unity_import, ["unity_pkg/unity_import_result.json"]),
                ("metrics", step_metrics, ["metrics.json"])]
    return [("emit-ue", step_ue_import, ["build/ue_import_result.json"]),
            ("shot", step_shot, ["shots"]),
            ("metrics", step_metrics, ["metrics.json"])]


def _functions(case, target=None):
    """(step, function, writes) for this case, in order. Identity sits after build (it annotates scene.json) and
    before emitting; the emit half depends on case.json's target.

    `writes` decides whether a step is done: a file path, or a callable for steps whose artifact name is dynamic
    (the screenshot carries the case name in it)."""
    c = load_case(case)
    source = caseinfo.scan(case)["source"]
    steps = _graph(source)
    if source in ("rdc", "3dmigoto"):
        steps = steps + [("identify", step_identify, ["draw_identity.json"])]
    out = []
    for name, fn, writes in steps + _emit_graph(target or c.get("target") or "unreal"):
        if name == "shot":
            writes = [lambda: _shot_file(case)]
        out.append((name, fn, writes))
    return out


def plan_steps(case):
    """The full step list this case can run with the inputs it already has, with done/pending status."""
    return [{"step": n, "done": _done(case, w), "writes": ["<shot from case.json>" if callable(w[0]) else w[0]]}
            for n, _, w in _functions(case)]


def _done(case, writes):
    """Done = the artifact exists. A file path, or a callable for steps whose artifact name is dynamic -- the shot
    only counts when the case.json shot record names a file that is still on disk (never a stale screenshot)."""
    p = writes[0]
    return bool(p()) if callable(p) else (_case_dir(case) / p).exists()


def run(case, frm=None, to=None, force=False, target=None, **kw):
    """Run the step graph in order. Skips steps whose artifacts exist (unless force) and steps outside [from, to].

    A failing step stops the run and leaves the error in case.json; the returned dict says what ran, what was
    skipped, and why it stopped. Optional steps that need input this machine cannot supply (an identify index that
    was not passed, a bundle path) are skipped with that reason rather than stopping the run.
    """
    steps = _functions(case, target)
    names = [s[0] for s in steps]
    for flag, step in (("--from", frm), ("--to", to)):
        if step and step not in names:
            raise ValueError(f"{flag} '{step}' is not a step of this case; its steps: {', '.join(names)}")
    lo = names.index(frm) if frm else 0
    hi = names.index(to) if to else len(names) - 1
    out = {"case": case, "ran": [], "skipped": [], "stopped": None, "plan": [{"step": n, "done": _done(case, w)}
                                                                          for n, _, w in steps]}
    for i, (name, fn, writes) in enumerate(steps):
        if i < lo or i > hi:
            out["skipped"].append({"step": name, "why": "outside --from/--to"})
            continue
        done = _done(case, writes)
        if done and not force:
            out["skipped"].append({"step": name, "why": "artifacts exist"})
            continue
        missing = _inputs(case, name, kw)
        if missing:
            out["skipped"].append({"step": name, "why": f"needs {missing}; pass it to run this step"})
            continue
        try:
            res = fn(case, **kw)
            out["ran"].append({"step": name, "result": _brief(res)})
        except Exception as e:  # noqa: BLE001 - the step already recorded its own failure
            out["stopped"] = {"step": name, "error": str(e)}
            break
    return out


def _inputs(case, name, kw):
    """What a step cannot reconstruct from files (a source index, a capture path). Empty means 'runnable'."""
    c = load_case(case)
    if name == "identify":
        need = kw.get("index") or (c.get("identify") or {}).get("index")
        return "" if need else "--index <SourceProbe dump-index dir>"
    if name == "survey":
        return "" if (kw.get("rdc") or c.get("rdc")) else "--rdc <file.rdc>"
    if name in ("export", "build", "ue-import", "shot", "metrics", "emit-ue", "emit-unity"):
        return ""
    if name == "ingest-3dmigoto":
        return "" if (kw.get("frame_dir") or c.get("frame_dir")) else "--frame-dir <FrameAnalysis-...>"
    if name == "ingest-unity":
        return "" if (kw.get("bundle") or c.get("bundle")) else "--bundle <Unity bundle>"
    return ""


def _ue_hint(logp):
    """The one line of a UE log that usually explains a failed shot (missing map, bad commandlet)."""
    try:
        t = Path(logp).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(t.splitlines()):
        if "could not be found" in line or "Failed to enter" in line:
            return line.split("] ", 1)[-1][:220]
    return ""


def _brief(res):
    """Keep run output readable: numbers and paths, not megabytes of mesh records."""
    if isinstance(res, dict):
        return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else
                    (f"<{type(v).__name__} len={len(v)}>" if isinstance(v, (list, dict)) else str(v)))
                for k, v in list(res.items())[:12]}
    return str(res)[:300]


def _ue_name(case):
    import re
    return re.sub(r"[^A-Za-z0-9_]", "_", case)
