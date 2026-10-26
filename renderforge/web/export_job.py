"""Background worker for a case export started from the workbench.

A separate process so a 500-mesh export (minutes, hundreds of MB) never blocks the HTTP threads. It reuses the exact
builder the synchronous endpoint uses (`exporters.case_zip`), so both paths produce the same archive:

    .venv\\Scripts\\python.exe -m renderforge.web.export_job <job-id> <case>

Reads workspace/_exports/<id>.json for the options the web server recorded (fmt, meshes), writes
workspace/_exports/<id>.zip and finally merges {"state": "done"|"failed", "bytes": ...} back into that record.
"""
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from renderforge import caseinfo                      # noqa: E402
from renderforge.web import exporters                  # noqa: E402

WS = ROOT / "workspace"
EXPORTS = WS / "_exports"


def _write(jid, rec):
    p = EXPORTS / f"{jid}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    tmp.replace(p)


def main(jid, case):
    t0 = time.time()
    p = EXPORTS / f"{jid}.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    scene_dir = WS / case / "build"
    sc = caseinfo._json(scene_dir / "scene.json")
    if not sc:
        rec.update(state="failed", error=f"{case} has no build/scene.json", seconds=round(time.time() - t0, 1))
        _write(jid, rec)
        return 1
    mesh_ids = [m for m in (rec.get("meshes") or "").split(",") if m] or None
    try:
        buf, man = exporters.case_zip(scene_dir, sc, case, mesh_ids=mesh_ids, fmt=rec.get("fmt") or "obj",
                                      include_textures=rec.get("textures", True), workspace=WS,
                                      progress=lambda s: print(s, flush=True))
        out = EXPORTS / f"{jid}.zip"
        tmp = out.with_suffix(".zip.tmp")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(out)
        rec.update(state="done", bytes=man["bytes"], meshes=man["meshes"], textures=man["textures"],
                   skipped=man["skipped"][:50], seconds=round(time.time() - t0, 1))
        print(f"wrote {out} ({man['bytes'] / 1e6:.1f} MB, {man['meshes']} meshes, {man['textures']} textures)")
        rc = 0
    except Exception as e:                                # noqa: BLE001 - the record is the UI's only channel
        traceback.print_exc()
        rec.update(state="failed", error=f"{type(e).__name__}: {e}", seconds=round(time.time() - t0, 1))
        rc = 1
    _write(jid, rec)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
