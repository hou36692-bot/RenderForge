"""ZZZ (Zenless Zone Zero, Unity/IL2CPP) placement adapter: per-draw world matrices and the capture camera.

Every number below was measured on the Frame01 dump (FrameAnalysis-2026-09-28-223512, 267 draws, 213 of
them indexed meshes) and is reproducible with the probes under `workspace/zzz_probe/scripts/`.

cbuffer layout (vertex shader stage; 3DMigoto writes `NNNNNN-vs-cbN=<resource hash>...buf`):

  vs-cb1  per-OBJECT block (116 floats = 29 vec4 on the 3D draws):
            0..3      WORLD matrix, row-vector: p_world = [p, 1] @ W
            4..7      a copy of the world *inverse*, but only sometimes refreshed (see below)
            20..23    a second (world, ...) pair used by the shadow/depth pass
  vs-cb0  per-FRAME camera block. The block is only fully present when the buffer is 848 floats;
          screen-space UI draws bind a shorter cb0 with unrelated contents.
            95..98    projection for the 2880x1368 character preview (aspect 2.1053, vfov 24.03 deg)
            115..118  the same projection with a negated Y row
            119..122  VIEW matrix (world -> view, D3D handedness: view +Z points behind the camera)
            123..126  CAMERA-TO-WORLD matrix; its row 3 is the eye position
            126       the eye position literal, (700, 1.24, -2.55, 1)
            127..130  precomputed V @ P(preview)
            163       (2880, 1368, 1/2880, 1/1368)
            21.x      -0.44: a screen-space UI framing term (see "framing" below)
  vs-cb3  5..8    projection for the 3840x2160 composite frame (16:9, vfov 60 deg)
  vs-cb4 / cb5 / cb6  constant for every draw in the frame

How the slots were identified, not assumed:
  * `vs-cb1[0..3]` is the only 4x4 whose content changes from draw to draw while a matrix sits directly
    behind it; its rotation is orthonormal (|R R^T - I| < 1e-3) on 72 draws, which no arbitrary register
    window is.
  * `vs-cb0[123..126]` is camera-to-world: its row 3 equals the literal stored at `vs-cb0[126]` exactly
    (max abs error 0.0) and `vs-cb0[119..122] @ vs-cb0[123..126] == I` to 4.6e-5 on all 38 draws whose
    cb0 is 848 floats. The earlier reading of 119..122 as "the view matrix with the eye in its translation"
    was wrong: 119..122 has an all-zero translation column (it is world-to-camera, and the eye lives in the
    inverse). Reading the null vector of 119..122 as the eye is what produced a spurious 700-unit offset.
  * The inverse copy at `vs-cb1[4..7]` is stale on many draws (e.g. draw 193: `W @ W_inv - I` = 1.84),
    so it must NOT be used as a validation oracle. Rigidity of rows 0..3 is the usable check.

Vertex positions are MODEL space, and no model basis rotation is needed on top of W: applying
`p @ W @ V @ P` with the identity basis reproduces the dumped 2880x1368 character target. The
`model_basis` entry in `toolbox/games.json` ([[0,-1,0],[1,0,0],[0,0,1]]) belongs to the 3DMigoto
model-space path, where meshes carry no W at all. Applying it here rotates the character 90 degrees off
frame. Evidence for draw 106 (6936 triangles; the dumped RT alpha mask is 123201 px):

    variant          projected bbox x   bbox y      best IoU vs RT alpha   shift
    identity         1264..1613         899..1367   0.9783                 (+632, 0)
    @ model_basis    1283..1654         456..1016   0.5524                 (+664, -320)
    @ model_basis.T  1413..1757        1142..1367   0.3421                 (+752, +140)

The identity variant's vertical extent matches the mask exactly (899..1367 against 900..1367) and needs
no vertical shift; a 90-degree basis error could not produce that.

Framing residual: the preview pass does not draw the character where its projection puts it. The
ground-truth silhouette centre is at NDC x = -0.4413 while the projection says -0.0010, and
`vs-cb0[21].x == -0.44` with 0.44 * 1440 = 633.6 px, which is exactly the measured 632-634 px shift.
The projection windows have no x-offset term (`P[3,0] == 0` in every candidate), so this cannot be a
world or camera error: it is a screen-space term applied after projection, and it is identical for every
un-deformed draw (106, 109, 110, 111, 112, 113, 115, 116, 117, 121, 122) while skinned draws shift
differently (107: 360 px, 124: 256 px) because their POSITION0 is bind pose. `apply()` leaves the world
matrices and camera in true world space; `to_reference()` adds the term for consumers that want to
compare against the dumped preview directly.

Two cameras, one pose: the character preview uses cb0[95..98] (2880x1368, vfov 24) and the composite frame
uses cb3[5..8] (3840x2160, vfov 60) with the same cb0 view. `solve_placement()` exposes the preview camera
(`camera`/`vp`) and the world camera (`frame_camera`/`frame_vp`); `scene_camera`/`scene_vp` pick the world
one, because a reconstructed scene contains world geometry. The preview camera stays for comparisons against
the character render target.

Preview -> final frame: the 2880x1368 preview is composited into the 3840x2160 frame. Measured by
matching its character pixels against `reference_backbuffer.png` (byte-identical to the dumped 3840x2160
`o0=91005981` target, mean abs diff 0.00): scale 1.5789, offset (-200, 0), residual 31/255 over 16110
character samples. 1368 * 1.5789 = 2160 exactly, so the preview fills the frame height and is cropped
horizontally to 16:9.
"""
import json
import re
from pathlib import Path

import numpy as np

# Register windows, in vec4 rows. Named so the rest of the module carries no bare magic numbers.
VIEW_ROWS = 119            # vs-cb0: world -> view
CAMERA_ROWS = 123          # vs-cb0: view -> world (camera-to-world); row 3 is the eye
PROJ_PREVIEW_ROWS = 95     # vs-cb0: projection of the 2880x1368 preview
FRAME_PROJ_SLOT = 3        # vs-cb3: projection of the 3840x2160 composite frame
FRAME_PROJ_ROWS = 5        # vs-cb3 rows 5..8
FRAME_BLOCK_FLOATS = 92    # cb3 is this long on the 3D draws; other draws bind shorter/unrelated blocks
WORLD_ROWS = 0             # vs-cb1: world matrix
EYE_ROW = 126              # vs-cb0: eye position literal, cross-check for CAMERA_ROWS
UI_OFFSET_ROW = 21         # vs-cb0: screen-space x framing term, value is UI_OFFSET_X
UI_OFFSET_X = -0.44        # measured at vs-cb0[21].x; 0.44 * 1440 px = the 634 px residual
CAMERA_BLOCK_FLOATS = 848  # cb0 is only the camera block at this size; UI draws bind shorter buffers

PREVIEW_W, PREVIEW_H = 2880, 1368
FRAME_W, FRAME_H = 3840, 2160
# Measured composite rect of the preview inside the final frame (see module docstring).
PREVIEW_TO_FRAME = {"scale": 1.5789, "offset": (-200.0, 0.0), "mae": 31.4, "samples": 16110}

_SLOT_RE = re.compile(r"^(?P<eid>\d{6})-vs-cb(?P<slot>\d+)=(?P<hash>[0-9a-f]+)")


def read_cbuffers(frame_dir, eid):
    """Raw float32 register data for every vertex-shader cbuffer bound at draw `eid`.

    The hash in the filename identifies the *resource*, not the contents: the same hash appears on many
    draws with different bytes, so it cannot be used to group draws by content.
    """
    out = {}
    for p in Path(frame_dir).glob(f"{int(eid):06d}-vs-cb*.buf"):
        m = _SLOT_RE.match(p.name)
        if m and int(m.group("eid")) == int(eid):
            out[int(m.group("slot"))] = np.fromfile(p, dtype="<f4")
    return out


def _win(data, row):
    """Four consecutive vec4 rows starting at `row`, as a 4x4 float64 matrix (row-vector convention)."""
    a = np.asarray(data, dtype=np.float64)
    need = (row + 4) * 4
    if a.size < need:
        raise ValueError(f"cbuffer holds {a.size} floats, need {need} for rows {row}..{row + 3}")
    return a[row * 4:need].reshape(4, 4)


def _camera_draw(cb_by_draw):
    """Lowest draw id whose vs-cb0 is the full camera block (848 floats) and is self-consistent."""
    for eid in sorted(cb_by_draw):
        cb = cb_by_draw[eid]
        if 0 not in cb or len(cb[0]) != CAMERA_BLOCK_FLOATS:
            continue
        V, C = _win(cb[0], VIEW_ROWS), _win(cb[0], CAMERA_ROWS)
        if np.abs(V @ C - np.eye(4)).max() < 1e-3:
            return eid
    raise ValueError(f"no draw carries a consistent camera block (vs-cb0 of {CAMERA_BLOCK_FLOATS} floats)")


def solve_view_proj(cb_by_draw):
    """(view, projection) for the character preview pass, as row-vector 4x4 matrices.

    `cb_by_draw` maps draw id -> {slot: array} (the output of `read_cbuffers`). The camera block is
    identical across the draws that carry it, so the lowest consistent one is used.
    """
    cb = cb_by_draw[_camera_draw(cb_by_draw)][0]
    return _win(cb, VIEW_ROWS), _win(cb, PROJ_PREVIEW_ROWS)


def solve_camera(cb_by_draw):
    """Camera pose of the preview pass, in the game's world space (Unity axes: X right, Y up, Z forward).

    Row-vector camera-to-world C: its rows are right / up / backward (D3D `LookAt` convention), so the
    forward axis is -C[2]. The eye is C[3], and is cross-checked against the eye literal at vs-cb0[126].
    """
    cb = cb_by_draw[_camera_draw(cb_by_draw)][0]
    C = _win(cb, CAMERA_ROWS)
    P = _win(cb, PROJ_PREVIEW_ROWS)
    pos, right, up = C[3, :3].copy(), C[0, :3].copy(), C[1, :3].copy()
    fwd = -C[2, :3]
    fwd = fwd / max(np.linalg.norm(fwd), 1e-12)
    up = up / max(np.linalg.norm(up), 1e-12)
    right = np.cross(up, fwd)
    literal = np.asarray(cb, dtype=np.float64)[EYE_ROW * 4:EYE_ROW * 4 + 3]
    return {"position": pos.tolist(), "forward": fwd.tolist(), "up": up.tolist(), "right": right.tolist(),
            "hfov_deg": float(np.degrees(2 * np.arctan(1.0 / abs(P[0, 0])))),
            "vfov_deg": float(np.degrees(2 * np.arctan(1.0 / abs(P[1, 1])))),
            "aspect": float(abs(P[1, 1]) / abs(P[0, 0])),
            "eye_literal": literal.tolist(),
            "eye_literal_error": float(np.abs(pos - literal).max())}


def solve_frame_camera(cb_by_draw):
    """(camera, P) for the 3840x2160 composite frame, or (None, None) when no draw carries cb3.

    The preview and frame cameras share a pose but not a projection: the character is rendered with the
    24-degree preview projection, while the world behind it uses this 60-degree 16:9 one. A reconstructed
    scene wants the world camera; the preview camera stays available for comparisons against the character
    render target. Pose comes from the same cb0 camera block, so the two cameras cannot drift apart.
    """
    for eid in sorted(cb_by_draw):
        cb = cb_by_draw[eid]
        if FRAME_PROJ_SLOT not in cb or len(cb[FRAME_PROJ_SLOT]) < FRAME_BLOCK_FLOATS:
            continue
        P = _win(cb[FRAME_PROJ_SLOT], FRAME_PROJ_ROWS)
        cam = dict(solve_camera(cb_by_draw))
        cam.update({
            "hfov_deg": float(np.degrees(2 * np.arctan(1.0 / abs(P[0, 0])))),
            "vfov_deg": float(np.degrees(2 * np.arctan(1.0 / abs(P[1, 1])))),
            "aspect": float(FRAME_W / FRAME_H),
            "projection": "frame",
        })
        return cam, P
    return None, None


def solve_world(cb, C=None):
    """Per-draw world matrix from vs-cb1 rows 0..3 (row-vector: p_world = [p, 1] @ W).

    `C` is accepted for signature parity with other adapters; ZZZ needs nothing beyond the object block.
    Raises if the window is not a rigid transform, which is what catches a wrong-slot decode instead of
    silently emitting garbage. The inverse stored at rows 4..7 is deliberately not used: it is stale on
    many draws.
    """
    W = _win(cb[1], WORLD_ROWS)
    if abs(W[3, 3] - 1.0) > 1e-4 or np.abs(W[:3, 3]).max() > 1e-4:
        raise ValueError(f"vs-cb1 rows 0..3 is not affine: w column {np.round(W[:3, 3], 5).tolist()}, "
                         f"W[3,3]={W[3, 3]:.6f}")
    R = W[:3, :3]
    orth = float(np.abs(R @ R.T - np.eye(3)).max())
    if orth > 1e-3:
        raise ValueError(f"vs-cb1 rows 0..3 is not a rigid rotation: |R R^T - I| = {orth:.2e}")
    return W


def to_reference(V, P, ui_offset=UI_OFFSET_X):
    """The preview pass's on-screen framing: NDC x is shifted by `ui_offset` before rasterising.

    In row-vector form, adding `k * clip.w` to `clip.x` is post-multiplying by the identity with
    `S[3,0] = k`, because row 3 of a projection is the w row. `k` is the *value* -0.44 stored at
    vs-cb0[21].x, not the row index 21.
    """
    S = np.eye(4)
    S[3, 0] = float(ui_offset)
    return V @ P @ S


def solve_placement(frame_dir, extract_data):
    """Per-draw world matrices and the capture camera for an extract, without writing anything.

    This is the interface the build path consumes: `build_model_space(..., placement=...)` reads
    `world` (eid -> 16 floats, row-vector) and `camera`. Draws that fail the rigidity check are
    screen-space UI passes (they bind a 36-float vs-cb1), not 3D objects; they are absent from `world`
    and listed in `fail_reasons` rather than given a fabricated matrix.

    Raises if no draw at all yields a world matrix.
    """
    draws = extract_data["draws"]
    eids = sorted({int(d["eid"]) for d in draws})
    cbs = {eid: read_cbuffers(frame_dir, eid) for eid in eids}
    V, P = solve_view_proj(cbs)
    cam = solve_camera(cbs)
    frame_cam, frame_P = solve_frame_camera(cbs)
    frame_vp = (V @ frame_P) if frame_P is not None else None

    worlds, fail = {}, []
    for eid in eids:
        cb = cbs.get(eid) or {}
        if 1 not in cb:
            fail.append((eid, "no vs-cb1"))
            continue
        try:
            worlds[eid] = solve_world(cb)
        except ValueError as e:
            fail.append((eid, str(e)))
    if not worlds:
        raise RuntimeError("no draw yielded a world matrix; vs-cb1 does not hold one in this capture")

    camera_eid = _camera_draw(cbs)
    return {
        "name": "zzz", "source": "3dmigoto", "space": "unity",
        "world": {int(eid): [float(x) for x in W.reshape(-1)] for eid, W in worlds.items()},
        "world_ok": len(worlds), "draws": len(draws), "world_fail": len(fail), "fail_reasons": fail[:5],
        "camera": cam,
        "view": [float(x) for x in V.reshape(-1)],
        "projection": [float(x) for x in P.reshape(-1)],
        "vp": [float(x) for x in (V @ P).reshape(-1)],
        # the world camera of the composited frame; the preview camera stays available for RT comparisons
        "frame_camera": frame_cam,
        "frame_projection": [float(x) for x in frame_P.reshape(-1)] if frame_P is not None else None,
        "frame_vp": [float(x) for x in frame_vp.reshape(-1)] if frame_vp is not None else None,
        "scene_camera": frame_cam or cam,
        "scene_vp": [float(x) for x in (frame_vp if frame_vp is not None else V @ P).reshape(-1)],
        "scene_size": [FRAME_W, FRAME_H] if frame_cam else [PREVIEW_W, PREVIEW_H],
        "view_rows": VIEW_ROWS, "camera_rows": CAMERA_ROWS, "projection_rows": PROJ_PREVIEW_ROWS,
        "frame_projection_slot": FRAME_PROJ_SLOT, "frame_projection_rows": FRAME_PROJ_ROWS,
        "world_rows": WORLD_ROWS, "preview_size": [PREVIEW_W, PREVIEW_H],
        "frame_size": [FRAME_W, FRAME_H], "preview_to_frame": PREVIEW_TO_FRAME,
        "ui_offset_ndc_x": float(np.asarray(cbs[camera_eid][0])[UI_OFFSET_ROW * 4]),
        "model_basis": None,
    }


def apply(frame_dir, extract_json_path, out_json_path, world_space=True, placement=None):
    """Annotate an extract with per-draw world matrices, the camera, and the view-projection.

    Writes a copy of `extract_json_path` to `out_json_path`. Every draw gains
        "world":    16 floats, row-vector (p_world = [p, 1] @ W), or null when it has no rigid world
        "world_ok": whether that check passed
    and the package gains "camera", "view", "projection", "vp", "adapter".

    `placement` lets a caller that already ran `solve_placement()` skip the cbuffer pass (the build path
    does, so a build does not decode the same dumps twice). Returns a summary dict; raises if no draw at
    all yields a world matrix. This function exists to keep a reviewable copy on disk.
    """
    frame_dir, src, dst = Path(frame_dir), Path(extract_json_path), Path(out_json_path)
    data = json.loads(src.read_text(encoding="utf-8"))
    pl = placement or solve_placement(frame_dir, data)
    V = np.asarray(pl["view"], dtype=np.float64).reshape(4, 4)
    P = np.asarray(pl["projection"], dtype=np.float64).reshape(4, 4)

    for d in data["draws"]:
        W = pl["world"].get(int(d["eid"]))
        d["world"] = W
        d["world_ok"] = W is not None

    data["camera"] = pl["camera"]
    data["view"] = pl["view"]
    data["projection"] = pl["projection"]
    data["vp"] = [float(x) for x in (V @ P if world_space else to_reference(V, P)).reshape(-1)]
    data["frame_camera"] = pl["frame_camera"]
    data["frame_projection"] = pl["frame_projection"]
    data["frame_vp"] = pl["frame_vp"]
    data["adapter"] = {
        "game": "zzz", "source": "3dmigoto", "convention": "row-vector (p_world = [p,1] @ W)",
        "view_rows": pl["view_rows"], "camera_rows": pl["camera_rows"], "projection_rows": pl["projection_rows"],
        "frame_projection_slot": pl["frame_projection_slot"], "frame_projection_rows": pl["frame_projection_rows"],
        "world_rows": pl["world_rows"], "preview_size": pl["preview_size"],
        "frame_size": pl["frame_size"], "preview_to_frame": pl["preview_to_frame"],
        "scene_camera": "frame" if pl["frame_camera"] else "preview",
        "ui_offset_ndc_x": pl["ui_offset_ndc_x"], "model_basis": None,
        "note": "the model_basis in toolbox/games.json must NOT be applied on top of these world "
                "matrices; see renderforge/adapters/zzz.py",
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, indent=1), encoding="utf-8")

    return {"draws": pl["draws"], "world_ok": pl["world_ok"], "world_fail": pl["world_fail"],
            "fail_reasons": pl["fail_reasons"], "camera": pl["camera"],
            "scene_camera": "frame" if pl["frame_camera"] else "preview",
            "preview_to_frame": PREVIEW_TO_FRAME, "out": str(dst)}
