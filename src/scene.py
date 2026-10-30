class _SceneMixin:
    """Finding the objects to section and following them when they move."""

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    # Selected objects that only hold other objects: the part to section is the
    # first child in the selection path.
    _CONTAINERS = ("App::Part", "Assembly::AssemblyObject", "Assembly::AssemblyLink",
                   "App::LinkGroup", "App::DocumentObjectGroup")

    @staticmethod
    def _union_bbox(shapes):
        bb = App.BoundBox()
        for sh in shapes:
            bb.add(sh.BoundBox)
        return bb

    @staticmethod
    def _solid_or_compound(shape):
        if shape.ShapeType == "Compound" and len(shape.Solids) == 1:
            return shape.Solids[0]
        return shape

    def _resolve_entry(self, top, sub):
        """Selection entry -> (display object, shape in GLOBAL coordinates).

        getSelection() returns the leaf object (e.g. a Pocket inside a linked
        Body), whose shape lacks the placement of the assembly / link / part it
        sits in. With the full path, Part.getShape() applies every placement.
        """
        shape = None
        try:
            shape = Part.getShape(top, sub, needSubElement=False, refine=False)
        except Exception:
            pass
        if shape is None or shape.isNull():
            if hasattr(top, "Shape") and not top.Shape.isNull():
                shape = top.Shape
            else:
                return None, None

        # The object that gets hidden while the section is shown
        target = top
        if sub and top.TypeId in self._CONTAINERS:
            try:
                first = top.resolve(sub.split(".")[0] + ".")[0]
                if first is not None:
                    target = first
            except Exception:
                pass
        if target is not top or top.getParentGeoFeatureGroup() is not None:
            try:
                return target, self._global_shape(target)
            except Exception:
                pass
        return target, self._solid_or_compound(shape)

    def _global_shape(self, obj):
        """Shape of a scene object with the placements of all its parents.

        The parent placements are multiplied here instead of resolving a subname
        path with Part.getShape(): for parts inside an Assembly::AssemblyLink that
        path ignores the link's own placement (moved sub-assemblies left their
        section behind). Only the Location changes, so the TShape is shared and a
        pure move is cheap to detect (see _rigid_delta).
        """
        shape = Part.getShape(obj, needSubElement=False, refine=False)
        cur = obj
        while True:
            try:
                parent = cur.getParentGeoFeatureGroup()
            except Exception:
                parent = None
            if parent is None:
                break
            pl = getattr(parent, "Placement", None)
            if pl is not None:
                shape.Placement = pl.multiply(shape.Placement)
            cur = parent
        return self._solid_or_compound(shape)

    def _scene_targets(self, skip_keys=()):
        """Visible solids of the active document (not the macro's own objects)."""
        found = []
        doc = App.ActiveDocument
        if not doc:
            return found
        for obj in doc.Objects:
            try:
                if obj.Name.startswith(("Live_", "Section_")) or obj.TypeId in self._CONTAINERS:
                    continue
                vo = obj.ViewObject
                if vo is None or not hasattr(obj, "Shape") or not vo.Visibility:
                    continue
                if (obj.Document.Name, obj.Name) in skip_keys:
                    continue
                parent = obj.getParentGeoFeatureGroup()
                if parent is not None and parent.TypeId == "PartDesign::Body":
                    continue                      # features inside a Body: the Body is listed
                hidden_parent = False
                while parent is not None:
                    if parent.ViewObject is not None and not parent.ViewObject.Visibility:
                        hidden_parent = True
                        break
                    parent = parent.getParentGeoFeatureGroup()
                if hidden_parent:
                    continue
                shape = self._global_shape(obj)
                if shape.isNull() or not shape.Solids:
                    continue
                found.append(_Target(obj, shape))
            except Exception:
                continue
        found.sort(key=lambda t: t.label.lower())
        return found

    def _collect_targets(self):
        """Selected objects (checked) + every other visible solid (unchecked)."""
        picked, seen = [], set()
        for sel in Gui.Selection.getSelectionEx("", 0):
            for sub in (sel.SubElementNames or [""]):
                obj, shape = self._resolve_entry(sel.Object, sub)
                if obj is None:
                    continue
                key = (obj.Document.Name, obj.Name)
                if key in seen:
                    continue
                seen.add(key)
                t = _Target(obj, shape, checked=True)
                t.src = (sel.Object, sub)
                picked.append(t)
        scene = self._scene_targets(skip_keys=seen)
        if not picked:
            # Nothing selected: clip everything that is visible. A small scene is
            # sectioned completely, a big one starts unchecked (check what you need).
            if not scene:
                QMessageBox.warning(None, "Error", "There are no visible solids in the active document.")
                return False
            if len(scene) <= AUTO_SECTION_MAX_OBJECTS:
                for t in scene:
                    t.checked = True
        self.targets = picked + scene
        return True

    def rescan_objects(self):
        """Re-read the visible solids; keeps the objects that are being sectioned."""
        if self._closed:
            return
        kept = [t for t in self.targets if t.checked]
        keys = {t.key for t in kept}
        dropped = [t for t in self.targets if not t.checked]
        for t in dropped:
            self._restore_transparency(t)
            self._release(t)
        self.targets = kept + self._scene_targets(skip_keys=keys)
        self._fill_list()
        self.schedule_update()

    @staticmethod
    def _display_color(obj):
        """Shape color of an object, following links to the real object."""
        candidates = [obj, getattr(obj, "LinkedObject", None)]
        try:
            candidates.append(obj.getLinkedObject(True))
        except Exception:
            pass
        for o in candidates:
            vo = getattr(o, "ViewObject", None) if o is not None else None
            color = getattr(vo, "ShapeColor", None) if vo is not None else None
            if color:
                return color
        return (0.8, 0.8, 0.8)

    # ------------------------------------------------------------------
    # Following moving parts
    # ------------------------------------------------------------------
    def on_scene_changed(self):
        if self._closed or not FOLLOW_SCENE:
            return
        self._scene_dirty = True
        self.schedule_update()

    def _current_shape(self, t):
        if t.src is not None:
            return self._resolve_entry(*t.src)[1]
        return self._global_shape(t.obj)

    @staticmethod
    def _rigid_delta(old, new):
        """4x4 numpy matrix that moves `old` onto `new`, or None if the geometry changed."""
        if new.isPartner(old):
            return np.array(new.Matrix.A).reshape(4, 4) @ np.linalg.inv(np.array(old.Matrix.A).reshape(4, 4))
        a, b = old.Vertexes, new.Vertexes
        if len(a) != len(b) or len(old.Faces) != len(new.Faces) or not a:
            return None
        if abs(old.Volume - new.Volume) > 1e-6 * max(abs(old.Volume), 1.0):
            return None
        step = max(1, len(a) // 2000)
        P = np.array([(v.X, v.Y, v.Z) for v in a[::step]])
        Q = np.array([(v.X, v.Y, v.Z) for v in b[::step]])
        # rigid fit (Kabsch); topology order is kept when a shape is only moved
        pc, qc = P.mean(axis=0), Q.mean(axis=0)
        U, _, Wt = np.linalg.svd((P - pc).T @ (Q - qc))
        D = np.diag([1.0, 1.0, np.sign(np.linalg.det(Wt.T @ U.T))])
        R = Wt.T @ D @ U.T
        tr = qc - R @ pc
        diag = max(old.BoundBox.DiagonalLength, 1e-9)
        if np.abs(P @ R.T + tr - Q).max() > 1e-6 * diag:
            return None
        M = np.eye(4)
        M[:3, :3], M[:3, 3] = R, tr
        return M

    def _forget(self, t):
        """Drop every cached section of one target."""
        for cache in (self._slice_cache, self._loop_cache, self._tri_cache):
            for k in [k for k in cache if k[0] == t.key]:
                del cache[k]

    def _refresh_targets(self):
        """Re-read the global shapes; moved meshes are transformed, changed ones rebuilt.
        Returns True if anything moved or changed."""
        changed = False
        for t in self.targets:
            try:
                new = self._current_shape(t)
            except Exception:
                continue
            if new is None or new.isNull() or new.isSame(t.shape):
                continue
            M = self._rigid_delta(t.shape, new) if HAS_NP else None
            if M is not None and np.allclose(M, np.eye(4), atol=1e-12):
                t.shape = new
                continue
            if M is not None:
                R, tr = M[:3, :3], M[:3, 3]
                t.meshes = {lvl: (V @ R.T + tr, T, gap) for lvl, (V, T, gap) in t.meshes.items()}
                if t.mesh is not None:
                    t.mesh = (t.mesh[0] @ R.T + tr, t.mesh[1])
            else:
                t.meshes, t.mesh, t.slice_ms = {}, None, 0.0
            t.shape = new
            t.bbox = new.BoundBox
            t.bg_mode = None                # boolean background: cut again
            self._forget(t)
            changed = True
        return changed



