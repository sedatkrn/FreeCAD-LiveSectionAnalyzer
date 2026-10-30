class LiveSectionAnalyzer(_SceneMixin, _PanelMixin, _GeometryMixin, _MeshSectionMixin, _FreezeMixin,
                          QDockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Interactive Section Analysis")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self._closed = False
        self._mode = None             # "task" or "dock"
        self.panel = None
        self.targets = []             # all listed objects (checked = sectioned)
        self.section_feat = None
        self.edge_feat = None
        self.hatch_feat = None
        self.frozen_group = None
        self.clip = None
        self.clip_parent = None
        self.clip_ok = False
        self.preview = None
        self.mesh_switch = None       # Coin nodes of the mesh-based section (see _create_mesh_nodes)
        self.mesh_nodes = None
        self.gizmo = None
        self._gizmo_dragging = False
        self._gizmo_start_offset = 0.0
        self._slice_cache = {}
        self._loop_cache = {}         # mesh section loops per (object, mesh level, plane)
        self._tri_cache = {}          # OpenCascade triangulation of the section fill (same keys)
        self._hatch_idx = {}          # object key -> index into HATCH_STYLES
        self._scene_dirty = False     # a placement / shape changed in the document
        self._observer = None
        self._doc_name = App.ActiveDocument.Name if App.ActiveDocument else None
        self.init_done = False

        if not self._collect_targets():
            self._closed = True
            return

        # Plane center follows the selected objects; ranges follow the whole scene
        picked = [t.shape for t in self.targets if t.checked] or [t.shape for t in self.targets]
        self.bbox = self._union_bbox(picked)
        scene = self._union_bbox([t.shape for t in self.targets])
        self.x_len = scene.XMax - scene.XMin
        self.y_len = scene.YMax - scene.YMin
        self.z_len = scene.ZMax - scene.ZMin

        self._skipped = 0
        # Debounce timer: many UI events -> a single update
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.update_section)
        # One full (fine) update after parts stop moving
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(SCENE_SETTLE_MS)
        self._settle_timer.timeout.connect(self.update_section)

        # Features first: init_ui() needs to know whether GPU clipping is available
        self.create_features()
        self.init_ui()
        self.create_gizmo()
        self.update_style()
        self.update_section()
        self.init_done = True
        self._observer = _SceneObserver(self)
        App.addDocumentObserver(self._observer)
        self.show_panel()

    def create_features(self):
        """Create all features once"""
        doc = App.ActiveDocument
        if not doc:
            return

        # Section fill
        self.section_feat = doc.addObject("Part::Feature", "Live_Section_Fill")
        self.section_feat.ViewObject.ShapeColor = (0.75, 0.65, 0.68)
        self.section_feat.ViewObject.LineColor = (0.0, 0.0, 0.0)
        self.section_feat.ViewObject.LineWidth = 3.0
        self.section_feat.ViewObject.DisplayMode = "Shaded"
        self.section_feat.ViewObject.Transparency = 0
        self.section_feat.ViewObject.Visibility = False

        # Section edge lines
        self.edge_feat = doc.addObject("Part::Feature", "Live_Section_Edges")
        self.edge_feat.ViewObject.LineColor = (0.0, 0.0, 0.0)
        self.edge_feat.ViewObject.LineWidth = 2.0
        self.edge_feat.ViewObject.DisplayMode = "Wireframe"
        self.edge_feat.ViewObject.Visibility = False
        self.edge_feat.ViewObject.PointSize = 1

        # Hatch lines
        self.hatch_feat = doc.addObject("Part::Feature", "Live_Hatch_Lines")
        self.hatch_feat.ViewObject.LineColor = (0.3, 0.3, 0.3)
        self.hatch_feat.ViewObject.LineWidth = 1.0
        self.hatch_feat.ViewObject.DisplayMode = "Wireframe"
        self.hatch_feat.ViewObject.Visibility = False
        self.hatch_feat.ViewObject.PointSize = 1

        # One GPU clip plane at the root of the 3D view clips EVERYTHING that is shown
        if HAS_COIN:
            try:
                self.clip = coin.SoClipPlane()
                self.clip.on.setValue(False)
                view = Gui.ActiveDocument.ActiveView
                self.clip_parent = PlaneGizmo._render_group(view.getSceneGraph())
                self.clip_parent.insertChild(self.clip, 0)
                self.clip_ok = True
                self._create_preview_node()
                self._create_mesh_nodes()
            except Exception as e:
                App.Console.PrintWarning(f"Clip plane unavailable, using boolean mode: {e}\n")
                self.clip = None
                self.clip_parent = None
                self.clip_ok = False

        for feat in (self.section_feat, self.edge_feat, self.hatch_feat):
            _hide_in_tree(feat)
            feat.purgeTouched()

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------
    def _overlay_pose(self, normal, flip, use_clip):
        """Offset of the in-plane overlays (see OVERLAY_OFFSET_REL)."""
        eps = max(self.x_len, self.y_len, self.z_len) * OVERLAY_OFFSET_REL
        keep = App.Vector(normal)
        if CLIP_KEEP_NEGATIVE != bool(flip):
            keep = App.Vector(-keep.x, -keep.y, -keep.z)    # side that stays visible
        # clip mode: just inside the kept half; boolean mode: just in front of the cut face
        return App.Placement(keep * (eps if use_clip else -eps), App.Rotation())

    def update_section(self, force_exact=False):
        if self._closed or not self.targets:
            return
        doc = App.ActiveDocument
        if not doc:
            return

        self._timer.stop()
        t_start = time.perf_counter()
        tm = {"slice": 0.0, "faces": 0.0, "fill": 0.0, "edges": 0.0, "hatch": 0.0, "bg": 0.0}
        self._skipped = 0
        self._meshed = 0
        self._mesh_t0 = time.perf_counter()

        # While dragging the slider we only do the cheap parts
        quick = self.slider.isSliderDown() or self._gizmo_dragging

        if self._scene_dirty:
            self._scene_dirty = False
            if self._refresh_targets():
                quick = True                    # parts are moving: coarse now, fine when they stop
                self._settle_timer.start()

        use_clip = self.clip_ok and self.chk_gpu_clip.isChecked()
        self._sync_targets(use_clip)
        active = [t for t in self.targets if t.checked]
        busy = not quick                                   # wait cursor for the full (slow) update
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)

        plane_type, normal, plane_base, center_pos = self.get_plane()
        flip = self.chk_mirror_bg.isChecked()
        pose = self._overlay_pose(normal, flip, use_clip)
        # force_exact: Freeze needs the real OpenCascade geometry
        mesh_mode = self._mesh_active() and not force_exact

        self.sync_gizmo(plane_type, normal, plane_base)

        try:
            # --- CLIP: the whole scene (GPU mode) ---
            if use_clip:
                self.set_clip(normal, plane_base, flip)
                self.clip.on.setValue(True)
            elif self.clip_ok:
                self.clip.on.setValue(False)

            # --- BACKGROUND copies of the sectioned objects ---
            t0 = time.perf_counter()
            # GPU mode needs no copies: the clip plane cuts the original objects,
            # which stay selectable (Assembly joints, measuring, ...).
            for t in (() if use_clip else active):
                self._vis(t.obj, False)
                if t.bg_mode is None or not quick:
                    # (while dragging in boolean mode the previous solid is kept)
                    t.bg_feat.Shape = self.compute_cut_solid(t.shape, plane_type, normal, plane_base, flip)
                    self._set(t.bg_feat.ViewObject, "Lighting", "Two side")
                    t.bg_mode = "solid"
                self._vis(t.bg_feat, True)

            tm["bg"] = time.perf_counter() - t0
            self._meshed = 0

            if mesh_mode:
                # --- SECTION from the tessellation: outline, hatch and fill are Coin nodes ---
                self._show_preview([])
                for feat in (self.section_feat, self.edge_feat, self.hatch_feat):
                    self._vis(feat, False)
                self._update_mesh_section(active, plane_type, normal, plane_base, pose, quick, tm)
            else:
                self._mesh_hide()
                # --- SECTION (slice) of every checked object ---
                wires, faces, face_colors = [], [], []
                per_obj = []                                   # (target, faces) for the hatch
                preview_segs = []
                for t in active:
                    t0 = time.perf_counter()
                    if (quick and self.preview is not None and t.slice_ms > FAST_PREVIEW_MS
                            and not self._slice_cached(t, plane_type, normal, plane_base)):
                        # expensive object while dragging: outline from the tessellation
                        if self._plane_hits_bbox(t.bbox, normal, normal.dot(plane_base)):
                            preview_segs.append(self.mesh_segments(t, normal, normal.dot(plane_base)))
                            self._meshed += 1
                        else:
                            self._skipped += 1
                        tm["slice"] += time.perf_counter() - t0
                        continue
                    w = self.get_slice(t, plane_type, normal, plane_base)
                    tm["slice"] += time.perf_counter() - t0
                    if not w:
                        continue
                    t0 = time.perf_counter()
                    f = self.make_faces(w)
                    tm["faces"] += time.perf_counter() - t0
                    wires += w
                    faces += f
                    if f:
                        per_obj.append((t, f))
                    shade = tuple(c * 0.75 for c in t.color[:3])
                    face_colors += [shade + (1.0,)] * len(f)

                self._show_preview(preview_segs)

                if not wires:
                    self._vis(self.section_feat, False)
                    self._vis(self.edge_feat, False)
                    self._vis(self.hatch_feat, False)
                else:
                    # Fill
                    t0 = time.perf_counter()
                    if self.chk_section_fill.isChecked() and faces:
                        self.section_feat.Shape = Part.Compound(faces)
                        self.section_feat.Placement = pose
                        vo = self.section_feat.ViewObject
                        if self.chk_obj_colors.isChecked() and len(faces) == len(face_colors):
                            vo.DiffuseColor = face_colors
                        else:
                            vo.DiffuseColor = [tuple(vo.ShapeColor[:3]) + (1.0,)]
                        self._vis(self.section_feat, True)
                    else:
                        self._vis(self.section_feat, False)

                    tm["fill"] = time.perf_counter() - t0

                    # Edge lines
                    t0 = time.perf_counter()
                    if self.chk_edges.isChecked():
                        self.edge_feat.Shape = Part.Compound(wires)
                        self.edge_feat.Placement = pose
                        self._vis(self.edge_feat, True)
                    else:
                        self._vis(self.edge_feat, False)

                    tm["edges"] = time.perf_counter() - t0

                    # Hatch (skipped while dragging)
                    t0 = time.perf_counter()
                    if self.chk_hatch.isChecked() and faces and not quick:
                        hatch = self.build_hatch_per_object(
                            per_obj, plane_type, center_pos, self.spin_spacing.value())
                        if hatch is not None:
                            self.hatch_feat.Shape = hatch
                            self.hatch_feat.Placement = pose
                            self._vis(self.hatch_feat, True)
                        else:
                            self._vis(self.hatch_feat, False)
                    else:
                        self._vis(self.hatch_feat, False)
                    tm["hatch"] = time.perf_counter() - t0

            self.update_style()

        except Exception as e:
            App.Console.PrintError(f"Section Error: {str(e)}\n")

        if busy:
            QApplication.restoreOverrideCursor()

        # Plain Part::Feature objects need no recompute; just clear the "touched" flag.
        for feat in [self.section_feat, self.edge_feat, self.hatch_feat] + [t.bg_feat for t in active]:
            if feat is not None:
                feat.purgeTouched()

        total = time.perf_counter() - t_start
        # adaptive delay: after a slow update give the UI more time to collect events
        self._timer.setInterval(int(min(250, max(16 if quick else 60, total * 300))))

        if DEBUG_TIMING:
            App.Console.PrintMessage(
                "[LSA] %s %d obj (%d skipped by bbox, %d mesh) | bg %.0f  slice %.0f  faces %.0f  "
                "fill %.0f  edges %.0f  hatch %.0f | total %.0f ms\n" % (
                    ("drag" if quick else "full") + ("/mesh" if mesh_mode else ""),
                    len(active), self._skipped, self._meshed,
                    tm["bg"] * 1e3, tm["slice"] * 1e3, tm["faces"] * 1e3,
                    tm["fill"] * 1e3, tm["edges"] * 1e3, tm["hatch"] * 1e3, total * 1e3))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def shutdown(self, close_dialog=True):
        """Remove everything the macro created. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True

        if self._mode == "task" and close_dialog:
            try:
                Gui.Control.closeDialog()
            except Exception:
                pass

        try:
            self._timer.stop()
            self._settle_timer.stop()
        except Exception:
            pass
        if self._observer is not None:
            try:
                App.removeDocumentObserver(self._observer)
            except Exception:
                pass
            self._observer = None

        if self.gizmo is not None:
            self.gizmo.remove()
            self.gizmo = None

        if self.preview is not None and self.clip_parent is not None:
            try:
                self.clip_parent.removeChild(self.preview_switch)
            except Exception:
                pass
            self.preview = None

        if self.mesh_switch is not None and self.clip_parent is not None:
            try:
                self.clip_parent.removeChild(self.mesh_switch)
            except Exception:
                pass
            self.mesh_switch = None
            self.mesh_nodes = None

        # Remove clip plane from the scene graph before deleting its owner
        if self.clip is not None and self.clip_parent is not None:
            try:
                self.clip_parent.removeChild(self.clip)
            except Exception:
                pass
            self.clip = None
            self.clip_parent = None

        # Show the original objects again and remove their copies
        for t in self.targets:
            self._restore_transparency(t)
            self._release(t)

        doc = App.ActiveDocument
        if doc:
            # Delete all live features (frozen group is kept)
            for feat in [self.section_feat, self.edge_feat, self.hatch_feat]:
                if feat is not None:
                    try:
                        if doc.getObject(feat.Name):
                            doc.removeObject(feat.Name)
                    except Exception:
                        pass

        self._slice_cache.clear()
        self._loop_cache.clear()
        self._tri_cache.clear()

        if self._mode == "task":
            # The Tasks panel owns the form; the unused dock can simply go.
            self.deleteLater()

    def on_document_closed(self):
        """The sectioned document is going away: the panel has nothing left to show."""
        self._timer.stop()
        self._settle_timer.stop()
        # Not from inside the document's own deletion
        QTimer.singleShot(0, self.shutdown if self._mode == "task" else self.close)

    def closeEvent(self, event):
        """Dock-window close (also used when the macro is started again)"""
        self.shutdown()
        super().closeEvent(event)

        # Fully remove the dock so repeated runs don't leave hidden docks behind
        try:
            Gui.getMainWindow().removeDockWidget(self)
            self.deleteLater()
        except Exception:
            pass


