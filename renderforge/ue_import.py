import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from renderforge.ue_run import run

if __name__ == "__main__":
    scene, case = sys.argv[1], sys.argv[2]
    extra = {"RF_SCENE": scene, "RF_CASE": case}
    for a in sys.argv[3:]:
        k, v = a.split("=")
        extra[k] = v
    rc, logp = run("import_scene.py", extra, log=f"ue_import_{case}.log")
    print("exit", rc, "log", logp)
