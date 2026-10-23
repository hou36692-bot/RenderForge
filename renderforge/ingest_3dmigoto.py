"""Ingest a 3DMigoto Frame Analysis dump (see notes on indirect draws below) (XXMI / GIMI / SRMI / ZZMI / WWMI captures) into a RenderForge extract package.

Indirect draws (DrawIndexedInstancedIndirect): ZZZ issues its character draws this way with GPU-generated arguments, so the
dump only contains stale CPU-side writes of the args buffer. Resolving them from those writes was tried on ZZZ Frame01 and gives
wrong index ranges (shredded meshes), so it is off by default (`resolve_indirect=True` to experiment); by default the whole
dumped index buffer is used.

The extract package has the same shape as the RenderDoc export (extract.json + meshes/ + textures/ + cbuffers/), so
downstream stages (matching, build, emitters) do not care where the data came from. What differs: Frame Analysis has no
post-vertex-shader positions, so world placement cannot be solved generically; meshes are emitted in their own
(model/skinned) space and a per-game placement hook may refine them later.
"""
import hashlib
import json
import re
import shutil
import struct
from pathlib import Path

import numpy as np

FILE_RE = re.compile(r"^(?P<draw>\d{6})-(?P<slot>(?:[a-z]+-)?[A-Za-z]+\d*)=(?P<hash>(?:!S!=)?[0-9a-f]+)(?:-vs=(?P<vs>[0-9a-f]+))?(?:-ps=(?P<ps>[0-9a-f]+))?\.(?P<ext>buf|txt|dds|dsc)$")
DRAW_RE = re.compile(r"^(\d{6}) (DrawIndexed|DrawIndexedInstanced|DrawIndexedInstancedIndirect|Draw|DrawInstanced)\((.*)\)")
FORMATS = {  # DXGI name -> (numpy dtype, components, normalization)
    "R32G32B32A32_FLOAT": ("<f4", 4, None), "R32G32B32_FLOAT": ("<f4", 3, None), "R32G32_FLOAT": ("<f4", 2, None), "R32_FLOAT": ("<f4", 1, None),
    "R16G16_FLOAT": ("<f2", 2, None), "R16G16B16A16_FLOAT": ("<f2", 4, None),
    "R8G8B8A8_UNORM": ("u1", 4, 255.0), "R8G8B8A8_UINT": ("u1", 4, None), "R8G8B8A8_SNORM": ("i1", 4, 127.0),
    "R16G16B16A16_SNORM": ("<i2", 4, 32767.0), "R16G16_UNORM": ("<u2", 2, 65535.0), "R16G16B16A16_UNORM": ("<u2", 4, 65535.0),
    "R32G32B32A32_UINT": ("<u4", 4, None), "R32G32_UINT": ("<u4", 2, None), "R32_UINT": ("<u4", 1, None),
}


def parse_layout(txt_path):
    elems, stride, topo, cur = [], None, None, None
    for line in Path(txt_path).read_text(errors="replace").splitlines():
        if line.startswith("vb") and ("[" in line.split(":")[0]):
            break
        if line.startswith("stride:"):
            stride = int(line.split(":")[1])
        elif line.startswith("topology:"):
            topo = line.split(":")[1].strip()
        elif line.startswith("element["):
            cur = {}
            elems.append(cur)
        elif cur is not None and ":" in line:
            k, v = [s.strip() for s in line.split(":", 1)]
            cur[k] = v
    return stride, topo, elems


def decode_elem(buf, stride, offset, fmt, count):
    if fmt not in FORMATS:
        return None
    dt, n, norm = FORMATS[fmt]
    size = np.dtype(dt).itemsize * n
    raw = np.frombuffer(buf, dtype=np.uint8)
    nvert_avail = len(raw) // stride if stride else 0
    count = min(count, nvert_avail)
    idx = (np.arange(count)[:, None] * stride + offset + np.arange(size)[None, :])
    arr = np.frombuffer(raw[idx].tobytes(), dtype=dt).reshape(count, n).astype(np.float32)
    if norm:
        arr = np.maximum(arr / norm, -1.0)
    return arr


UPDATE_RE = re.compile(r"^(?P<draw>\d{6})\.(?P<sub>\d+)-(?:update|unmap)=(?P<hash>[0-9a-f]+)@(?P<addr>[0-9A-F]+)\.buf$")


def index_updates(fa):
    """CPU-side buffer writes (UpdateSubresource / Map+Unmap) keyed by resource address: [(draw, sub, path)]"""
    idx = {}
    for p in fa.iterdir():
        m = UPDATE_RE.match(p.name)
        if m:
            idx.setdefault(m["addr"].upper(), []).append((int(m["draw"]), int(m["sub"]), p))
    for v in idx.values():
        v.sort()
    return idx


def indirect_args(updates, addr, offset, draw_no):
    """DrawIndexedInstancedIndirect args = 5 x uint32 at `offset` in the args buffer, as last written before the draw."""
    cands = [c for c in updates.get(addr.upper().lstrip("0X").zfill(16), []) if c[0] <= draw_no]
    if not cands:
        return None
    data = cands[-1][2].read_bytes()
    if len(data) < offset + 20:
        return None
    ic, inst, start, base, sinst = struct.unpack_from("<IIIiI", data, offset)
    return {"IndexCountPerInstance": ic, "InstanceCount": inst, "StartIndexLocation": start, "BaseVertexLocation": base}


def parse_log(log_path):
    """draw number -> {api, args}"""
    draws = {}
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = DRAW_RE.match(line)
            if m and "3DMigoto" not in line[:14]:
                args = {}
                for kv in m.group(3).split(","):
                    if ":" in kv:
                        k, v = kv.split(":", 1)
                        k, v = k.strip(), v.strip()
                        args[k] = int(v) if re.fullmatch(r"-?\d+", v) else v
                draws[int(m.group(1))] = {"api": m.group(2), "args": args}
    return draws


def ingest(fa_dir, out_dir, max_draws=None, min_indices=3, reference_png=None, resolve_indirect=False):
    fa, out = Path(fa_dir), Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    for sub in ("meshes", "textures", "cbuffers"):
        (out / sub).mkdir(parents=True)
    log = parse_log(fa / "log.txt")
    updates = index_updates(fa)
    by_draw = {}
    for p in fa.iterdir():
        m = FILE_RE.match(p.name)
        if m:
            by_draw.setdefault(int(m["draw"]), {}).setdefault(m["slot"], {})[m["ext"]] = p
    meshes, draws_out, textures = {}, [], {}
    mesh_cache = {}
    n_skipped = {"no_vb": 0, "no_ib": 0, "bad_layout": 0, "no_draw_args": 0}

    def save_tex(p, slot_fmt):
        h = p.name.split("=", 1)[1].split("-")[0] if "!S!" not in p.name else p.name.split("!S!=")[1].split("-")[0]
        if h in textures:
            return h
        dsc = p.with_suffix(".dsc")
        info = {"format": "", "w": 0, "h": 0}
        if dsc.exists():
            t = dsc.read_text(errors="replace")
            mf = re.search(r'format="([^"]+)"', t)
            info["format"] = mf.group(1) if mf else ""
            for k, key in (("width", "w"), ("height", "h")):
                mm = re.search(rf"{k}=(\d+)", t)
                info[key] = int(mm.group(1)) if mm else 0
            info["array"] = int(re.search(r"array=(\d+)", t).group(1)) if re.search(r"array=(\d+)", t) else 1
        rec = dict(info, id=h, file=None, dynamic=False)
        try:
            from PIL import Image
            im = Image.open(p)
            im.load()
            if min(im.size) >= 8:
                im.convert("RGBA").save(out / "textures" / f"t{h}.png")
                rec["file"] = f"textures/t{h}.png"
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:80]
        textures[h] = rec
        return h

    for dn in sorted(by_draw):
        files = by_draw[dn]
        d = log.get(dn)
        if d is None or not d["api"].startswith("DrawIndexed"):
            continue
        if "vb0" not in files or "txt" not in files["vb0"] or "buf" not in files["vb0"]:
            n_skipped["no_vb"] += 1
            continue
        if "ib" not in files or "buf" not in files["ib"]:
            n_skipped["no_ib"] += 1
            continue
        # vertex layout (elements carry InputSlot) + per-slot strides
        _, topo, elems = parse_layout(files["vb0"]["txt"])
        strides = {}
        for slot, f in files.items():
            if slot.startswith("vb") and "txt" in f and "buf" in f:
                strides[int(slot[2:])] = (parse_layout(f["txt"])[0], f["buf"])
        if not elems or not any(e.get("SemanticName") == "POSITION" for e in elems):
            n_skipped["bad_layout"] += 1
            continue
        ib_txt = files["ib"].get("txt")
        ib_fmt = "DXGI_FORMAT_R32_UINT"
        if ib_txt:
            for line in Path(ib_txt).read_text(errors="replace").splitlines()[:6]:
                if line.startswith("format:"):
                    ib_fmt = line.split(":")[1].strip()
        idx_dtype = "<u2" if "R16" in ib_fmt else "<u4"
        ib = np.fromfile(files["ib"]["buf"], dtype=idx_dtype).astype(np.int64)
        a = d["args"]
        count = a.get("IndexCount")
        start = a.get("StartIndexLocation", 0)
        base = a.get("BaseVertexLocation", 0)
        if count is None and resolve_indirect and "pBufferForArgs" in a:
            ia = indirect_args(updates, str(a["pBufferForArgs"]), int(a.get("AlignedByteOffsetForArgs", 0)), dn)
            if ia:
                count, start, base = ia["IndexCountPerInstance"], ia["StartIndexLocation"], ia["BaseVertexLocation"]
                n_skipped["indirect_resolved"] = n_skipped.get("indirect_resolved", 0) + 1
        if count is None:  # args unavailable: the whole dumped index buffer is all we know
            count, start, base = len(ib), 0, 0
            n_skipped["indirect_unresolved"] = n_skipped.get("indirect_unresolved", 0) + 1
        idx = ib[start:start + count] + base
        if len(idx) < min_indices or len(idx) % 3 or idx.min() < 0:
            continue
        vmin, vmax = int(idx.min()), int(idx.max())
        sig = (files["vb0"]["buf"].name.split("=")[1][:8], files["ib"]["buf"].name.split("=")[1][:8], start, count, base)
        mid = mesh_cache.get(sig)
        if mid is None:
            mid = f"m{len(mesh_cache):04d}"
            mesh_cache[sig] = mid
            md = out / "meshes" / mid
            md.mkdir()
            nv = vmax - vmin + 1
            (idx - vmin).astype("<u4").tofile(md / "idx.u32")
            attrs = []
            bufs = {}
            for e in elems:
                slot = int(e["InputSlot"])
                if slot not in strides or e.get("InputSlotClass") != "per-vertex":
                    continue
                stride, bpath = strides[slot]
                if bpath not in bufs:
                    bufs[bpath] = bpath.read_bytes()
                arr = decode_elem(bufs[bpath][vmin * stride:], stride, int(e["AlignedByteOffset"]), e["Format"], nv)
                if arr is None or len(arr) != nv:
                    continue
                name = f'{e["SemanticName"]}{e["SemanticIndex"]}'
                fn = f"{name}.f32"
                arr.astype("<f4").tofile(md / fn)
                attrs.append({"name": name, "file": fn, "comps": int(arr.shape[1]), "format": e["Format"], "count": nv, "perInstance": False})
            if not any(x["name"] == "POSITION0" for x in attrs):
                n_skipped["bad_layout"] += 1
                shutil.rmtree(md)
                mesh_cache.pop(sig)
                continue
            meshes[mid] = {"id": mid, "verts": nv, "indices": int(len(idx)), "topology": "Topology." + (topo or "trianglelist"), "attrs": attrs,
                           "firstEid": dn, "vmin": vmin}
        tex = []
        for slot, f in files.items():
            if slot.startswith("ps-t") and "dds" in f:
                h = save_tex(f["dds"], None)
                tex.append({"slot": int(slot[4:]), "res": h, "format": textures[h]["format"]})
        draws_out.append({"eid": dn, "mesh": mid, "name": "", "topology": "Topology.TriangleList", "numInstances": 1, "postVS": None,
                          "cbuffers": {}, "textures": tex, "vsSrvs": [], "psDiscard": False, "rts": [], "indirect": "Indirect" in d["api"]})
        if max_draws and len(draws_out) >= max_draws:
            break
    if reference_png and Path(reference_png).exists():
        shutil.copy(reference_png, out / "reference_backbuffer.png")
    pkg = {"rdc": str(fa), "api": "3DMigoto-FrameAnalysis", "source": "3dmigoto", "draws": draws_out, "meshes": meshes, "textures": textures,
           "skipped": n_skipped}
    (out / "extract.json").write_text(json.dumps(pkg, indent=1), encoding="utf-8")
    return {"draws": len(draws_out), "meshes": len(meshes), "textures": len(textures), "skipped": n_skipped}
