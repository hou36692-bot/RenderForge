"""RenderForge command line: survey -> export -> build -> ue-import -> shot, per case.

  python -m renderforge survey  <case> --rdc <file.rdc>
  python -m renderforge export  <case> [--range A-B | --rt-count N]
  python -m renderforge build   <case> [--rt-count N]
  python -m renderforge ue-import <case> [--max-instances N]
  python -m renderforge shot    <case> [--cmd "viewmode ..."]
  python -m renderforge run     <case> --rdc <file.rdc>        (everything, auto-picks the main scene pass)

Cases live in workspace/<case>/ ; original .rdc files are only read, never copied or modified.
"""
import argparse
import collections
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"


def case_dir(name):
    return WS / name


def load_case(name):
    p = case_dir(name) / "case.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_case(name, data):
    p = case_dir(name) / "case.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def ue_name(case):
    return re.sub(r"[^A-Za-z0-9_]", "_", case)


def cmd_survey(a):
    from . import rd_run
    c = load_case(a.case)
    if a.rdc:
        c["rdc"] = str(Path(a.rdc).resolve())
    save_case(a.case, c)
    out = case_dir(a.case) / "survey"
    rc = rd_run.run("rf_extract.py", {"mode": "survey", "rdc": c["rdc"], "out": str(out)})
    groups = scene_passes(out / "survey.json")
    print(f"survey done (rc={rc}); candidate scene passes:")
    for i, g in enumerate(groups[:6]):
        print(f"  [{i}] RT x{g['rt_count']} {g['rt_formats'][:3]} {g['size']} draws={g['n']} eids {g['first']}..{g['last']} idx={g['indices']}")
    return 0


def scene_passes(survey_path):
    s = json.loads(Path(survey_path).read_text(encoding="utf-8"))
    groups = collections.OrderedDict()
    for d in s["draws"]:
        if not d["rts"] or not d["rtSize"]:
            continue
        key = (tuple(d["rtFormats"]), tuple(d["rtSize"]), d["depth"])
        groups.setdefault(key, []).append(d)
    out = []
    for (fmts, size, depth), v in groups.items():
        out.append({"rt_count": len(fmts), "rt_formats": list(fmts), "size": list(size), "n": len(v),
                    "first": v[0]["eid"], "last": v[-1]["eid"], "indices": sum(x["numIndices"] for x in v),
                    "eids": [x["eid"] for x in v]})
    # a G-buffer-like pass: several render targets at (near) full resolution, with many draws
    out.sort(key=lambda g: (g["rt_count"] >= 3 and g["size"][0] >= 1000, g["indices"]), reverse=True)
    return out


def cmd_export(a):
    from . import rd_run
    c = load_case(a.case)
    out = case_dir(a.case) / "export"
    job = {"mode": "export", "rdc": c["rdc"], "out": str(out)}
    if a.range:
        lo, hi = a.range.split("-")
        job["range"] = [int(lo), int(hi)]
    else:
        groups = scene_passes(case_dir(a.case) / "survey" / "survey.json")
        g = next((x for x in groups if a.rt_count is None or x["rt_count"] == a.rt_count), groups[0])
        job["eids"] = g["eids"]
        c["scene_pass"] = {"rt_count": g["rt_count"], "size": g["size"], "draws": g["n"]}
        save_case(a.case, c)
        print(f"auto-selected pass: RT x{g['rt_count']} {g['size']} draws={g['n']}")
    if out.exists():
        shutil.rmtree(out)
    rc = rd_run.run("rf_extract.py", job)
    print("export rc", rc, "->", out)
    return 0


def cmd_build(a):
    from .build import build
    c = load_case(a.case)
    rtc = a.rt_count or (c.get("scene_pass") or {}).get("rt_count")
    filt = (lambda d: len(d["rts"]) == rtc) if rtc else None
    sc = build(case_dir(a.case) / "export", case_dir(a.case) / "build", filt)
    print("VP:", sc["vp"], "match", round(sc["vp_match"], 2))
    print("report:", sc["report"], "| meshes", len(sc["meshes"]), "materials", len(sc["materials"]), "textures", len(sc["textures"]))
    return 0


def cmd_ue_import(a):
    from .ue_run import run
    extra = {"RF_SCENE": str(case_dir(a.case) / "build" / "scene.json"), "RF_CASE": ue_name(a.case)}
    if a.max_instances:
        extra["RF_MAX_INSTANCES"] = str(a.max_instances)
    if a.emissive:
        extra["RF_EMISSIVE"] = "1"
    rc, logp = run("import_scene.py", extra, log=f"ue_import_{a.case}.log")
    res = json.loads((case_dir(a.case) / "build" / "ue_import_result.json").read_text())
    print("ue import rc", rc, res)
    return 0 if res.get("ok") else 1


def cmd_shot(a):
    from .ue_run import run_game_shot
    name = f"{a.case}_ue"
    rc, logp = run_game_shot(ue_name(a.case), name, wait_ticks=a.ticks, console=a.cmd or f"fov {load_case(a.case).get('hfov', 100)}")
    src = ROOT / "ue_project" / "RenderForgeUE" / "Saved" / "Screenshots" / "WindowsEditor" / f"{name}.png"
    dst = case_dir(a.case) / "shots"
    dst.mkdir(exist_ok=True)
    if src.exists():
        shutil.copy(src, dst / src.name)
        print("shot ->", dst / src.name)
    else:
        print("shot failed, see", logp)
    return 0


def cmd_compare(a):
    from PIL import Image
    d = case_dir(a.case)
    ref = d / "export" / "reference_backbuffer.png"
    ue = d / "shots" / f"{a.case}_ue.png"
    if not (ref.exists() and ue.exists()):
        print("need", ref, "and", ue)
        return 1
    A, B = Image.open(ref).convert("RGB"), Image.open(ue).convert("RGB")
    h = 720
    A = A.resize((int(A.width * h / A.height), h))
    B = B.resize((int(B.width * h / B.height), h))
    out = Image.new("RGB", (A.width + B.width, h))
    out.paste(A, (0, 0))
    out.paste(B, (A.width, 0))
    dst = d / "shots" / "compare.png"
    out.save(dst)
    print("compare ->", dst, "(left: RenderDoc frame, right: UE)")
    return 0


def cmd_run(a):
    a.range, a.rt_count, a.max_instances, a.cmd, a.ticks = None, None, None, None, 1200
    for fn in (cmd_survey, cmd_export, cmd_build, cmd_ue_import, cmd_shot, cmd_compare):
        if fn(a):
            return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="renderforge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("survey", cmd_survey), ("export", cmd_export), ("build", cmd_build), ("ue-import", cmd_ue_import),
                     ("shot", cmd_shot), ("compare", cmd_compare), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("case")
        p.add_argument("--rdc")
        p.add_argument("--range")
        p.add_argument("--rt-count", type=int)
        p.add_argument("--max-instances", type=int)
        p.add_argument("--emissive", action="store_true", help="albedo preview: base color also drives emissive")
        p.add_argument("--cmd")
        p.add_argument("--ticks", type=int, default=900)
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
