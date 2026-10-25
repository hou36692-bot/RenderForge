"""Per-game placement adapters.

A 3DMigoto Frame Analysis dump has no post-vertex-shader positions, so `ingest_3dmigoto` can only emit
meshes in their own model space. Recovering where those meshes actually sit in the world is game
specific: it depends on which cbuffer slot holds the per-object matrix, which holds the view, and which
projection variant the pass used. That knowledge lives here, one module per game, rather than in the
shared pipeline (see the isolation rule in CLAUDE.md).

Every adapter exposes the same shape so `pipeline` can call it without knowing the game:

    read_cbuffers(frame_dir, eid)      -> {slot_name: np.ndarray}       raw float32 register data
    solve_view_proj(cb_by_draw)        -> (V, P)                        row-vector view / projection
    solve_world(cb, C)                 -> 4x4 row-vector world matrix   for one draw
    apply(frame_dir, extract_json, out_json) -> summary dict            annotate an extract in place

Matrices are row-vector convention throughout: `p_world = [p, 1] @ W`. `decompose()` in `solve` turns
such a W into the (location, quat, scale) triple the scene package stores.
"""
