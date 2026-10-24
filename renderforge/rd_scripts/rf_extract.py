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
    bufinfo = {str(b.resourceId): b for b in ctrl.GetBuffers()}

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

    def indices_for(st, a, allow_instanced=True):
        ib = st.GetIBuffer()
        indexed = bool(a.flags & rd.ActionFlags.Indexed)
        if not indexed:
            return None, None, list(range(a.numIndices)), a.vertexOffset
        # Instanced instancing may draw a range of a bigger index buffer (ShowAll draws) or read parameters
        # written by the GPU; one canonical buffer per mesh would then be the wrong identity. Opt out.
        if a.numInstances > 0 and not allow_instanced:
            return None, None, None, a.baseVertex
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

    if JOB["mode"] == "ids":
        # Draw identity without geometry: ordered indices per draw, the draw's position fetch, instance count.
        # Enough to hash a draw's index list for source-mesh identification without exporting vertex data.
        rows = []
        for a, path in draws:
            try:
                ctrl.SetFrameEvent(a.eventId, True)
                st = ctrl.GetPipelineState()
                ib, istride, idx, basev = indices_for(st, a)
                vins = st.GetVertexInputs()
                vbs = st.GetVBuffers()
                posl = None
                for v in vins:
                    if v.name == "ATTRIBUTE0" and v.vertexBuffer < len(vbs):
                        vb = vbs[v.vertexBuffer]
                        posl = {"vb": rid(vb.resourceId), "vbOff": vb.byteOffset, "vbStride": vb.byteStride,
                                "off": v.byteOffset, "fmt": fmt_name(v.format), "perInstance": bool(v.perInstance),
                                "comps": v.format.compCount, "compBytes": v.format.compByteWidth,
                                "compType": str(v.format.compType)}
                        break
                rows.append({"eid": a.eventId, "path": path, "numIndices": a.numIndices,
                             "numInstances": a.numInstances, "indexed": bool(a.flags & rd.ActionFlags.Indexed),
                             "topology": str(st.GetPrimitiveTopology()),
                             "ib": rid(ib.resourceId) if ib else None,
                             "ibOff": ib.byteOffset if ib else None, "ibStride": istride,
                             "indexOffset": a.indexOffset, "baseVertex": basev, "vertexOffset": a.vertexOffset,
                             "indices": idx, "position": posl})
            except Exception:
                log("ids fail eid", a.eventId, traceback.format_exc())
        json.dump({"rdc": JOB["rdc"], "api": str(api), "draws": rows},
                  open(os.path.join(OUT, "ids.json"), "w"), indent=1)
        log("ids done", len(rows))
        return ctrl, cap

    # ---------- export ----------
    want = None
    if JOB.get("eids"):
        want = set(JOB["eids"])
    elif JOB.get("range"):
        lo, hi = JOB.get("range")[:2]
        want = set(a.eventId for a, _ in draws if lo <= a.eventId <= hi)
    # A draw with no render targets still contributes when it is explicitly asked for (depth-only passes, or a
    # caller that selected it by its depth attachment).
    no_rt = set(JOB.get("no_rt_eids") or [])
    tex_dir = os.path.join(OUT, "textures")
    mesh_dir = os.path.join(OUT, "meshes")
    cb_dir = os.path.join(OUT, "cbuffers")
    srv_dir = os.path.join(OUT, "srv")
    for d_ in (tex_dir, mesh_dir, cb_dir, srv_dir):
        os.makedirs(d_, exist_ok=True)

    saved_tex = {}
    mesh_cache = {}
    draws_of_mesh = {}
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

    def save_texture(resid, force=False):
        key = rid(resid)
        if key in saved_tex:
            rec = saved_tex[key]
            if not force or (rec and rec.get("file")):
                return rec
        t = texinfo.get(key)
        rec = None
        if t is not None:
            # `force` = the caller proved this is a content input of the pass being exported (render-target
            # feedback); it is written even though it is also a target elsewhere in the frame.
            dyn = is_dynamic(resid) and not force
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

    ps_discard = {}

    def pixel_shader_discards(st):
        """True if the bound pixel shader contains a discard/clip (alpha-tested / masked material)."""
        sid = rid(st.GetShader(rd.ShaderStage.Pixel))
        if sid in ps_discard:
            return ps_discard[sid]
        res_ = False
        try:
            refl = st.GetShaderReflection(rd.ShaderStage.Pixel)
            if refl is not None:
                targets = ctrl.GetDisassemblyTargets(True)
                txt = ctrl.DisassembleShader(st.GetGraphicsPipelineObject(), refl, targets[0])
                res_ = "discard" in txt
        except Exception:
            log("disasm fail", sid, traceback.format_exc())
        ps_discard[sid] = res_
        return res_

    def export_draw(a, path):
        ctrl.SetFrameEvent(a.eventId, True)
        st = ctrl.GetPipelineState()
        allow_inst = not JOB.get("no_instanced_index")
        ib, istride, idx, basev = indices_for(st, a, allow_inst)
        if idx is None:
            return {"eid": a.eventId, "mesh": None, "skipped": "instanced index range not canonical"}
        if not idx:
            return None
        # For an instanced draw, idx is the whole index buffer, not this draw's range: the vertex range must come
        # from the draw action, not from min/max over idx.
        if a.numInstances > 0 and not allow_inst:
            mn, nverts = a.vertexOffset, a.numIndices
        else:
            mn, mx = min(idx) + basev, max(idx) + basev
            nverts = mx - mn + 1
        if nverts <= 0 or nverts > 5000000:
            return None
        vbs = st.GetVBuffers()
        vins = st.GetVertexInputs()
        sig = (rid(ib.resourceId) if ib else "", ib.byteOffset if ib else 0, a.indexOffset, a.numIndices, basev,
               a.vertexOffset if (a.numInstances > 0 and not allow_inst) else 0,
               tuple((rid(vbs[v.vertexBuffer].resourceId), vbs[v.vertexBuffer].byteOffset, v.byteOffset,
                      vbs[v.vertexBuffer].byteStride, fmt_name(v.format), bool(v.perInstance)) for v in vins))
        mid = mesh_cache.get(sig)
        topo = str(st.GetPrimitiveTopology())
        if mid is None:
            mid = "m%04d" % len(mesh_cache)
            mesh_cache[sig] = mid
            md = os.path.join(mesh_dir, mid)
            os.makedirs(md, exist_ok=True)
            # Whole-IB canonical buffer: every draw that shares the buffer gets the same normalized indices, so
            # each draw's own index slice (read back from the draw) is the exact identity token.
            iarr = array("I", [i + basev - mn for i in idx])
            with open(os.path.join(md, "idx.u32"), "wb") as f:
                f.write(iarr.tobytes())
            draws_of_mesh[mid] = {"vmin": mn, "nverts": nverts}
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
                           "firstEid": a.eventId, "vmin": mn}
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
                texs.append({"slot": u.access.index, "res": rid(r_), "format": fmt_name(u.descriptor.format),
                             "view": fmt_name(u.descriptor.format) if u.descriptor.format is not None else None})
                save_texture(r_)
            # render targets the pass samples are inputs too (read-back / refraction / distortion sources)
            if JOB.get("save_rt_inputs"):
                for o in a.outputs:
                    if o != rd.ResourceId.Null():
                        save_texture(o, force=True)
        except Exception:
            log("tex fail eid", a.eventId, traceback.format_exc())
        # vertex-shader buffer SRVs (manual vertex fetch: UVs / tangents often live here)
        srvs = []
        try:
            for u in st.GetReadOnlyResources(rd.ShaderStage.Vertex):
                dsc = u.descriptor
                r_ = dsc.resource
                if r_ == rd.ResourceId.Null() or rid(r_) not in bufinfo:
                    continue
                b = bufinfo[rid(r_)]
                off = dsc.byteOffset
                size = dsc.byteSize or (b.length - off)
                rec = {"slot": u.access.index, "res": rid(r_), "off": off, "size": size,
                       "format": fmt_name(dsc.format), "elemBytes": getattr(dsc, "elementByteSize", 0), "file": None}
                if 0 < size <= JOB.get("max_srv_bytes", 64 * 1024 * 1024):
                    fn = "srv_%s_%d_%d.bin" % (rid(r_).split("::")[-1], off, size)
                    fp = os.path.join(srv_dir, fn)
                    if not os.path.exists(fp):
                        with open(fp, "wb") as f:
                            f.write(ctrl.GetBufferData(r_, off, size))
                    rec["file"] = "srv/" + fn
                srvs.append(rec)
        except Exception:
            log("srv fail eid", a.eventId, traceback.format_exc())
        outs = [o for o in a.outputs if o != rd.ResourceId.Null()]
        info = draws_of_mesh[mid]
        rts = [rid(o) for o in outs]
        own = [i + basev - info["vmin"] for i in idx[a.indexOffset:a.indexOffset + a.numIndices]] if (
            a.numInstances == 0 and len(idx) > a.numIndices) else None
        return {"eid": a.eventId, "mesh": mid, "vsSrvs": srvs, "psDiscard": pixel_shader_discards(st), "name": a.GetName(sf), "path": path, "topology": topo,
                "numInstances": a.numInstances, "postVS": post, "cbuffers": cbs, "textures": texs,
                "vmin": info["vmin"], "nverts": info["nverts"], "ownIndices": own,
                "vs": rid(st.GetShader(rd.ShaderStage.Vertex)), "ps": rid(st.GetShader(rd.ShaderStage.Pixel)),
                # the first output is the scene colour; extra outputs mark which extra buffers a pass writes
                "rts": rts, "writes_extra": [fmt_name(texinfo[r].format) for r in rts[1:] if r in texinfo]}

    n_ok = 0
    n_inst_sel = 0
    for a, path in draws:
        if want is not None and a.eventId not in want and a.eventId not in no_rt:
            continue
        try:
            d = export_draw(a, path)
            if d and d.get("mesh"):
                out_draws.append(d)
                n_ok += 1
            elif d:
                log("export skip eid", a.eventId, d.get("skipped"))
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
        json.dump({"rdc": JOB["rdc"], "api": str(api), "draws": out_draws, "meshes": meshes, "textures": saved_tex,
                   "flags": {k: JOB[k] for k in ("no_instanced_index",) if k in JOB}}, f, indent=1)
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
