"""Transform solving: recover per-draw world matrices and the camera ViewProj from an extract."""
import json
from pathlib import Path

import numpy as np


class Extract:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data = json.load(open(self.root / "extract.json"))
        self.meshes = self.data["meshes"]
        self.draws = self.data["draws"]
        self._cb = {}

    def cbuffer(self, h):
        if h not in self._cb:
            self._cb[h] = np.fromfile(self.root / "cbuffers" / (h + ".bin"), dtype="f4")
        return self._cb[h]

    def mesh_attr(self, mesh_id, idx):
        a = self.meshes[mesh_id]["attrs"][idx]
        arr = np.fromfile(self.root / "meshes" / mesh_id / a["file"], dtype="f4")
        return arr.reshape(-1, a["comps"])

    def position_attr_index(self, mesh_id):
        for i, a in enumerate(self.meshes[mesh_id]["attrs"]):
            if a["comps"] in (3, 4) and a["format"].endswith("FLOAT") and not a["perInstance"] and "32" in a["format"]:
                return i
        return None

    def vsout(self, draw):
        p = draw["postVS"]
        if not p:
            return None
        return np.fromfile(self.root / p["file"], dtype="f4").reshape(-1, 4)


def fit_local_to_clip(pos, clip, max_samples=400, seed=0):
    """Least-squares M so that [pos,1] @ M == clip (row-vector convention). Returns (M, rel_error)."""
    n = len(pos)
    if n < 8:
        return None, 1e9
    rng = np.random.default_rng(seed)
    sel = rng.choice(n, min(n, max_samples), replace=False) if n > max_samples else np.arange(n)
    h = np.c_[pos[sel, :3].astype(np.float64), np.ones(len(sel))]
    c = clip[sel].astype(np.float64)
    if not np.isfinite(c).all():
        return None, 1e9
    M = np.linalg.lstsq(h, c, rcond=None)[0]
    scale = max(np.abs(c).max(), 1e-9)
    err = np.abs(h @ M - c).max() / scale
    # validate on all vertices when we subsampled
    if len(sel) < n:
        ha = np.c_[pos[:, :3].astype(np.float64), np.ones(n)]
        err = max(err, np.abs(ha @ M - clip).max() / scale)
    return M, float(err)


def affine_score(W):
    """How close W (row-vector convention 4x4) is to a scaled-rotation + translation. 0 = perfect."""
    if abs(W[3, 3]) < 1e-12:
        return 9.0
    W = W / W[3, 3]
    lc = np.abs(W[:3, 3]).max()
    R = W[:3, :3]
    rs = np.linalg.norm(R, axis=1)
    if rs.min() < 1e-9:
        return 9.0
    Rn = R / rs[:, None]
    orth = np.abs(Rn @ Rn.T - np.eye(3)).max()
    return float(lc + orth)


def proj_score(W):
    """Distance of the last column from (0,0,0,1): 0 for any true affine transform (shear allowed)."""
    if abs(W[3, 3]) < 1e-12:
        return 9.0
    return float(np.abs(W[:3, 3] / W[3, 3]).max())


def fit_static_draws(ex, draw_filter=None, tol=2e-4):
    out = []
    for d in ex.draws:
        if draw_filter and not draw_filter(d):
            continue
        pi = ex.position_attr_index(d["mesh"])
        vs = ex.vsout(d)
        if pi is None or vs is None:
            continue
        pos = ex.mesh_attr(d["mesh"], pi)
        if len(pos) != len(vs):
            continue
        M, err = fit_local_to_clip(pos, vs)
        if M is not None and err < tol:
            out.append({"draw": d, "M": M, "err": err})
    return out


def vp_candidates(ex, draws, max_distinct=3):
    """Yield (name, matrix_row_convention) for 4x4 windows in VS/PS cbuffers that are shared across draws."""
    slots = {}
    for d in draws:
        for k, cb in d["cbuffers"].items():
            slots.setdefault(k, set()).add(cb["hash"])
    for k, hs in sorted(slots.items()):
        if len(hs) > max_distinct:
            continue
        for h in sorted(hs):
            data = ex.cbuffer(h)
            for off in range(0, len(data) - 15, 4):
                A = data[off:off + 16].astype(np.float64).reshape(4, 4)
                if not np.isfinite(A).all() or abs(np.linalg.det(A)) < 1e-12:
                    continue
                if np.linalg.cond(A) > 1e9:
                    continue
                for tag, B in (("A", A), ("AT", A.T.copy())):
                    # a perspective VP has clip.w == view depth, so its w column is a unit forward vector
                    if abs(np.linalg.norm(B[:3, 3]) - 1.0) < 1e-2:
                        yield f"{k}:{h}:{off*4}:{tag}", B


def find_view_proj(ex, statics, top=5, sample=60):
    """Rank candidate VP matrices by how many static draws yield a valid affine world matrix."""
    sub = statics[:: max(1, len(statics) // sample)]
    ranked = []
    for name, C in vp_candidates(ex, [s["draw"] for s in statics]):
        try:
            Ci = np.linalg.inv(C)
        except np.linalg.LinAlgError:
            continue
        good = 0
        for s in sub:
            if proj_score(s["M"] @ Ci) < 1e-3:
                good += 1
        if good:
            ranked.append((good / len(sub), name, C))
    ranked.sort(key=lambda r: -r[0])
    return ranked[:top]


def regularize_planar(W, pos, planar_tol=1e-4):
    """Flat meshes (cards, decals) leave the out-of-plane axis of a least-squares fit arbitrary.
    Rebuild it as a similarity-consistent axis so W is a proper transform. Returns (W, was_planar)."""
    P = pos[:, :3].astype(np.float64)
    c = P.mean(0)
    u, sv, vt = np.linalg.svd(P - c, full_matrices=False)
    if sv[0] < 1e-9 or sv[2] / sv[0] > planar_tol:
        return W, False
    B = vt                              # rows u1,u2,n
    W3 = W[:3, :3]
    a1, a2 = B[0] @ W3, B[1] @ W3
    n3 = np.cross(a1, a2)
    ln = np.linalg.norm(n3)
    if ln < 1e-12:
        return W, True
    a3 = n3 / ln * 0.5 * (np.linalg.norm(a1) + np.linalg.norm(a2))
    A = np.vstack([a1, a2, a3])
    W3n = B.T @ A
    out = W.copy()
    world_c = c @ W3 + W[3, :3]
    out[:3, :3] = W3n
    out[3, :3] = world_c - c @ W3n
    return out, True


def decompose(W):
    """Row-vector 4x4 -> (location, quat xyzw, scale) matching UE FTransform(p' = q*(s*p)+t). Shear is dropped."""
    A = W[:3, :3]
    U, S, Vt = np.linalg.svd(A)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U = U.copy()
        U[:, 2] *= -1
        R = U @ Vt
    scale = np.diag(A @ R.T).copy()
    Rc = R.T  # column-vector rotation
    t = np.trace(Rc)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = [(Rc[2, 1] - Rc[1, 2]) / s, (Rc[0, 2] - Rc[2, 0]) / s, (Rc[1, 0] - Rc[0, 1]) / s, 0.25 * s]
    elif Rc[0, 0] > Rc[1, 1] and Rc[0, 0] > Rc[2, 2]:
        s = np.sqrt(1.0 + Rc[0, 0] - Rc[1, 1] - Rc[2, 2]) * 2
        q = [0.25 * s, (Rc[0, 1] + Rc[1, 0]) / s, (Rc[0, 2] + Rc[2, 0]) / s, (Rc[2, 1] - Rc[1, 2]) / s]
    elif Rc[1, 1] > Rc[2, 2]:
        s = np.sqrt(1.0 + Rc[1, 1] - Rc[0, 0] - Rc[2, 2]) * 2
        q = [(Rc[0, 1] + Rc[1, 0]) / s, 0.25 * s, (Rc[1, 2] + Rc[2, 1]) / s, (Rc[0, 2] - Rc[2, 0]) / s]
    else:
        s = np.sqrt(1.0 + Rc[2, 2] - Rc[0, 0] - Rc[1, 1]) * 2
        q = [(Rc[0, 2] + Rc[2, 0]) / s, (Rc[1, 2] + Rc[2, 1]) / s, 0.25 * s, (Rc[1, 0] - Rc[0, 1]) / s]
    q = np.array(q)
    q /= np.linalg.norm(q)
    return W[3, :3].copy(), q, scale
