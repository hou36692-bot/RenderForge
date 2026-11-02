"""Per-game placement adapters.

A 3DMigoto Frame Analysis dump has no post-vertex-shader positions, so `ingest_3dmigoto` can only emit
meshes in their own model space. Recovering where those meshes actually sit in the world is game
specific: it depends on which cbuffer slot holds the per-object matrix, which holds the view, and which
projection variant the pass used. That knowledge lives here, one module per game, rather than in the
shared pipeline: an adapter must never be imported by another game's adapter, so one game's quirks cannot
leak into the generic path.

Every adapter exposes the same shape so `pipeline` can call it without knowing the game:

    read_cbuffers(frame_dir, eid)      -> {slot_name: np.ndarray}       raw float32 register data
    solve_view_proj(cb_by_draw)        -> (V, P)                        row-vector view / projection
    solve_world(cb, C)                 -> 4x4 row-vector world matrix   for one draw
    solve_placement(frame_dir, extract_data) -> dict                    worlds + camera, no writes
    apply(frame_dir, extract_json, out_json) -> summary dict            annotate an extract in place

`solve_placement` is what the build path consumes: `world` maps draw id -> 16 floats (row-vector) and
`camera` is a scene-package camera dict. `build_model_space(..., placement=...)` places instances with
those matrices and reports how many picked draws had none, instead of fabricating a transform.

Matrices are row-vector convention throughout: `p_world = [p, 1] @ W`. `decompose()` in `solve` turns
such a W into the (location, quat, scale) triple the scene package stores.
"""
from importlib import import_module

# Games whose adapter can place a 3DMigoto extract in world space. A game profile opts in with
# `placement_adapter`, so the generic path only loads a module when that game says it has one.
PLACEMENT_ADAPTERS = {"zzz"}


def placement_adapter(name):
    """The adapter module for `name`, or None. Only games listed in PLACEMENT_ADAPTERS can be loaded."""
    if not name or name == "none":
        return None
    if name not in PLACEMENT_ADAPTERS:
        raise ValueError(f"unknown placement adapter {name!r}; known: {', '.join(sorted(PLACEMENT_ADAPTERS))}")
    return import_module(f".{name}", __name__)
