"""Whole-library source-mesh identification against a captured extract.

Source side: a SourceProbe `dump-index` directory (sections.tsv + index/*.u32, hashes of every source LOD
section). Capture side: the exported draw buffers; a draw's ordered index list is re-canonicalised as uint32
and hashed exactly like SourceProbe does, so a hit is byte-exact indices, not a count heuristic.

Instanced draws: the capture keeps only one canonical index buffer per mesh (instance transforms share it), so a
hash hit identifies the source section for every instance of that mesh.

Outputs scene_identity.json: per capture mesh, the source section(s) that match, plus a draw->mesh roll-up that
names each draw. `apply` then annotates scene.json meshes/instances with the source identity.
"""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def load_index(root):
    """sections.tsv -> list of rows; plus lookup by indices hash."""
    root = Path(root)
    rows = []
    tsv = root / "sections.tsv"
    with open(tsv, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != len(header):
                continue
            r = dict(zip(header, parts))
            r["lod"] = int(r["lod"])
            r["section"] = int(r["section"])
            r["indexCount"] = int(r["indexCount"])
            r["vertexCount"] = int(r["vertexCount"])
            for k in ("minX", "minY", "minZ", "maxX", "maxY", "maxZ"):
                r[k] = float(r[k])
            rows.append(r)
    by_hash = defaultdict(list)
    for r in rows:
        by_hash[r["indicesSha256"]].append(r)
    return rows, by_hash


def capture_mesh_hashes(export_dir, draws):
    """capture mesh id -> (indicesSha256, indexedPositionsSha256) from the exported idx + position attribute."""
    from .solve import Extract
    ex = Extract(export_dir)
    out = {}
    for mid, m in ex.meshes.items():
        p = ex.root / "meshes" / mid / "idx.u32"
        if not p.exists() or m["indices"] == 0 or m["indices"] % 3:
            continue
        idx = np.fromfile(p, dtype="<u4").astype(np.uint32)
        pos_i = ex.position_attr_index(mid)
        if pos_i is None:
            continue
        pos = ex.mesh_attr(mid, pos_i)[:, :3]
        if idx.max() >= len(pos):
            continue
        ref = np.ascontiguousarray(pos[idx].astype("<f4"))
        out[mid] = (hashlib.sha256(idx.tobytes()).hexdigest().upper(),
                    hashlib.sha256(ref.tobytes()).hexdigest().upper(), int(len(idx)))
    return out


def identify(export_dir, index_dir, draws=None):
    from .solve import Extract
    ex = Extract(export_dir)
    rows, by_hash = load_index(index_dir)
    draws = ex.draws if draws is None else draws
    caps = capture_mesh_hashes(export_dir, draws)
    per_mesh = {}
    for mid, (ih, ph, n) in caps.items():
        hits = [r for r in by_hash.get(ih, []) if r["indexedPositionsSha256"] == ph and r["indexCount"] == n]
        if hits:
            per_mesh[mid] = [{"package": r["package"], "sourceObject": r["sourceObject"], "archive": r["archive"],
                              "lod": r["lod"], "section": r["section"], "indexCount": r["indexCount"],
                              "level": "exact"} for r in hits]
    # roll-up: per draw, its instances, and the matched source section of the mesh it uses
    draw_rows = []
    for d in draws:
        hit = per_mesh.get(d["mesh"])
        draw_rows.append({
            "eid": d["eid"], "mesh": d["mesh"], "instances": max(1, int(d.get("numInstances") or 1)),
            "numIndices": ex.meshes[d["mesh"]]["indices"],
            "source": ({"package": hit[0]["package"], "sourceObject": hit[0]["sourceObject"], "lod": hit[0]["lod"],
                        "section": hit[0]["section"], "candidates": len(hit)} if hit else None),
        })
    unique_sources = len({h["sourceObject"] for hs in per_mesh.values() for h in hs})
    draws_matched = sum(1 for r in draw_rows if r["source"])
    return {
        "index": str(Path(index_dir).resolve()), "sourceSections": len(rows),
        "capturedMeshes": len(caps), "matchedMeshes": len(per_mesh),
        "draws": len(draw_rows), "drawsMatched": draws_matched,
        "uniqueSourceObjects": unique_sources,
        "meshes": per_mesh, "drawRows": draw_rows,
        "scope": "Exact ordered-index and indexed-position SHA-256 equality between the capture's index buffer and "
                 "SourceProbe source LOD sections. Full-mesh identity only (draws of a sub-section of a larger source "
                 "mesh, e.g. instanced array meshes, are reported as unmatched, not approximated).",
    }


def identify_draws(ids_json, index_dir):
    """Draw-level identity from rf_extract's `ids` mode (no geometry export needed).

    Every triangle-list draw's ordered index list is hashed exactly like SourceProbe does; a hit names the source
    LOD section for that draw, which is per-draw even when a mesh's canonical index buffer covers several draws.
    Instance transforms still come from a later export/build of the same draw.
    """
    ids = json.loads(Path(ids_json).read_text(encoding="utf-8"))
    rows, by_hash = load_index(index_dir)
    out = []
    for d in ids["draws"]:
        row = {"eid": d["eid"], "mesh": None, "instances": max(1, int(d["numInstances"])),
               "numIndices": d["numIndices"], "source": None}
        if d["topology"].endswith("TriangleList") and d["numIndices"] % 3 == 0 and d["indices"] is not None:
            arr = np.ascontiguousarray(np.asarray(d["indices"], dtype="<u4"))
            hits = by_hash.get(hashlib.sha256(arr.tobytes()).hexdigest().upper(), [])
            if hits:
                row["source"] = {"package": hits[0]["package"], "sourceObject": hits[0]["sourceObject"],
                                 "lod": hits[0]["lod"], "section": hits[0]["section"],
                                 "indexCount": hits[0]["indexCount"], "candidates": len(hits)}
        out.append(row)
    matched = [r for r in out if r["source"]]
    return {
        "ids": str(Path(ids_json).resolve()), "index": str(Path(index_dir).resolve()),
        "sourceSections": len(rows), "draws": len(out), "drawsMatched": len(matched),
        "instances": sum(r["instances"] for r in out), "instancesMatched": sum(r["instances"] for r in matched),
        "uniqueSourceObjects": len({r["source"]["sourceObject"] for r in matched}),
        "drawRows": out,
        "scope": "Per-draw exact ordered-index SHA-256 equality against source LOD sections. Naming is per draw; "
                 "instance matrices require a build of the same draws.",
    }


def apply_draws(scene_path, draw_identity):
    """Attach per-draw source identity to scene instances (eid -> source), and name meshes only when every draw
    that uses that mesh agrees on one source section."""
    scene = json.loads(Path(scene_path).read_text(encoding="utf-8"))
    by_eid = {r["eid"]: r["source"] for r in draw_identity["drawRows"] if r["source"]}
    for inst in scene["instances"]:
        src = by_eid.get(inst["eid"])
        if src:
            inst["source"] = {"object": src["sourceObject"], "lod": src["lod"], "section": src["section"]}
    per_mesh = defaultdict(set)
    for inst in scene["instances"]:
        if "source" in inst:
            per_mesh[inst["mesh"]].add((inst["source"]["object"], inst["source"]["lod"], inst["source"]["section"]))
    named = 0
    for mid, srcs in per_mesh.items():
        if len(srcs) == 1 and mid in scene["meshes"]:
            o, lod, sec = next(iter(srcs))
            scene["meshes"][mid]["source"] = {"object": o, "lod": lod, "section": sec, "level": "exact"}
            named += 1
    scene["identity"] = {"mode": "draw", "drawsMatched": draw_identity["drawsMatched"], "draws": draw_identity["draws"],
                         "instancesMatched": draw_identity["instancesMatched"], "instances": draw_identity["instances"],
                         "uniqueSourceObjects": draw_identity["uniqueSourceObjects"], "meshesNamed": named}
    Path(scene_path).write_text(json.dumps(scene, indent=1), encoding="utf-8")
    return scene["identity"]


def identify_export(export_dir, index_dir, draws=None):
    """Per-draw identity for an already-exported extract, using each draw's own index slice when the export
    recorded one (draws sharing an index buffer), else the mesh's canonical buffer.

    This is what makes instanced draws and multi-draw meshes identifiable per draw, which a whole-mesh hash cannot do.
    """
    from .solve import Extract
    ex = Extract(export_dir)
    rows, by_hash = load_index(index_dir)
    draws = ex.draws if draws is None else draws
    out = []
    for d in draws:
        m = ex.meshes[d["mesh"]]
        own = d.get("ownIndices")
        if own is not None:
            arr = np.ascontiguousarray(np.asarray(own, dtype="<u4"))
        else:
            idxp = ex.root / "meshes" / d["mesh"] / "idx.u32"
            arr = np.ascontiguousarray(np.fromfile(idxp, dtype="<u4")) if idxp.exists() else None
        row = {"eid": d["eid"], "mesh": d["mesh"], "instances": max(1, int(d.get("numInstances") or 1)),
               "numIndices": m["indices"], "source": None}
        if arr is not None and m["topology"].endswith("TriangleList") and len(arr) % 3 == 0:
            hits = by_hash.get(hashlib.sha256(arr.tobytes()).hexdigest().upper(), [])
            if hits:
                row["source"] = {"package": hits[0]["package"], "sourceObject": hits[0]["sourceObject"],
                                 "lod": hits[0]["lod"], "section": hits[0]["section"],
                                 "indexCount": hits[0]["indexCount"], "candidates": len(hits)}
        out.append(row)
    matched = [r for r in out if r["source"]]
    return {
        "export": str(Path(export_dir).resolve()), "index": str(Path(index_dir).resolve()),
        "sourceSections": len(rows), "mode": "per-draw", "draws": len(out), "drawsMatched": len(matched),
        "instances": sum(r["instances"] for r in out), "instancesMatched": sum(r["instances"] for r in matched),
        "uniqueSourceObjects": len({r["source"]["sourceObject"] for r in matched}),
        "drawRows": out,
        "scope": "Per-draw exact ordered-index SHA-256 equality against source LOD sections; the draw's own index "
                 "slice is used when recorded, otherwise the mesh's canonical index buffer.",
    }


def apply(scene_path, identity, matched_only=True):
    """Annotate scene.json meshes (and their instances) with the matched source identity."""
    scene = json.loads(Path(scene_path).read_text(encoding="utf-8"))
    meshes = identity["meshes"]
    if isinstance(scene["meshes"], dict):
        for mid, hits in meshes.items():
            if mid in scene["meshes"]:
                h = hits[0]
                scene["meshes"][mid]["source"] = {"package": h["package"], "object": h["sourceObject"],
                                                  "lod": h["lod"], "section": h["section"],
                                                  "candidates": len(hits), "level": "exact"}
    by_mesh = {mid: hits[0] for mid, hits in meshes.items()}
    for inst in scene["instances"]:
        h = by_mesh.get(inst["mesh"])
        if h:
            inst["source"] = {"object": h["sourceObject"], "lod": h["lod"], "section": h["section"]}
    scene["identity"] = {"matchedMeshes": identity["matchedMeshes"], "capturedMeshes": identity["capturedMeshes"],
                         "drawsMatched": identity["drawsMatched"], "draws": identity["draws"],
                         "uniqueSourceObjects": identity["uniqueSourceObjects"]}
    Path(scene_path).write_text(json.dumps(scene, indent=1), encoding="utf-8")
    return scene["identity"]
