"""Numeric score of a reconstruction screenshot against the game's reference backbuffer.

This is a *same-viewpoint approximate* comparison, not a pixel test. The camera is solved approximately (fov and pose can
differ by a few percent) and UE/Unity lighting is not the game's, so a good reconstruction still has visible absolute
error. The numbers here answer two questions instead: did the same geometry land in roughly the same place (ssim, mask
overlap), and did anything render at all (coverage).

SSIM is the standard uniform-window variant on BT.601 luminance, computed with integral images so 1080p stays well under
a second. Coverage is a heuristic: background is taken to be the dominant quantised colour in the top 20% rows plus the
four 32-px borders, and everything farther than a small distance from it is "foreground". That is strong enough to catch
an empty or half-missing scene and is deliberately not a segmentation.

Orientation is detected, not assumed: Unity ReadPixels screenshots are often bottom-up while UE HighResShot is top-down,
so both orientations are tried and the truth is reported in `shot_flipped`.

    compare("workspace/case/export/reference_backbuffer.png", "workspace/case/shots/case_ue.png")
    compare_case("workspace/case")            # -> workspace/case/metrics.json
    python -m renderforge.metrics <case_dir> | <reference.png> <shot.png>
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

_WIN = 8               # SSIM window, uniform; valid windows only (no padding)
_BG_DIST = 0.02        # per-channel RMS distance from the background colour that still counts as background
_FLIP_MARGIN = 0.02    # flipped must beat as-is MAE by this relative margin to be called "clearly better"
_FLIP_EPS = 1e-4       # ...and by this absolute margin, so a near-noise difference never flips an image

_SCOPE = (
    "same-viewpoint approximate comparison, not pixel-identical: camera pose/fov is solved approximately and "
    "UE/Unity lighting differs from the game, so a good reconstruction still shows nonzero error; coverage is a "
    "heuristic background-colour estimate meant to catch missing or empty geometry, not an exact segmentation."
)


def _sig(x, n=6):
    """Round to n significant digits; keeps small errors informative while producing stable JSON."""
    v = float(x)
    return float(f"{v:.{n}g}")


def _require_image(path):
    if not Path(path).is_file():
        raise FileNotFoundError(f"image not found: {path}")


def _load_rgb(path):
    with Image.open(path) as im:
        return im.convert("RGB")


def _box_mean(a, win):
    """Mean over every fully-contained win x win window, via an integral image (O(n))."""
    h, w = a.shape
    s = np.zeros((h + 1, w + 1), dtype=np.float64)
    s[1:, 1:] = np.cumsum(np.cumsum(a, axis=0, dtype=np.float64), axis=1, dtype=np.float64)
    total = s[win:, win:] - s[:-win, win:] - s[win:, :-win] + s[:-win, :-win]
    return total / float(win * win)


def _luma(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _ssim(rgb_a, rgb_b, win=_WIN):
    """Global SSIM mean plus the per-window map (shape: H-win+1 x W-win+1)."""
    win = max(1, min(win, rgb_a.shape[0], rgb_a.shape[1]))   # images smaller than the window still get one window
    x, y = _luma(rgb_a), _luma(rgb_b)
    mx, my = _box_mean(x, win), _box_mean(y, win)
    # Covariance is computed as E[xy]-E[x]E[y]; for identical inputs x and y the two sides are bit-identical, so an
    # identical pair yields exactly 1.0 rather than 0.9999999999.
    vx = _box_mean(x * x, win) - mx * mx
    vy = _box_mean(y * y, win) - my * my
    vxy = _box_mean(x * y, win) - mx * my
    c1 = (0.01 * 1.0) ** 2
    c2 = (0.03 * 1.0) ** 2
    num = (2.0 * mx * my + c1) * (2.0 * vxy + c2)
    den = (mx * mx + my * my + c1) * (vx + vy + c2)
    smap = np.clip(num / den, -1.0, 1.0)
    return float(smap.mean()), smap


def _background_colour(rgb):
    """Mode of a 16-level quantisation of the top 20% rows plus the four 32-px borders. Heuristic by design."""
    h, w = rgb.shape[:2]
    band = rgb[: max(1, int(round(h * 0.2)))].reshape(-1, 3)
    b = max(1, min(32, h // 8, w // 8))
    borders = np.concatenate([
        rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
        rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3),
    ])
    sample = np.concatenate([band, borders]).astype(np.float64)
    q = np.clip(np.rint(sample * 15.0), 0, 15).astype(np.int32)
    key = (q[:, 0] * 16 + q[:, 1]) * 16 + q[:, 2]
    counts = np.bincount(key, minlength=4096)
    return sample[key == int(counts.argmax())].mean(axis=0)


def _foreground(rgb):
    """Boolean mask of pixels farther than _BG_DIST (RMS per channel) from the estimated background colour."""
    bg = _background_colour(rgb)
    dist = np.sqrt(np.mean((rgb.astype(np.float64) - bg) ** 2, axis=-1))
    return dist > _BG_DIST


def _coverage(rgb):
    mask = _foreground(rgb)
    return mask, float(mask.mean())


def _mae(a, b):
    return float(np.mean(np.abs(a - b)))


def _write_ssim_png(smap, path):
    v = np.clip(smap, 0.0, 1.0)
    Image.fromarray((v * 255.0 + 0.5).astype(np.uint8), mode="L").save(path)


def compare(reference_path, shot_path, out_json=None):
    """Compare one reference PNG with one reconstruction PNG; see the module docstring for what the numbers mean."""
    t0 = time.perf_counter()
    reference_path, shot_path = Path(reference_path), Path(shot_path)
    _require_image(reference_path)
    _require_image(shot_path)

    ref_im, shot_im = _load_rgb(reference_path), _load_rgb(shot_path)
    ref_size, shot_size = list(ref_im.size), list(shot_im.size)

    # Never upscale: the larger image is reduced to the smaller one's size.
    if ref_size[0] * ref_size[1] > shot_size[0] * shot_size[1]:
        ref_im = ref_im.resize(tuple(shot_size), Image.Resampling.LANCZOS)
    elif shot_size[0] * shot_size[1] > ref_size[0] * ref_size[1]:
        shot_im = shot_im.resize(tuple(ref_size), Image.Resampling.LANCZOS)
    common = list(shot_im.size)

    ref = np.asarray(ref_im, dtype=np.float64) / 255.0
    shot = np.asarray(shot_im, dtype=np.float64) / 255.0

    # Bottom-up screenshots are common enough that the orientation is measured, never assumed.
    mae_asis = _mae(ref, shot)
    mae_flip = _mae(ref, shot[::-1])
    flipped = bool(mae_flip < mae_asis * (1.0 - _FLIP_MARGIN) and (mae_asis - mae_flip) > _FLIP_EPS)
    if flipped:
        shot = shot[::-1]

    diff = ref - shot
    mae = float(np.mean(np.abs(diff)))
    rmse = float(np.sqrt(np.mean(diff * diff)))
    p95_abs = float(np.percentile(np.mean(np.abs(diff), axis=-1), 95))
    ssim, smap = _ssim(ref, shot)

    mask_ref, coverage_ref = _coverage(ref)
    mask_shot, coverage_shot = _coverage(shot)
    union = np.logical_or(mask_ref, mask_shot).sum()
    inter = np.logical_and(mask_ref, mask_shot).sum()
    mask_iou = 1.0 if union == 0 else float(inter) / float(union)

    result = {
        "reference": str(reference_path),
        "compared": shot_path.name,
        "sizes": {"reference": ref_size, "shot": shot_size, "compared": common},
        "shot_flipped": flipped,
        "orientation": "bottom-up (flipped vertically to top-down)" if flipped else "top-down (as-is)",
        "flip_check_mae": {"as_is": _sig(mae_asis), "flipped": _sig(mae_flip)},
        "ssim": _sig(ssim),
        "mae": _sig(mae),
        "rmse": _sig(rmse),
        "p95_abs": _sig(p95_abs),
        "coverage_ref": _sig(coverage_ref),
        "coverage_shot": _sig(coverage_shot),
        "coverage_error": _sig(abs(coverage_shot - coverage_ref)),
        "mask_iou": _sig(mask_iou),
        "scope": _SCOPE,
        "seconds": round(time.perf_counter() - t0, 3),
    }

    if out_json is not None:
        out = Path(out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        ssim_png = out.parent / "metrics_ssim.png"   # dark = locally bad match
        _write_ssim_png(smap, ssim_png)
        result["ssim_map"] = str(ssim_png)
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def compare_case(case_dir, shot_name=None, out_json=None):
    """Compare a case's reference backbuffer against its reconstruction shot and write <case>/metrics.json."""
    case = Path(case_dir)
    ref = case / "export" / "reference_backbuffer.png"
    _require_image(ref)

    if shot_name is None:
        candidates = [
            case / "shots" / f"{case.name}_ue.png",
            case / "shots" / f"{case.name}_unity.png",
            case / "unity_shot.png",          # zzz-era layout
        ]
        shot = next((p for p in candidates if p.is_file()), None)
        if shot is None:
            tried = ", ".join(str(p) for p in candidates)
            raise FileNotFoundError(f"shot image not found; tried: {tried}")
    else:
        p = Path(shot_name)
        # An existing path is taken as-is (absolute, or relative to the caller's cwd); otherwise it is a file name
        # inside the case's shots/ (then the case root).
        if p.is_file():
            shot = p
        else:
            found = [c for c in (case / "shots" / p, case / p) if c.is_file()]
            shot = found[0] if found else case / "shots" / p
        _require_image(shot)

    out = Path(out_json) if out_json is not None else case / "metrics.json"
    return compare(ref, shot, out_json=out)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 1:
        result = compare_case(args[0])
    elif len(args) == 2:
        result = compare(args[0], args[1])
    else:
        print("usage: python -m renderforge.metrics <case_dir> | <reference.png> <shot.png>", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
