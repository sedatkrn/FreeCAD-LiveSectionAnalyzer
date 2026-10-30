class _MeshSectionMixin:
    """Mesh-based section drawn as Coin nodes (uses the numpy core)."""

    def _create_preview_node(self):
        """Coin node for the fast drag outline. It sits before the clip plane, so it is
        never clipped (it lies exactly in the plane)."""
        if not HAS_NP:
            return
        try:
            sw = coin.SoSwitch()
            sw.whichChild.setValue(coin.SO_SWITCH_NONE)
            sep = coin.SoSeparator()
            lm = coin.SoLightModel()
            lm.model.setValue(coin.SoLightModel.BASE_COLOR)
            col = coin.SoBaseColor()
            col.rgb.setValue(0.0, 0.0, 0.0)
            ds = coin.SoDrawStyle()
            ds.lineWidth.setValue(2.0)
            coords = coin.SoCoordinate3()
            lines = coin.SoLineSet()
            for node in (lm, col, ds, coords, lines):
                sep.addChild(node)
            sw.addChild(sep)
            self.clip_parent.insertChild(sw, 0)
            self.preview, self.preview_switch = sep, sw
            self.preview_coords, self.preview_lines = coords, lines
        except Exception as e:
            App.Console.PrintWarning(f"Fast preview unavailable: {e}\n")
            self.preview = None

    def _create_mesh_nodes(self):
        """Coin nodes of the mesh-based section: fill (polygons), outline and hatch (lines).

        Like the fast preview they sit before the clip plane, so they are never clipped.
        """
        if not HAS_NP:
            return
        try:
            sw = coin.SoSwitch()
            sw.whichChild.setValue(coin.SO_SWITCH_NONE)
            root = coin.SoSeparator()
            lm = coin.SoLightModel()
            lm.model.setValue(coin.SoLightModel.BASE_COLOR)
            root.addChild(lm)

            # fill: concave polygons (holes are bridged in), one color per polygon;
            # pushed back a little so the lines drawn in the same plane stay on top
            fsep = coin.SoSeparator()
            offs = coin.SoPolygonOffset()
            offs.factor.setValue(1.0)
            offs.units.setValue(1.0)
            bind = coin.SoMaterialBinding()
            bind.value.setValue(coin.SoMaterialBinding.PER_PART)
            fcol = coin.SoBaseColor()
            hints = coin.SoShapeHints()
            hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
            hints.shapeType.setValue(coin.SoShapeHints.UNKNOWN_SHAPE_TYPE)
            hints.faceType.setValue(coin.SoShapeHints.UNKNOWN_FACE_TYPE)
            fcoords = coin.SoCoordinate3()
            fset = coin.SoFaceSet()
            for node in (offs, bind, fcol, hints, fcoords, fset):
                fsep.addChild(node)
            root.addChild(fsep)

            def line_group():
                sep = coin.SoSeparator()
                col = coin.SoBaseColor()
                ds = coin.SoDrawStyle()
                coords = coin.SoCoordinate3()
                lines = coin.SoLineSet()
                for node in (col, ds, coords, lines):
                    sep.addChild(node)
                root.addChild(sep)
                return col, ds, coords, lines

            ecol, eds, ecoords, elines = line_group()
            hcol, hds, hcoords, hlines = line_group()
            sw.addChild(root)
            self.clip_parent.insertChild(sw, 0)
            self.mesh_switch = sw
            self.mesh_nodes = {
                "fill": (fcol, fcoords, fset),
                "edge": (ecol, eds, ecoords, elines),
                "hatch": (hcol, hds, hcoords, hlines),
            }
        except Exception as e:
            App.Console.PrintWarning(f"Mesh section unavailable: {e}\n")
            self.mesh_switch = None
            self.mesh_nodes = None

    @staticmethod
    def _tessellation(t):
        """Vertices / triangles of the object as numpy arrays (built once)."""
        if t.mesh is None:
            tol = max(t.bbox.DiagonalLength * 1e-3, 1e-4)
            verts, tris = t.shape.tessellate(tol)
            t.mesh = (np.array([(v.x, v.y, v.z) for v in verts], dtype=float).reshape(-1, 3),
                      np.array(tris, dtype=np.int64).reshape(-1, 3))
        return t.mesh

    def mesh_segments(self, t, normal, d):
        """Intersection of the plane n.x = d with the tessellation: (k, 2, 3) segments."""
        V, T = self._tessellation(t)
        if len(T) == 0:
            return np.zeros((0, 2, 3))
        n = np.array((normal.x, normal.y, normal.z))
        sv = V @ n - d
        pos = sv[T] >= 0.0                              # (m, 3)
        cnt = pos.sum(axis=1)
        cut = (cnt == 1) | (cnt == 2)
        if not cut.any():
            return np.zeros((0, 2, 3))
        P, sp, c1 = T[cut], pos[cut], (cnt[cut] == 1)
        lone = np.where(c1, sp.argmax(axis=1), (~sp).argmax(axis=1))
        r = np.arange(len(P))
        ia, ib, ic = P[r, lone], P[r, (lone + 1) % 3], P[r, (lone + 2) % 3]
        sa = sv[ia]

        def cross(i_other):
            so = sv[i_other]
            f = (sa / (sa - so))[:, None]
            return V[ia] + f * (V[i_other] - V[ia])

        return np.stack((cross(ib), cross(ic)), axis=1)

    # ------------------------------------------------------------------
    # Mesh-based section (v0.5.0)
    # ------------------------------------------------------------------
    @staticmethod
    def _plane_basis(plane_type):
        """In-plane axes (u, w) of the section plane as numpy vectors."""
        if plane_type == "YZ":
            return np.array((0.0, 1.0, 0.0)), np.array((0.0, 0.0, 1.0))
        if plane_type == "XZ":
            return np.array((1.0, 0.0, 0.0)), np.array((0.0, 0.0, 1.0))
        return np.array((1.0, 0.0, 0.0)), np.array((0.0, 1.0, 0.0))

    def _basis_for(self, plane_type):
        """Current in-plane axes (u, w) as numpy vectors, tilt included."""
        fr = getattr(self, "_plane_frame", None)
        if fr is None:
            return self._plane_basis(plane_type)
        return (np.array((fr[0].x, fr[0].y, fr[0].z)), np.array((fr[1].x, fr[1].y, fr[1].z)))

    def _mesh_active(self):
        return (HAS_NP and self.mesh_switch is not None and hasattr(self, "chk_mesh")
                and self.chk_mesh.isChecked())

    def _mesh_for(self, t, fine):
        """Welded tessellation (V, T, gap) of a target, coarse or fine; built once and cached."""
        lvl = 1 if fine else 0
        m = t.meshes.get(lvl)
        if m is None and fine:
            coarse = self._mesh_for(t, False)
            if (len(coarse[1]) > MESH_FINE_MAX_TRIS
                    or getattr(t, "mesh_s", 0.0) * MESH_FINE_PREDICT > MESH_FINE_BUDGET_S
                    or time.perf_counter() - self._mesh_t0 > MESH_FINE_BUDGET_S):
                t.meshes[1] = coarse                  # heavy part / out of time: keep the coarse mesh
                return coarse
        if m is None:
            diag = max(t.bbox.DiagonalLength, 1e-9)
            tol = max(diag * (MESH_TOL_FINE_REL if fine else MESH_TOL_REL), MESH_TOL_MIN)
            t_tess = time.perf_counter()
            Vs, Ts, Ss, off = [], [], [], 0
            for i, solid in enumerate(t.shape.Solids or [t.shape]):
                verts, tris = solid.tessellate(tol)
                if not tris:
                    continue
                Vs.append(np.array([(p.x, p.y, p.z) for p in verts], dtype=float).reshape(-1, 3))
                Ts.append(np.array(tris, dtype=np.int64).reshape(-1, 3) + off)
                Ss.append(np.full(len(verts), i, dtype=np.int64))
                off += len(verts)
            if Vs:
                V, T = mesh_weld(np.concatenate(Vs), np.concatenate(Ts), np.concatenate(Ss), diag * 1e-6)
            else:
                V, T = np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
            m = (V, T, diag * 1e-4)
            t.meshes[lvl] = m
            if not fine:
                t.mesh_s = time.perf_counter() - t_tess        # cost of the coarse mesh, predicts the fine one
        return m

    def _mesh_loops(self, t, fine, normal, d):
        """(closed, open) section polylines of one object from its mesh (cached)."""
        key = (t.key, fine, round(normal.x, 6), round(normal.y, 6), round(normal.z, 6), round(d, 4))
        hit = self._loop_cache.get(key)
        if hit is None:
            V, T, gap = self._mesh_for(t, fine)
            n = (normal.x, normal.y, normal.z)
            hit = mesh_section_loops(V, T, np.array(n), d, gap, self._plane_index(t, fine, V, T, n))
            if len(self._loop_cache) >= MESH_CACHE_SIZE:
                self._loop_cache.pop(next(iter(self._loop_cache)))
            self._loop_cache[key] = hit
        return hit

    @staticmethod
    def _plane_index(t, fine, V, T, n):
        """Slab index of a big mesh once the same normal is used twice in a row: moving the
        plane reuses it, while tilting (a new normal every update) stays a plain scan."""
        if len(T) < MESH_INDEX_MIN_TRIS:
            return None
        nkey = tuple(round(c, 6) for c in n)
        entry = t.plane_index.get(fine)
        if entry is None or entry[0] is not V or entry[1] != nkey:
            t.plane_index[fine] = (V, nkey, None)
            return None
        if entry[2] is None:
            entry = (V, nkey, mesh_plane_index(V, T, n))
            t.plane_index[fine] = entry
        return entry[2]

    def _fill_quads(self, key, loops):
        """Convex quads of the section fill (cached); see fill_trapezoids()."""
        quads = self._tri_cache.get(key)
        if quads is None:
            quads = fill_trapezoids(loops)
            if len(self._tri_cache) >= MESH_CACHE_SIZE:
                self._tri_cache.pop(next(iter(self._tri_cache)))
            self._tri_cache[key] = quads
        return quads

    def _hatch_style(self, t):
        """(angle, spacing factor) of an object; stable for the whole session."""
        idx = self._hatch_idx.get(t.key)
        if idx is None:
            idx = len(self._hatch_idx)
            self._hatch_idx[t.key] = idx
        return HATCH_STYLES[idx % len(HATCH_STYLES)]

    @staticmethod
    def _fill_node(coords, shape_set, arrays):
        """Point arrays ((n, 3) each) -> SoCoordinate3 + numVertices of a line/face set."""
        if arrays:
            pts = np.concatenate(arrays)
            counts = [len(a) for a in arrays]
            coords.point.setValues(0, len(pts), pts.tolist())
            shape_set.numVertices.setValues(0, len(counts), counts)
        coords.point.setNum(sum(len(a) for a in arrays))
        shape_set.numVertices.setNum(len(arrays))

    def _apply_mesh_style(self):
        """Colors and widths of the mesh section follow the panel (via the exact features)."""
        if self.mesh_nodes is None:
            return
        try:
            ecol, eds, _, _ = self.mesh_nodes["edge"]
            hcol, hds, _, _ = self.mesh_nodes["hatch"]
            ecol.rgb.setValue(*tuple(self.edge_feat.ViewObject.LineColor[:3]))
            eds.lineWidth.setValue(float(self.edge_feat.ViewObject.LineWidth))
            hcol.rgb.setValue(*tuple(self.hatch_feat.ViewObject.LineColor[:3]))
            hds.lineWidth.setValue(float(self.hatch_feat.ViewObject.LineWidth))
        except Exception:
            pass

    def _mesh_hide(self):
        if self.mesh_switch is not None:
            self.mesh_switch.whichChild.setValue(coin.SO_SWITCH_NONE)

    def _update_mesh_section(self, active, plane_type, normal, plane_base, pose, quick, tm):
        """Outline, hatch and fill of the checked objects from their tessellation.

        Loops come from chaining the plane/triangle segments, the hatch is scanline-clipped
        against them (even-odd) and the fill is one polygon per outer loop with its holes
        bridged in. Everything is drawn as Coin nodes; exact geometry exists only for Freeze.
        """
        perf = time.perf_counter
        d = normal.dot(plane_base)
        U, W = self._basis_for(plane_type)
        basis = np.stack((U, W), axis=1)                                   # (3, 2)
        base3 = np.array((normal.x, normal.y, normal.z)) * d + np.array(
            (pose.Base.x, pose.Base.y, pose.Base.z))

        def to3(p2):
            return p2 @ basis.T + base3

        fine = not quick
        spacing = self.spin_spacing.value()
        want_fill = self.chk_section_fill.isChecked()
        want_edges = self.chk_edges.isChecked()
        want_hatch = self.chk_hatch.isChecked() and (fine or MESH_DRAG_HATCH)
        obj_colors = self.chk_obj_colors.isChecked()
        flat = tuple(self.section_feat.ViewObject.ShapeColor[:3])

        edges, hatch, fill, fill_cols, fill_counts = [], [], [], [], []
        self._mesh_t0 = perf()
        for t in active:
            if not self._plane_hits_bbox(t.bbox, normal, d):
                self._skipped += 1
                continue
            t0 = perf()
            closed, opened = self._mesh_loops(t, fine, normal, d)
            tm["slice"] += perf() - t0
            if not closed and not opened:
                continue

            loops = [L @ basis for L in closed]                            # (m, 2) in-plane
            if want_edges:
                t0 = perf()
                for L in loops:
                    edges.append(to3(np.vstack((L, L[:1]))))
                for L in opened:
                    edges.append(to3(L @ basis))
                tm["edges"] += perf() - t0

            if want_fill and loops:
                t0 = perf()
                npts = sum(len(L) for L in loops)
                shade = tuple(c * 0.75 for c in t.color[:3]) if obj_colors else flat
                if npts <= MESH_MAX_FILL_POINTS:
                    if npts <= MESH_MAX_QUAD_POINTS:
                        tkey = (t.key, fine, round(normal.x, 6), round(normal.y, 6), round(normal.z, 6), round(d, 4))
                        quads = self._fill_quads(tkey, loops)             # convex quads: no tearing
                        if len(quads):
                            fill.append(to3(quads.reshape(-1, 2)))        # one transform for all quads
                            fill_counts.extend([4] * len(quads))
                            fill_cols.extend([shade] * len(quads))
                    else:
                        for p in fill_polygons(loops):                    # huge sections
                            fill.append(to3(p))
                            fill_counts.append(len(p))
                            fill_cols.append(shade)
                tm["faces"] += perf() - t0

            if want_hatch and loops:
                t0 = perf()
                angle, scale = self._hatch_style(t)
                segs = hatch_segments(loops, angle, spacing * scale, MAX_HATCH_LINES)
                if len(segs):
                    hatch.append(to3(segs.reshape(-1, 2)))
                tm["hatch"] += perf() - t0

        t0 = perf()
        try:
            fcol, fcoords, fset = self.mesh_nodes["fill"]
            if fill:
                fpts = np.concatenate(fill)
                fcoords.point.setValues(0, len(fpts), fpts.tolist())
                fset.numVertices.setValues(0, len(fill_counts), fill_counts)
            fcoords.point.setNum(sum(len(a) for a in fill))
            fset.numVertices.setNum(len(fill_counts))
            if fill_cols:
                fcol.rgb.setValues(0, len(fill_cols), fill_cols)
            fcol.rgb.setNum(len(fill_cols))
            self._fill_node(self.mesh_nodes["edge"][2], self.mesh_nodes["edge"][3], edges)
            hcoords, hlines = self.mesh_nodes["hatch"][2:]
            if hatch:
                pts = np.concatenate(hatch)
                hcoords.point.setValues(0, len(pts), pts.tolist())
                hlines.numVertices.setValues(0, len(pts) // 2, [2] * (len(pts) // 2))
                hcoords.point.setNum(len(pts))
                hlines.numVertices.setNum(len(pts) // 2)
            else:
                hcoords.point.setNum(0)
                hlines.numVertices.setNum(0)
            shown = bool(fill or edges or hatch)
            self.mesh_switch.whichChild.setValue(coin.SO_SWITCH_ALL if shown else coin.SO_SWITCH_NONE)
        except Exception as e:
            App.Console.PrintError(f"Mesh section error: {e}\n")
        tm["fill"] += perf() - t0

    def _show_preview(self, segs):
        """Draw the fast outline (list of (k,2,3) arrays) or hide it when empty."""
        if self.preview is None:
            return
        pts = [a for a in segs if len(a)]
        if not pts:
            self.preview_switch.whichChild.setValue(coin.SO_SWITCH_NONE)
            return
        arr = np.concatenate(pts).reshape(-1, 3)
        k = len(arr) // 2
        # setValues() does not shrink the fields: cut them to the new size, otherwise
        # the segments of earlier plane positions stay on screen
        self.preview_coords.point.setValues(0, len(arr), arr.tolist())
        self.preview_coords.point.setNum(len(arr))
        self.preview_lines.numVertices.setValues(0, k, [2] * k)
        self.preview_lines.numVertices.setNum(k)
        self.preview_switch.whichChild.setValue(coin.SO_SWITCH_ALL)



