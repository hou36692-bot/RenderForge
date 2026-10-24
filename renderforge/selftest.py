"""Offline regression fixture for the pipeline's numerically established facts.

Every check here is already known to hold on data committed to this workspace, so a failure means a change in one
place silently broke another: the space transform stopped being invertible, the Unity bundle no longer matches the
engine's own world matrices, a Scene Package stopped being internally consistent, the metric stopped agreeing with
itself, case scanning or the toolbox data files drifted. Nothing here needs a GPU, RenderDoc, UE or Unity, and no
real case directory is rewritten (scratch output goes to workspace/_selftest/).

`run` never raises on a failing check: it collects checks into passed/failed/skipped and returns ok = not failed.
A missing optional fixture is skipped, not failed; a skipped entry carries ok=False but is not counted as a failure.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"
SCRATCH = WS / "_selftest"

_VERT_TOL = 1e-5      # float32 round-trip of a metre-scale vertex
_MAT_TOL = 1e-6       # 4x4 entries after an exact inverse permutation
_TRANS_TOL = 1e-5     # ground-truth translations are printed with ~7 significant digits


class Skip(Exception):
    """Fixture absent: reported as skipped, never as a failure."""


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise Skip(f"missing fixture: {path}") from e


def _f(x):
    """Short stable number for detail strings."""
    return f"{float(x):.3g}"


def _issues_text(issues, facts):
    if issues:
        return "; ".join(facts + ["FAIL: " + i for i in issues])
    return "; ".join(facts)


def _check(name, fn):
    """Run one check; an exception becomes a failed check so the remaining checks still run."""
    try:
        issues, facts = fn()
    except Skip as e:
        return {"name": name, "ok": False, "detail": "skipped: " + str(e)}, "skip"
    except Exception as e:  # noqa: BLE001 - a crashing check must not hide the others
        return {"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}"}, "fail"
    ok = not issues
    return {"name": name, "ok": ok, "detail": _issues_text(issues, facts)}, ("pass" if ok else "fail")


def _space_roundtrip():
    """unity -> ue -> unity must return the exact vertices and world matrices (space.py is an inverse pair)."""
    from .space import convert_scene

    src = WS / "unity_synth" / "build"
    have = _read_json(src / "scene.json")
    if have.get("space") != "unity":
        return ["fixture scene.json is not in unity space"], []
    SCRATCH.mkdir(parents=True, exist_ok=True)
    ue_dir = convert_scene(src, SCRATCH / "space_ue", "ue")
    back_dir = convert_scene(ue_dir, SCRATCH / "space_back", "unity")
    back = _read_json(back_dir / "scene.json")

    issues = []
    if back.get("space") != "unity":
        issues.append(f"round-trip space is {back.get('space')!r}")
    if set(back["meshes"]) != set(have["meshes"]):
        issues.append("mesh set changed across the round-trip")
    if len(back["instances"]) != len(have["instances"]):
        issues.append(f"instance count {len(back['instances'])} != {len(have['instances'])}")

    vmax = 0.0
    for mid, m in have["meshes"].items():
        if mid not in back["meshes"]:
            continue
        a = np.fromfile(src / m["dir"] / "pos.f32", dtype="<f4").reshape(-1, 3)
        b = np.fromfile(back_dir / back["meshes"][mid]["dir"] / "pos.f32", dtype="<f4").reshape(-1, 3)
        if a.shape != b.shape:
            issues.append(f"{mid}: vertex count {len(b)} != {len(a)}")
            continue
        vmax = max(vmax, float(np.abs(a.astype(np.float64) - b.astype(np.float64)).max()))

    mmax = 0.0
    for i0, i1 in zip(have["instances"], back["instances"]):
        W0 = np.asarray(i0["matrix"], dtype=np.float64).reshape(4, 4)
        W1 = np.asarray(i1["matrix"], dtype=np.float64).reshape(4, 4)
        mmax = max(mmax, float(np.abs(W0 - W1).max()))

    if vmax >= _VERT_TOL:
        issues.append(f"max vertex error {_f(vmax)} >= {_f(_VERT_TOL)}")
    if mmax >= _MAT_TOL:
        issues.append(f"max matrix error {_f(mmax)} >= {_f(_MAT_TOL)}")
    facts = [f"verts max err {_f(vmax)}", f"matrices max err {_f(mmax)}",
             f"{len(have['meshes'])} meshes / {len(have['instances'])} instances"]
    return issues, facts


def _unity_ingest():
    """The Unity reader must reproduce the engine's own localToWorldMatrix (Bundles/expected.json is ground truth)."""
    scene = _read_json(WS / "unity_synth" / "build" / "scene.json")
    raw = _read_json(ROOT / "unity_project" / "RenderForgeUnity" / "Bundles" / "expected.json")
    # expected.json holds Unity's column-vector matrices row-major; the package uses the row-vector convention, so
    # the same numbers are the transpose: expected[:3, 3] is the world translation.
    exp = {name: np.asarray(v, dtype=np.float64).reshape(4, 4) for name, v in raw.items()}

    issues = []
    inst = scene["instances"]
    if len(inst) != 3:
        issues.append(f"{len(inst)} placed instances, expected 3")
    wants = [(1.0, 2.0, 3.0), (3.801525, 2.584890, -1.140956), (5.0, 1.0, 0.0)]
    trans = [np.asarray(i["matrix"], dtype=np.float64).reshape(4, 4)[3, :3] for i in inst]

    # Specific positions are the verified fixture record; matching expected.json by nearest translation keeps the
    # ground-truth comparison meaningful instead of pinning it to one instance order.
    for w in wants:
        d = min(float(np.linalg.norm(t - np.asarray(w))) for t in trans) if trans else float("inf")
        if d >= _TRANS_TOL:
            issues.append(f"no instance at {w} (nearest {_f(d)} away)")

    worst = 0.0
    worst_full = 0.0
    matched, unmatched = [], []
    for i in inst:
        W = np.asarray(i["matrix"], dtype=np.float64).reshape(4, 4)
        name, E = min(exp.items(), key=lambda kv: float(np.linalg.norm(kv[1][:3, 3] - W[3, :3])))
        d = float(np.linalg.norm(E[:3, 3] - W[3, :3]))
        # A far nearest neighbour is not a correspondence: the bundle's ground truth does not cover every placed
        # object (RF_Prefab, the instantiated prefab root, has no expected.json entry).
        if d >= _TRANS_TOL:
            unmatched.append(i.get("name") or str(i.get("eid")))
            continue
        matched.append(name)
        worst = max(worst, d)
        worst_full = max(worst_full, float(np.abs(E.T - W).max()))
    # RF_Parent is a container transform (no MeshFilter) and so has no instance; the two children must be present.
    for want in ("RF_ChildCube", "RF_ChildWedge"):
        if want not in matched:
            issues.append(f"no instance matched expected {want}")
    if worst >= _TRANS_TOL:
        issues.append(f"translation error {_f(worst)} >= {_f(_TRANS_TOL)}")
    if worst_full >= _MAT_TOL:
        issues.append(f"full matrix error {_f(worst_full)} >= {_f(_MAT_TOL)}")

    for mid, verts, tris in (("u0000", 24, 12), ("u0001", 5, 5)):
        m = scene["meshes"].get(mid)
        if m is None:
            issues.append(f"mesh {mid} missing")
        elif m["verts"] != verts or m["tris"] != tris:
            issues.append(f"{mid}: {m['verts']} verts / {m['tris']} tris, expected {verts}/{tris}")

    facts = [f"{len(inst)} instances", f"matched {len(matched)} to expected.json",
             f"no ground truth for {unmatched}" if unmatched else "all covered by expected.json",
             f"max translation err {_f(worst)}", f"max matrix err {_f(worst_full)}", "u0000 24/12, u0001 5/5"]
    return issues, facts


def _wuwa_integrity():
    """The real bridge Scene Package must stay internally consistent (read-only; it is a verified record)."""
    case = WS / "wuwa_bridge"
    sc = _read_json(case / "build" / "scene.json")
    meshes, inst = sc["meshes"], sc["instances"]
    report = sc.get("report") or {}

    issues = []
    if not (sc.get("vp_match") or 0) > 0.9:
        issues.append(f"vp_match {_f(sc.get('vp_match') or 0)} <= 0.9")
    if len(inst) <= 19000:
        issues.append(f"{len(inst)} instances, expected > 19000")

    missing_mesh = [i["mesh"] for i in inst if i["mesh"] not in meshes]
    if missing_mesh:
        issues.append(f"{len(missing_mesh)} instances reference unknown mesh ids (e.g. {missing_mesh[:3]})")

    bad = []
    for mid, m in meshes.items():
        d = case / "build" / m["dir"]
        if not d.is_dir():
            bad.append(f"{mid}: no dir")
        elif not (d / "pos.f32").is_file() or not (d / "idx.u32").is_file():
            bad.append(f"{mid}: missing pos/idx")
        else:
            if (d / "pos.f32").stat().st_size != m["verts"] * 12:
                bad.append(f"{mid}: pos bytes != verts*12")
            if (d / "idx.u32").stat().st_size != m["tris"] * 3 * 4:
                bad.append(f"{mid}: idx bytes != tris*12")
    if bad:
        issues.append(f"{len(bad)} inconsistent mesh dirs (e.g. {bad[:3]})")

    s = int(report.get("static") or 0)
    b = int(report.get("baked") or 0)
    n = len(inst)
    if not (0.9 * n <= s + b <= 1.05 * n):
        issues.append(f"report.static+report.baked = {s + b}, not sane against {n} instances")

    facts = [f"vp_match {_f(sc.get('vp_match'))}", f"{len(meshes)} meshes", f"{n} instances",
             f"static {s} + baked {b} = {s + b}", f"skipped {report.get('skipped')}, extra {report.get('extra')}"]
    return issues, facts


def _metrics():
    """metrics.py must agree with itself and detect orientation (lazy import: this file stays usable without it)."""
    try:
        from . import metrics
    except ImportError as e:
        raise Skip(f"renderforge.metrics not importable ({e})") from e
    try:
        from PIL import Image
    except ImportError as e:
        raise Skip(f"PIL not importable ({e})") from e

    case = WS / "wuwa_bridge"
    ref = case / "export" / "reference_backbuffer.png"
    shot = case / "shots" / "wuwa_bridge_ue.png"
    for p in (ref, shot):
        if not p.is_file():
            raise Skip(f"missing fixture: {p}")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    with Image.open(shot) as im:
        a = np.asarray(im.convert("RGB"))
    self_a = SCRATCH / "metrics_self.png"
    self_flip = SCRATCH / "metrics_self_flipped.png"
    Image.fromarray(a).save(self_a)
    Image.fromarray(a[::-1]).save(self_flip)

    real = metrics.compare(ref, shot)
    same = metrics.compare(self_a, self_a)
    flipped = metrics.compare(ref, self_flip)

    issues = []
    ssim = float(real["ssim"])
    if not 0.0 <= ssim <= 1.0:
        issues.append(f"ssim {_f(ssim)} outside [0, 1]")
    if same["ssim"] != 1.0:
        issues.append(f"self-comparison ssim {same['ssim']!r} != 1.0")
    if same["mae"] != 0:
        issues.append(f"self-comparison mae {same['mae']!r} != 0")
    if real["shot_flipped"]:
        issues.append("unflipped shot reported as flipped")
    if not flipped["shot_flipped"]:
        issues.append("vertically flipped copy not detected")

    facts = [f"ssim {_f(ssim)}", f"self ssim {same['ssim']:.1f} mae {same['mae']:.1f}",
             f"flip detected {flipped['shot_flipped']}"]
    return issues, facts


def _case_scan():
    """The case layer must keep finding the verified cases with the right source and stages."""
    try:
        from . import caseinfo
    except ImportError as e:
        raise Skip(f"renderforge.caseinfo not importable ({e})") from e
    if not (WS / "wuwa_bridge").is_dir():
        raise Skip(f"missing fixture: {WS / 'wuwa_bridge'}")

    cases = caseinfo.scan_all()
    by_name = {c.get("case"): c for c in cases} if isinstance(cases, list) else {}
    issues = []
    if not isinstance(cases, list):
        issues.append(f"scan_all returned {type(cases).__name__}, expected list")
    else:
        for c in cases:
            if not isinstance(c, dict) or not {"case", "source", "stages", "next"} <= set(c):
                issues.append(f"case entry missing keys: {str(c)[:80]}")
                break

    w = by_name.get("wuwa_bridge")
    if not isinstance(w, dict):
        issues.append("wuwa_bridge not scanned")
    else:
        if w.get("source") != "rdc":
            issues.append(f"wuwa_bridge source {w.get('source')!r} != 'rdc'")
        for st in ("export", "build", "emit-ue"):
            if not (w.get("stages") or {}).get(st):
                issues.append(f"wuwa_bridge stage {st} not detected")

    z = by_name.get("zzz_frame01")
    if (WS / "zzz_frame01").is_dir():
        if not isinstance(z, dict):
            issues.append("zzz_frame01 not scanned")
        elif z.get("source") != "3dmigoto":
            issues.append(f"zzz_frame01 source {z.get('source')!r} != '3dmigoto'")

    facts = [f"{len(cases)} cases scanned", "wuwa_bridge rdc export/build/emit-ue", "zzz_frame01 3dmigoto"]
    return issues, facts


def _toolbox_data():
    """Registry/games/recipes must cross-reference real tool ids, and docs/TOOLBOX.md must be current."""
    from .toolbox import STATUS_ORDER

    tb = ROOT / "toolbox"
    reg = _read_json(tb / "registry.json")
    games = _read_json(tb / "games.json")["games"]
    recipes = _read_json(tb / "recipes.json")["recipes"]
    ids = {t["id"] for t in reg["tools"]}

    issues = []
    for t in reg["tools"]:
        if t.get("status") not in STATUS_ORDER:
            issues.append(f"tool {t.get('id')} has status {t.get('status')!r}")

    dangling = []
    for rid, r in recipes.items():
        for step in r.get("steps") or []:
            for tool in step.get("tools") or []:
                if tool not in ids:
                    dangling.append(f"{rid}:{tool}")
    for gid, g in games.items():
        for field in ("capture", "source", "frame_extractors"):
            for tool in g.get(field) or []:
                if tool not in ids:
                    dangling.append(f"{gid}:{field}:{tool}")
    if dangling:
        issues.append(f"unknown tool ids: {dangling[:5]}")

    docs = ROOT / "docs" / "TOOLBOX.md"
    data_mtimes = [(tb / n).stat().st_mtime for n in ("registry.json", "games.json", "recipes.json")]
    if not docs.is_file():
        issues.append("docs/TOOLBOX.md missing; run python -m renderforge docs")
    elif docs.stat().st_mtime < max(data_mtimes) - 1e-6:
        issues.append("docs/TOOLBOX.md is older than toolbox data; run python -m renderforge docs")

    facts = [f"{len(ids)} tools", f"{len(games)} games", f"{len(recipes)} recipes",
             f"docs {_f(docs.stat().st_mtime - max(data_mtimes))}s newer than data" if docs.is_file() else "no docs"]
    return issues, facts


CHECKS = [
    ("space-roundtrip", _space_roundtrip),
    ("unity-ingest", _unity_ingest),
    ("wuwa-integrity", _wuwa_integrity),
    ("metrics", _metrics),
    ("case-scan", _case_scan),
    ("toolbox-data", _toolbox_data),
]


def run(verbose=True):
    """Run every check; collect results instead of stopping at the first failure."""
    t0 = time.perf_counter()
    passed, failed, skipped = [], [], []
    for name, fn in CHECKS:
        entry, status = _check(name, fn)
        {"pass": passed, "fail": failed, "skip": skipped}[status].append(entry)
        if verbose:
            print(f"{status.upper():4} {name}: {entry['detail']}")
    seconds = time.perf_counter() - t0
    result = {"ok": not failed, "passed": passed, "failed": failed, "skipped": skipped, "seconds": round(seconds, 3)}
    if verbose:
        print(f"{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped in {result['seconds']:.2f}s")
    return result


if __name__ == "__main__":
    raise SystemExit(0 if run(verbose=True)["ok"] else 1)
