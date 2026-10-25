"""Run a Python script inside headless UE (commandlet-style) against the RenderForge UE project."""
import os
import subprocess
import sys
from pathlib import Path

UE_DIR = Path(os.environ.get("UE_DIR", r"D:\UE\UE_5.8"))
PROJECT = Path(__file__).resolve().parent.parent / "ue_project" / "RenderForgeUE" / "RenderForgeUE.uproject"
SCRIPTS = Path(__file__).resolve().parent / "ue_scripts"


def run(script, env_extra=None, log="ue_run.log", timeout=7200, nullrhi=True):
    logp = Path(__file__).resolve().parent.parent / "workspace" / "_logs" / log
    logp.parent.mkdir(exist_ok=True)
    cmd = [str(UE_DIR / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"), str(PROJECT),
           f"-ExecutePythonScript={SCRIPTS / script}", "-unattended", "-nosplash", f"-abslog={logp}"]
    if nullrhi:
        cmd.append("-nullrhi")
    env = dict(os.environ, **(env_extra or {}))
    p = subprocess.run(cmd, env=env, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode, logp


if __name__ == "__main__":
    rc, logp = run(sys.argv[1])
    print("exit", rc, "log", logp)


def run_editor(script, env_extra=None, log="ue_editor.log", timeout=1800):
    """Full editor (needs a GPU), off-screen; the script must call quit_editor() itself."""
    logp = Path(__file__).resolve().parent.parent / "workspace" / "_logs" / log
    cmd = [str(UE_DIR / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"), str(PROJECT),
           f"-ExecCmds=py {(SCRIPTS / script).as_posix()}", "-unattended", "-nosplash", "-windowed", "-ResX=1280", "-ResY=720", f"-abslog={logp}"]
    env = dict(os.environ, **(env_extra or {}))
    p = subprocess.run(cmd, env=env, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode, logp


def run_game_shot(case, shot_name, w=1920, h=1080, wait_ticks=900, log="ue_game.log", timeout=1800, console="",
                  script="game_shot.py", extra_env=None):
    """Launch the reconstructed map as a game (off-screen) and save a HighResShot into Saved/Screenshots."""
    logp = Path(__file__).resolve().parent.parent / "workspace" / "_logs" / log
    cmd = [str(UE_DIR / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"), str(PROJECT),
           f"/Game/RenderForge/{case}/Maps/Recon", "-game", "-RenderOffScreen", "-unattended", "-nosplash",
           f"-ResX={w}", f"-ResY={h}", f"-ExecCmds=py {(SCRIPTS / script).as_posix()}", f"-abslog={logp}"]
    env = dict(os.environ, RF_CASE=case, RF_W=str(w), RF_H=str(h), RF_WAIT_TICKS=str(wait_ticks),
               RF_SHOT_NAME=shot_name, RF_CMD=console, **(extra_env or {}))
    p = subprocess.run(cmd, env=env, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode, logp
