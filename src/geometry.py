class _GeometryMixin:
    """Section plane, GPU clip, boolean cut and the exact (OpenCascade) section and hatch."""

    def _tilt_text(self):
        ta, tb = self.spin_tilt_a.value(), self.spin_tilt_b.value()
        if abs(ta) < 1e-9 and abs(tb) < 1e-9:
            return ""
        return "_tilt_%g_%g" % (ta, tb)

    def reset_tilt(self):
        for sp in (self.spin_tilt_a, self.spin_tilt_b):
            sp.blockSignals(True)
            sp.setValue(0.0)
            sp.blockSignals(False)
        self.schedule_update()

    def get_plane(self):
        """Returns (plane_type, normal, plane_base, center_pos).

        The plane goes through the bounding-box centre moved by `offset` along its normal.
        The tilt angles turn it about the two in-plane axes of the chosen base plane; the
        resulting in-plane frame is kept in self._plane_frame = (u, w, normal, d).
        """
        offset_val = self.offset_spin.value()
        c = self.bbox.Center
        if self.radio_yz.isChecked():
            ptype, n0, u0, w0 = "YZ", App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)
        elif self.radio_xz.isChecked():
            ptype, n0, u0, w0 = "XZ", App.Vector(0, 1, 0), App.Vector(1, 0, 0), App.Vector(0, 0, 1)
        else:
            ptype, n0, u0, w0 = "XY", App.Vector(0, 0, 1), App.Vector(1, 0, 0), App.Vector(0, 1, 0)
        ta, tb = self.spin_tilt_a.value(), self.spin_tilt_b.value()
        names = {"YZ": ("Y", "Z"), "XZ": ("X", "Z"), "XY": ("X", "Y")}[ptype]
        self.lbl_tilt_a.setText("Tilt %s:" % names[0])
        self.lbl_tilt_b.setText("Tilt %s:" % names[1])
        n, u, w = App.Vector(n0), App.Vector(u0), App.Vector(w0)
        if abs(ta) > 1e-9 or abs(tb) > 1e-9:
            rot = App.Rotation(w0, tb).multiply(App.Rotation(u0, ta))
            n, u, w = rot.multVec(n0), rot.multVec(u0), rot.multVec(w0)
        plane_base = App.Vector(c.x + n.x * offset_val, c.y + n.y * offset_val, c.z + n.z * offset_val)
        self._plane_w0 = w0
        self._plane_frame = (u, w, n, n.dot(plane_base))
        center_pos = plane_base.dot(n0)                      # position along the base axis
        return ptype, n, plane_base, center_pos

    def set_clip(self, normal, plane_base, flip=False):
        """Position the GPU clip plane (cheap). flip=True keeps the other half."""
        d = normal.dot(plane_base)
        n = App.Vector(normal)
        if CLIP_KEEP_NEGATIVE != bool(flip):
            n = App.Vector(-n.x, -n.y, -n.z)
            d = -d
        self.clip.plane.setValue(coin.SbPlane(coin.SbVec3f(n.x, n.y, n.z), d))

    def compute_cut_solid(self, shape, plane_type, normal, plane_base, flip=False):
        """Exact boolean cut (slow). Used when GPU clipping is off and for freezing.

        flip=True keeps the opposite half. The geometry itself is never moved
        or reflected, so the object stays where it is while the plane moves.
        """
        size = max(self.x_len, self.y_len, self.z_len) * 4
        cutter = Part.makeBox(size, size, size)
        # local +Z is the plane normal; the cutter removes the +normal side,
        # or the -normal side when flipped
        cutter.translate(App.Vector(-size / 2, -size / 2, -size if flip else 0))

        rot = App.Rotation(App.Vector(0, 0, 1), normal)         # local +Z -> plane normal
        cutter.Placement = App.Placement(plane_base, rot).multiply(cutter.Placement)
        return shape.cut(cutter)

    @staticmethod
    def _plane_hits_bbox(bb, n, d):
        """False when the plane n.x = d cannot touch the bounding box (cheap test)."""
        c = bb.Center
        r = (bb.XLength * abs(n.x) + bb.YLength * abs(n.y) + bb.ZLength * abs(n.z)) * 0.5
        tol = max(bb.DiagonalLength, 1.0) * 1e-6
        return abs(n.x * c.x + n.y * c.y + n.z * c.z - d) <= r + tol

    @staticmethod
    def _nkey(n):
        return (round(n.x, 5), round(n.y, 5), round(n.z, 5))

    def _slice_cached(self, t, plane_type, normal, plane_base):
        return (t.key, plane_type, self._nkey(normal), round(normal.dot(plane_base), 4)) in self._slice_cache

    def get_slice(self, t, plane_type, normal, plane_base):
        """Section wires of one object via Shape.slice (cached)."""
        d = normal.dot(plane_base)
        if not self._plane_hits_bbox(t.bbox, normal, d):
            self._skipped += 1
            return []
        key = (t.key, plane_type, self._nkey(normal), round(d, 4))
        wires = self._slice_cache.get(key)
        if wires is None:
            t0 = time.perf_counter()
            try:
                wires = t.shape.slice(normal, d)
            except Exception as e:
                App.Console.PrintWarning(f"Slice failed: {e}\n")
                wires = []
            t.slice_ms = (time.perf_counter() - t0) * 1e3
            if len(self._slice_cache) >= SLICE_CACHE_SIZE:
                self._slice_cache.pop(next(iter(self._slice_cache)))
            self._slice_cache[key] = wires
        return wires

    @staticmethod
    def make_faces(wires):
        """Build faces (with holes) from the slice wires."""
        if not wires:
            return []
        try:
            f = Part.makeFace(wires, "Part::FaceMakerBullseye")
            faces = f.Faces
            if faces:
                return faces
        except Exception:
            pass
        faces = []
        for w in wires:
            try:
                if w.isClosed():
                    faces.append(Part.Face(w))
            except Exception:
                pass
        return faces

    @staticmethod
    def _pt(plane_type, c, u, v):
        if plane_type == "YZ":
            return App.Vector(c, u, v)
        if plane_type == "XZ":
            return App.Vector(u, c, v)
        return App.Vector(u, v, c)

    def build_hatch(self, faces, plane_type, center_pos, spacing, angle=45.0):
        """Hatch of one set of section faces with a SINGLE boolean.

        The lines run at `angle` degrees (in-plane axes as in _pt) and sit at multiples of
        `spacing` from the origin, exactly like the mesh section draws them.
        """
        section = Part.Compound(faces)
        bb = section.BoundBox
        U, W, N, d = self._plane_frame
        corners = [App.Vector(x, y, z) for x in (bb.XMin, bb.XMax)
                   for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
        us = [c.dot(U) for c in corners]
        vs = [c.dot(W) for c in corners]
        u_min, u_max, v_min, v_max = min(us) - 2, max(us) + 2, min(vs) - 2, max(vs) + 2
        origin = N * d

        def pt(u, v):
            return origin + U * u + W * v

        a = math.radians(angle)
        nu, nv = math.cos(a), math.sin(a)             # line normal
        wu, wv = -nv, nu                              # line direction
        s_vals = [u * nu + v * nv for u in (u_min, u_max) for v in (v_min, v_max)]
        s_lo, s_hi = min(s_vals), max(s_vals)
        if (s_hi - s_lo) / spacing > MAX_HATCH_LINES:
            spacing = (s_hi - s_lo) / MAX_HATCH_LINES
            App.Console.PrintWarning(
                "Hatch spacing too small for this section, spacing increased automatically.\n"
            )
        half = math.hypot(u_max - u_min, v_max - v_min) / 2 + 1.0
        t0 = (u_min + u_max) / 2 * wu + (v_min + v_max) / 2 * wv   # centre along the lines

        lines = []
        j = math.ceil(s_lo / spacing)
        while j * spacing <= s_hi:
            s = j * spacing
            bu, bv = s * nu + t0 * wu, s * nv + t0 * wv
            lines.append(Part.makeLine(
                pt(bu - half * wu, bv - half * wv),
                pt(bu + half * wu, bv + half * wv)))
            j += 1

        if not lines:
            return None

        try:
            result = section.common(Part.Compound(lines))
            if result.Edges:
                return result
        except Exception as e:
            App.Console.PrintWarning(f"Hatch failed: {e}\n")
        return None

    def build_hatch_per_object(self, per_obj, plane_type, center_pos, spacing):
        """Hatch of all sectioned objects, each with its own angle / spacing (HATCH_STYLES)."""
        parts = []
        for t, faces in per_obj:
            angle, scale = self._hatch_style(t)
            h = self.build_hatch(faces, plane_type, center_pos, spacing * scale, angle)
            if h is not None:
                parts.append(h)
        return Part.Compound(parts) if parts else None



