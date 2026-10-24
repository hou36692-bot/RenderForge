---
name: renderforge
description: Drive the RenderForge toolbox (RenderDoc/3DMigoto/Unity capture -> UE/Unity scene) through its MCP tools instead of hand-built shell commands. Use when the user asks about capture-to-engine reconstruction, a workspace case, reconstruction quality numbers, or "what is installed / what should I run next" in this repo.
---

# RenderForge workbench

RenderForge turns a game frame (`.rdc`, 3DMigoto Frame Analysis dump, or a readable Unity bundle) into an editable
Scene Package and emits it into UE 5.8 or Unity 6. It is a **toolbox**, not one fixed pipeline: pick the route per
game and input.

Tool surface: the `renderforge` MCP server (`.mcp.json`), or the same commands via
`python -m renderforge <cmd>` with `.venv/Scripts/python.exe`. The MCP tools are thin wrappers over the CLI, so
their output and the CLI's are the same JSON.

## Start here (always, in this order)

1. `doctor` — what is actually installed. A missing RenderDoc/UE/Unity makes every downstream step impossible; say
   so instead of attempting it.
2. `plan` with `game` + `target` — which recipe applies and, per step, whether that tool is `verified-here`,
   `verified-synthetic`, `verified-prior`, `installed`, or only `candidate`. **Do not present a `candidate` tool as
   usable.**
3. `status` — every case in `workspace/`: source, which stages have artifacts, key numbers, and the one inferred
   next step. `status` with a `case` gives per-case detail and that case's next command.

Then act: `step` for one stage, `run` for the whole graph, `job_start` for anything slow.

## Reading the status vocabulary

Stages: `survey → export → build → identify → emit-ue / emit-unity → shot → metrics`.

- "done" always means **the artifact exists on disk**, not that a flag was recorded. `status` also cross-checks the
  engine project, so a case whose UE map was deleted reports `emit-ue` as the next step even if `case.json` says
  the import succeeded.
- `run` skips artifacts that exist, skips steps whose inputs are not on this machine (with the reason), and stops
  on a real failure after writing it into `case.json -> steps.<step>`; it prints the `--from` command that resumes.
- `identify` needs a SourceProbe `dump-index` directory. Without one it is not "passed", it is not run at all.
- `shot` is a UE game-mode off-screen render at the camera solved by `build`; `metrics` compares it with
  `export/reference_backbuffer.png`.

## Rules that keep claims honest

- **Never say a stage works because it exists.** Say what was run in this session, on which case, and what the
  number was. The repo convention is: no "done" without a command that actually ran.
- `metrics` numbers are approximate: same viewpoint, different lighting, solved camera. A good reconstruction still
  has nonzero error. Report `ssim`, `mae`, `coverage_error` together, never SSIM alone, and never call them
  pixel-identical. `ssim` below ~0.3 with a large `coverage_error` usually means missing geometry, not a bad metric;
  check `metrics_ssim.png` and the side-by-side before concluding.
- `shot_flipped` is a detected fact (Unity screenshots are often bottom-up). Do not "fix" a low score by assuming
  orientation; read the field.
- Build skips are honest gaps, not bugs to paper over: billboards/particles are not baked, non-rigid draws are
  baked from clip space and frozen at the captured pose, depth-only passes are not merged, GPU-driven indices lose
  part boundaries. If a user asks "why is X missing", read `status`'s `build report` before guessing.
- `docs/TOOLBOX.md` is generated (`python -m renderforge docs`). Change `toolbox/*.json` and regenerate; never
  hand-edit it.
- Original `.rdc` and game files are read-only. Generated data belongs in `workspace/<case>/`.

## Tool map

| Tool | Use it for |
|---|---|
| `doctor` | installed tools + versions, Python packages |
| `plan` | recipe + per-step tool status for a game profile and target (`unreal` / `unity`) |
| `games` | game profiles: engine, capture routes, source readers, verified items, gaps |
| `status` | case table / one case's detail and next step (`--json` equivalent is the default here) |
| `step` | one stage: `survey export build build-model ingest-unity ingest-3dmigoto identify ue-import unity-import shot metrics` |
| `run` | the case's step graph, `from`/`to`/`force` for resume or redo |
| `metrics` | SSIM / MAE / RMSE / p95 / coverage / mask IoU vs the reference frame |
| `job_start` / `job_status` / `job_log` / `jobs` | long stages detached (RenderDoc export, UE import, shot), outcome on disk |

There is deliberately no generic shell tool. If something cannot be done through these, the answer is "this is not
supported yet", not "let me spawn a command".

## Typical flows

- **New RenderDoc capture**: `status` → `step survey` (needs `rdc`) → `run --from export` → `metrics`.
- **Resume after a failure**: `status <case>` shows the failing step and its recorded error; `run <case> --from
  <step>` continues (add `force` to redo a step whose artifact is stale rather than missing).
- **Compare a different screenshot**: `metrics` with `shot` set to a path or a file under `shots/`.
- **Long render**: `job_start` with `step: "run"` and `extra: {"from": "export"}`, then `job_status` / `job_log`.

## Reference

- `docs/ARCHITECTURE.md` — layers, the two data contracts (Extract Package, Scene Package), licensing rules.
- `docs/TOOLBOX.md` — full tool table with status icons (generated).
- `toolbox/games.json` — per-game verified items and gaps; `toolbox/recipes.json` — workflows.
- `python -m renderforge selftest` — offline regression checks; run it after changing solver/space/ingest code.
