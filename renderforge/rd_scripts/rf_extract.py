# Runs INSIDE qrenderdoc (RenderDoc embedded Python 3.6, stdlib only):
#   qrenderdoc.exe --python rf_extract.py     with env RENDERFORGE_JOB=<job.json>
# job.json: {"mode": "survey"|"export", "rdc": "...", "out": "...", "eids": [..] | "range": [a,b], ...}
import os, sys, json, struct, hashlib, traceback
from array import array

JOB = json.load(open(os.environ["RENDERFORGE_JOB"], encoding="utf-8"))
OUT = JOB["out"]
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "extract.log"), "a", encoding="utf-8")


def log(*a):
    LOG.write(" ".join(str(x) for x in a) + "\n")
    LOG.flush()


def main():
    import renderdoc as rd

    cap = rd.OpenCaptureFile()
    r = cap.OpenFile(JOB["rdc"], "", None)
    if r != rd.ResultCode.Succeeded:
        raise RuntimeError("OpenFile failed: %s" % r)
    res, ctrl = cap.OpenCapture(rd.ReplayOptions(), None)
    if res != rd.ResultCode.Succeeded:
        raise RuntimeError("OpenCapture failed: %s" % res)
    sf = ctrl.GetStructuredFile()
    api = ctrl.GetAPIProperties().pipelineType
    log("opened", JOB["rdc"], "api", api)

    texinfo = {str(t.resourceId): t for t in ctrl.GetTextures()}

    def walk(a, path):
        name = a.GetName(sf)
        yield a, path
        for c in a.children:
            for x in walk(c, path + [name] if name else path):
                yield x

    draws = []
    for root in ctrl.GetRootActions():
        for a, path in walk(root, []):
            if a.flags & rd.ActionFlags.Drawcall and a.numIndices >= 3:
                draws.append((a, path))
    log("draws", len(draws))

    # ---------- helpers ----------
    def rid(x):
        return str(x)

    def fmt_name(f):
        return f.Name()

    def decode_plan(fmt):
        """Return (struct_fmt, ncomp, comptype) for a vertex format, or None."""
        n, w, ct = fmt.compCount, fmt.compByteWidth, fmt.compType
        if fmt.type == rd.ResourceFormatType.R10G10B10A2:
            return ("10-10-10-2", 4, ct)
        if fmt.type != rd.ResourceFormatType.Regular:
            return None
        C = rd.CompType
        if w == 1:
            ch = "B" if ct in (C.UNorm, C.UInt, C.UScaled) else "b"
        elif w == 2:
            ch = "e" if ct == C.Float else ("H" if ct in (C.UNorm, C.UInt, C.UScaled) else "h")
        elif w == 4:
            ch = "f" if ct == C.Float else ("I" if ct in (C.UInt, C.UScaled) else "i")
        else:
            return None
        return ("<" + ch * n, n, ct)

    def norm_vals(vals, w, ct):
        C = rd.CompType
        if ct == C.UNorm:
            m = float((1 << (8 * w)) - 1)
            return [v / m for v in vals]
        if ct == C.SNorm:
            m = float((1 << (8 * w - 1)) - 1)
            return [max(v / m, -1.0) for v in vals]
        return [float(v) for v in vals]

    def decode_attr(data, base, stride, count, fmt):
        plan = decode_plan(fmt)
        if plan is None:
            return None, 0
        sfmt, n, ct = plan
        out = array("f")
        bgra = fmt.BGRAOrder()
        if sfmt == "10-10-10-2":
            for i in range(count):
                o = base + i * stride
                if o + 4 > len(data):
                    out.extend([0.0] * 4)
                    continue
                (v,) = struct.unpack_from("<I", data, o)
                x, y, z, w2 = v & 1023, (v >> 10) & 1023, (v >> 20) & 1023, (v >> 30) & 3
                if ct == rd.CompType.UNorm:
                    row = [x / 1023.0, y / 1023.0, z / 1023.0, w2 / 3.0]
                elif ct == rd.CompType.SNorm:
                    row = [max(((q ^ 512) - 512) / 511.0, -1.0) for q in (x, y, z)] + [float(w2)]
                else:
                    row = [float(x), float(y), float(z), float(w2)]
                out.extend(row)
            return out, 4
        w = fmt.compByteWidth
        size = struct.calcsize(sfmt)
        for i in range(count):
            o = base + i * stride
            if o + size > len(data):
                out.extend([0.0] * n)
                continue
            vals = struct.unpack_from(sfmt, data, o)
            if ct != rd.CompType.Float:
                vals = norm_vals(vals, w, ct)
            if bgra and n >= 3:
                vals = list(vals)
                vals[0], vals[2] = vals[2], vals[0]
            out.extend(vals)
        return out, n

    def indices_for(st, a):
        ib = st.GetIBuffer()
        indexed = bool(a.flags & rd.ActionFlags.Indexed)
        if not indexed:
            return None, None, list(range(a.numIndices)), a.vertexOffset
        stride = ib.byteStride or 2
        off = ib.byteOffset + a.indexOffset * stride
        raw = ctrl.GetBufferData(ib.resourceId, off, a.numIndices * stride)
        cnt = len(raw) // stride
        idx = list(struct.unpack("<%d%s" % (cnt, "H" if stride == 2 else "I"), raw[:cnt * stride]))
        return ib, stride, idx, a.baseVertex

    # ---------- survey ----------
    def draw_summary(a, path):
        ctrl.SetFrameEvent(a.eventId, True)
        st = ctrl.GetPipelineState()
        outs = [o for o in a.outputs if o != rd.ResourceId.Null()]
        d = {
            "eid": a.eventId,
            "name": a.GetName(sf),
            "path": path,
            "indexed": bool(a.flags & rd.ActionFlags.Indexed),
            "numIndices": a.numIndices,
            "numInstances": a.numInstances,
            "topology": str(st.GetPrimitiveTopology()),
            "rts": [rid(o) for o in outs],
            "rtFormats": [],
            "depth": rid(st.GetDepthTarget().resource),
            "vs": rid(st.GetShader(rd.ShaderStage.Vertex)),
            "ps": rid(st.GetShader(rd.ShaderStage.Pixel)),
        }
        for o in outs:
            t = texinfo.get(rid(o))
            d["rtFormats"].append(fmt_name(t.format) if t else "?")
        d["rtSize"] = [texinfo[rid(outs[0])].width, texinfo[rid(outs[0])].height] if outs and rid(outs[0]) in texinfo else None
        d["ib"] = rid(st.GetIBuffer().resourceId)
        d["vbs"] = [rid(v.resourceId) for v in st.GetVBuffers() if v.resourceId != rd.ResourceId.Null()]
        d["vin"] = ["%s:%d+%d:%s" % (v.name, v.vertexBuffer, v.byteOffset, fmt_name(v.format)) for v in st.GetVertexInputs()]
        tex = []
        try:
            for u in st.GetReadOnlyResources(rd.ShaderStage.Pixel):
                if u.descriptor.resource != rd.ResourceId.Null():
                    tex.append(rid(u.descriptor.resource))
        except Exception:
            pass
        d["psTextures"] = tex
        return d

    if JOB["mode"] == "survey":
        rows = []
        for a, path in draws:
            try:
                rows.append(draw_summary(a, path))
            except Exception:
                log("survey fail eid", a.eventId, traceback.format_exc())
        json.dump({"rdc": JOB["rdc"], "api": str(api), "draws": rows},
                  open(os.path.join(OUT, "survey.json"), "w"), indent=1)
        log("survey done", len(rows))
        return ctrl, cap

    # ---------- export ----------
    want = None
    if JOB.get("eids"):
        want = set(JOB["eids"])
    elif JOB.get("range"):
        lo, hi = JOB["range"]
        want = set(a.eventId for a, _ in draws if lo <= a.eventId <= hi)
    tex_dir = os.path.join(OUT, "textures")
    mesh_dir = os.path.join(OUT, "meshes")
    cb_dir = os.path.join(OUT, "cbuffers")
    for d_ in (tex_dir, mesh_dir, cb_dir):
        os.makedirs(d_, exist_ok=True)

    saved_tex = {}
    mesh_cache = {}
    meshes = {}
    out_draws = []

    WRITE_USAGES = [getattr(rd.ResourceUsage, n) for n in (
        "ColorTarget", "DepthStencilTarget", "Copy", "CopyDst", "Resolve", "ResolveDst", "Clear",
        "GenMips", "CS_RWResource", "PS_RWResource") if hasattr(rd.ResourceUsage, n)]

    def is_dynamic(resid):
        try:
            for u in ctrl.GetUsage(resid):
                if u.usage in WRITE_USAGES and u.usage != getattr(rd.ResourceUsage, "Copy", None):
                    return True
        except Exception:
            pass
        return False

    def save_texture(resid):
        key = rid(resid)
        if key in saved_tex:
            return saved_tex[key]
        t = texinfo.get(key)
        rec = None
        if t is not None:
            dyn = is_dynamic(resid)
            rec = {"id": key, "w": t.width, "h": t.height, "mips": t.mips, "format": fmt_name(t.format),
                   "cube": bool(t.cubemap), "arr": t.arraysize, "dynamic": dyn, "file": None}
            if not dyn and not t.cubemap and t.arraysize <= 1 and t.width * t.height <= JOB.get("max_tex_pixels", 8192 * 8192):
                ts = rd.TextureSave()
                ts.resourceId = resid
                ts.mip = 0
                ts.slice.sliceIndex = 0
                ts.alpha = rd.AlphaMapping.Preserve
                ts.destType = rd.FileType.PNG
                fn = "t%s.png" % key.split("::")[-1]
                try:
                    ctrl.SaveTexture(ts, os.path.join(tex_dir, fn))
                    rec["file"] = "textures/" + fn
                except Exception:
                    log("save tex fail", key, traceback.format_exc())
        saved_tex[key] = rec
        return rec

    def save_cb(data):
        h = hashlib.sha1(data).hexdigest()[:16]
        p = os.path.join(cb_dir, h + ".bin")
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(data)
        return h

    def export_draw(a, path):
        ctrl.SetFrameEvent(a.eventId, True)
        st = ctrl.GetPipelineState()
        ib, istride, idx, basev = indices_for(st, a)
        if not idx:
            return None
        mn, mx = min(idx) + basev, max(idx) + basev
        nverts = mx - mn + 1
        if nverts <= 0 or nverts > 5000000:
            return None
        vbs = st.GetVBuffers()
        vins = st.GetVertexInputs()
        sig = (rid(ib.resourceId) if ib else "", ib.byteOffset if ib else 0, a.indexOffset, a.numIndices, basev,
               tuple((rid(vbs[v.vertexBuffer].resourceId), vbs[v.vertexBuffer].byteOffset, v.byteOffset,
                      vbs[v.vertexBuffer].byteStride, fmt_name(v.format)) for v in vins))
        mid = mesh_cache.get(sig)
        topo = str(st.GetPrimitiveTopology())
        if mid is None:
            mid = "m%04d" % len(mesh_cache)
            mesh_cache[sig] = mid
            md = os.path.join(mesh_dir, mid)
            os.makedirs(md, exist_ok=True)
            iarr = array("I", [i + basev - mn for i in idx])
            with open(os.path.join(md, "idx.u32"), "wb") as f:
                f.write(iarr.tobytes())
            attrs = []
            vbdata = {}
            seen = {}
            for v in vins:
                vb = vbs[v.vertexBuffer]
                if vb.resourceId == rd.ResourceId.Null():
                    continue
                k = (v.vertexBuffer, v.byteOffset, fmt_name(v.format), bool(v.perInstance))
                if k in seen:
                    s = seen[k]
                    attrs.append({"name": v.name, "file": s["file"], "comps": s["comps"], "format": s["format"],
                                  "count": s["count"], "perInstance": s["perInstance"]})
                    continue
                bk = (v.vertexBuffer, bool(v.perInstance))
                if bk not in vbdata:
                    if v.perInstance:
                        lo, ln = vb.byteOffset, max(vb.byteStride, 64)
                    else:
                        lo = vb.byteOffset + mn * vb.byteStride
                        ln = nverts * vb.byteStride if vb.byteStride else 4096
                    vbdata[bk] = ctrl.GetBufferData(vb.resourceId, lo, ln)
                data = vbdata[bk]
                cnt = 1 if v.perInstance else nverts
                arr, comps = decode_attr(data, v.byteOffset, vb.byteStride, cnt, v.format)
                if arr is None:
                    log("unsupported fmt", fmt_name(v.format), "eid", a.eventId)
                    continue
                fn = "%s_a%d.f32" % (v.name, len(attrs))
                with open(os.path.join(md, fn), "wb") as f:
                    f.write(arr.tobytes())
                rec = {"name": v.name, "file": fn, "comps": comps, "format": fmt_name(v.format), "count": cnt,
                       "perInstance": bool(v.perInstance)}
                attrs.append(rec)
                seen[k] = rec
            meshes[mid] = {"id": mid, "verts": nverts, "indices": len(idx), "topology": topo, "attrs": attrs,
                           "firstEid": a.eventId}
        # post-VS positions for this draw (instance 0)
        post = None
        try:
            mf = ctrl.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mf.vertexResourceId != rd.ResourceId.Null():
                raw = ctrl.GetBufferData(mf.vertexResourceId, mf.vertexByteOffset, 0)
                stride = mf.vertexByteStride
                rows = len(raw) // stride if stride else 0
                pos = array("f")
                for i in range(rows):
                    pos.extend(struct.unpack_from("<4f", raw, i * stride))
                fn = "vsout_%d.f32" % a.eventId
                with open(os.path.join(mesh_dir, mid, fn), "wb") as f:
                    f.write(pos.tobytes())
                post = {"file": "meshes/%s/%s" % (mid, fn), "rows": rows, "stride": stride,
                        "baseVertex": mf.baseVertex, "numIndices": mf.numIndices,
                        "indexRes": rid(mf.indexResourceId), "indexOff": mf.indexByteOffset}
        except Exception:
            log("postvs fail eid", a.eventId, traceback.format_exc())
        # constant buffers
        cbs = {}
        for stage, sname in ((rd.ShaderStage.Vertex, "vs"), (rd.ShaderStage.Pixel, "ps")):
            try:
                refl = st.GetShaderReflection(stage)
                if refl is None:
                    continue
                for i, blk in enumerate(refl.constantBlocks):
                    u = st.GetConstantBlock(stage, i, 0)
                    d = u.descriptor
                    if d.resource == rd.ResourceId.Null():
                        continue
                    data = ctrl.GetBufferData(d.resource, d.byteOffset, d.byteSize or blk.byteSize)
                    cbs["%s%d" % (sname, blk.fixedBindNumber)] = {
                        "hash": save_cb(data), "size": len(data), "res": rid(d.resource), "off": d.byteOffset}
            except Exception:
                log("cb fail eid", a.eventId, traceback.format_exc())
        # textures bound to PS
        texs = []
        try:
            for u in st.GetReadOnlyResources(rd.ShaderStage.Pixel):
                r_ = u.descriptor.resource
                if r_ == rd.ResourceId.Null():
                    continue
                texs.append({"slot": u.access.index, "res": rid(r_), "format": fmt_name(u.descriptor.format)})
                save_texture(r_)
        except Exception:
            log("tex fail eid", a.eventId, traceback.format_exc())
        outs = [o for o in a.outputs if o != rd.ResourceId.Null()]
        return {"eid": a.eventId, "mesh": mid, "name": a.GetName(sf), "path": path, "topology": topo,
                "numInstances": a.numInstances, "postVS": post, "cbuffers": cbs, "textures": texs,
                "vs": rid(st.GetShader(rd.ShaderStage.Vertex)), "ps": rid(st.GetShader(rd.ShaderStage.Pixel)),
                "rts": [rid(o) for o in outs]}

    n_ok = 0
    for a, path in draws:
        if want is not None and a.eventId not in want:
            continue
        try:
            d = export_draw(a, path)
            if d:
                out_draws.append(d)
                n_ok += 1
        except Exception:
            log("export fail eid", a.eventId, traceback.format_exc())
    # reference backbuffer
    try:
        sw = [t for t in ctrl.GetTextures() if t.creationFlags & rd.TextureCategory.SwapBuffer]
        if sw:
            ctrl.SetFrameEvent(draws[-1][0].eventId, True)
            ts = rd.TextureSave()
            ts.resourceId = sw[0].resourceId
            ts.mip = 0
            ts.destType = rd.FileType.PNG
            ts.alpha = rd.AlphaMapping.Discard
            ctrl.SaveTexture(ts, os.path.join(OUT, "reference_backbuffer.png"))
    except Exception:
        log("backbuffer save fail", traceback.format_exc())
    with open(os.path.join(OUT, "extract.json"), "w") as f:
        json.dump({"rdc": JOB["rdc"], "api": str(api), "draws": out_draws, "meshes": meshes, "textures": saved_tex}, f, indent=1)
    log("export done draws=%d meshes=%d textures=%d" % (n_ok, len(meshes), len(saved_tex)))
    return ctrl, cap


try:
    ctrl_cap = main()
    try:
        ctrl_cap[0].Shutdown()
        ctrl_cap[1].Shutdown()
    except Exception:
        pass
except Exception:
    log(traceback.format_exc())
LOG.close()
os._exit(0)
