"""Entry point for a detached job: run the CLI, then write the exit marker `jobs.status` reads."""
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "workspace" / "_jobs"


def main(argv):
    jid, args = argv[0], argv[1:]
    t0 = time.time()
    rc = 1
    try:
        sys.path.insert(0, str(ROOT))
        from renderforge.cli import main as cli_main
        rc = cli_main(args) or 0
    except SystemExit as e:              # argparse errors
        rc = int(e.code or 0)
    except Exception:                    # noqa: BLE001 - the traceback is the job's output
        traceback.print_exc()
        rc = 1
    (JOBS / f"{jid}.done.json").write_text(json.dumps({"rc": rc, "ended": time.time(),
                                                      "seconds": round(time.time() - t0, 1)}), encoding="utf-8")
    print(f"\n[rf-job {jid}] exit {rc} after {time.time() - t0:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
