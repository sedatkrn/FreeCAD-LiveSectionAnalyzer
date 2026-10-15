class LiveSectionAnalyzer(QDockWidget):
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
            feat.purgeTouched()

    def init_ui(self):
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(4, 4, 4, 4)

        # --- HEADER: logo, title, version ---
        header = QHBoxLayout()
        pm = _logo_pixmap(40)
        if pm is not None:
            logo = QLabel()
            logo.setPixmap(pm)
            logo.setFixedSize(44, 44)
            header.addWidget(logo)
        title = QLabel(
            "<b>Live Section Analyzer</b><br>"
            "<span style='color: #777; font-size: 10px;'>FreeCAD clipping macro  v%s</span>" % VERSION
        )
        header.addWidget(title, 1)
        root.addLayout(header)

        def section(title, expanded=True):
            box = _Collapsible(title, expanded)
            root.addWidget(box)
            return box.body_layout

        # --- OBJECTS ---
        layout = section("Objects (checked = section)")
        self.obj_list = QListWidget()
        self.obj_list.setMinimumHeight(110)
        self.obj_list.setToolTip(
            "Everything visible is clipped by the plane.\n"
            "Checked objects also get a section fill, outline and hatch."
        )
        layout.addWidget(self.obj_list)
        row = QHBoxLayout()
        for text, slot in (("All", lambda: self._check_all(True)),
                           ("None", lambda: self._check_all(False)),
                           ("Refresh", self.rescan_objects)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        layout.addLayout(row)
        self._fill_list()
        self.obj_list.itemChanged.connect(self.on_target_toggled)

        # --- SECTION PLANE ---
        layout = section("Section Plane")
        self.radio_xy = QRadioButton("XY Plane")
        self.radio_xz = QRadioButton("XZ Plane")
        self.radio_yz = QRadioButton("YZ Plane (Default)")
        self.radio_yz.setChecked(True)

        layout.addWidget(self.radio_xy)
        layout.addWidget(self.radio_xz)
        layout.addWidget(self.radio_yz)

        self.chk_gizmo = QCheckBox("Show plane gizmo (drag the arrow in the 3D view)")
        self.chk_gizmo.setChecked(True)
        self.chk_gizmo.stateChanged.connect(self.on_gizmo_toggled)
        layout.addWidget(self.chk_gizmo)

        # Tilt: turns the plane about its two in-plane axes (0 / 0 = axis-aligned)
        tilt_layout = QHBoxLayout()
        self.lbl_tilt_a, self.lbl_tilt_b = QLabel("Tilt A:"), QLabel("Tilt B:")
        self.spin_tilt_a, self.spin_tilt_b = QDoubleSpinBox(), QDoubleSpinBox()
        for lbl, sp in ((self.lbl_tilt_a, self.spin_tilt_a), (self.lbl_tilt_b, self.spin_tilt_b)):
            sp.setRange(-89.0, 89.0)
            sp.setDecimals(1)
            sp.setSingleStep(5.0)
            sp.setSuffix(" \u00b0")
            sp.setToolTip("Tilts the section plane about one of its in-plane axes. "
                          "0 \u00b0 / 0 \u00b0 is the plain XY / XZ / YZ plane.")
            sp.valueChanged.connect(self.schedule_update)
            tilt_layout.addWidget(lbl)
            tilt_layout.addWidget(sp)
        self.btn_tilt_reset = QPushButton("Reset")
        self.btn_tilt_reset.setToolTip("Back to the axis-aligned plane")
        self.btn_tilt_reset.clicked.connect(self.reset_tilt)
        tilt_layout.addWidget(self.btn_tilt_reset)
        layout.addLayout(tilt_layout)

        # Section engine (part of the Section Plane group)
        mesh_ok = HAS_NP and self.mesh_switch is not None
        self.chk_mesh = QCheckBox("Mesh-based section (fast, curves are polygonal)")
        self.chk_mesh.setChecked(bool(MESH_SECTION) and mesh_ok)
        self.chk_mesh.setEnabled(mesh_ok)
        self.chk_mesh.setToolTip(
            "Builds outline, hatch and fill from the tessellation instead of OpenCascade: "
            "much faster on big or curved parts. Each object gets its own hatch angle and "
            "spacing. Freeze Section always uses exact geometry."
        )
        layout.addWidget(self.chk_mesh)

        # --- OFFSET ---
        layout = section("Offset (Position)")

        max_dim = max(self.x_len, self.y_len, self.z_len)
        self.offset_range = max(max_dim * 0.6, 10.0)

        offset_layout = QHBoxLayout()

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(-100, 100)
        self.slider.setValue(0)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(10)
        offset_layout.addWidget(self.slider)

        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-self.offset_range, self.offset_range)
        self.offset_spin.setValue(0.0)
        self.offset_spin.setSingleStep(self.offset_range / 20.0)
        self.offset_spin.setSuffix(" mm")
        self.offset_spin.setDecimals(1)
        self.offset_spin.setMaximumWidth(100)
        offset_layout.addWidget(self.offset_spin)

        layout.addLayout(offset_layout)

        range_label = QLabel(f"<i>Offset range: ±{self.offset_range:.1f} mm (based on object size)</i>")
        range_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(range_label)

        # --- BACKGROUND SETTINGS ---
        layout = section("Background")

        trans_layout = QHBoxLayout()
        trans_layout.addWidget(QLabel("Transparency (%):"))
        self.spin_trans = QSpinBox()
        self.spin_trans.setRange(0, 100)
        self.spin_trans.setValue(0)
        trans_layout.addWidget(self.spin_trans)
        trans_layout.addStretch()
        layout.addLayout(trans_layout)

        # GPU clip toggle
        gpu_layout = QHBoxLayout()
        self.chk_gpu_clip = QCheckBox("Fast GPU clipping")
        self.chk_gpu_clip.setChecked(self.clip_ok)
        self.chk_gpu_clip.setEnabled(self.clip_ok)
        self.chk_gpu_clip.setToolTip(
            "Clips the object on the GPU (fast). When off, an exact boolean "
            "cut is computed instead (slower, but real geometry)."
        )
        gpu_layout.addWidget(self.chk_gpu_clip)
        gpu_layout.addStretch()
        layout.addLayout(gpu_layout)

        mirror_layout = QHBoxLayout()
        self.chk_mirror_bg = QCheckBox("Mirror Background (show opposite half)")
        self.chk_mirror_bg.setChecked(False)
        mirror_layout.addWidget(self.chk_mirror_bg)
        mirror_layout.addStretch()
        layout.addLayout(mirror_layout)

        # --- SECTION FILL ---
        layout = section("Section Fill")

        # With GPU clipping the cut is open, so the fill acts as the cap: default on.
        self.chk_section_fill = QCheckBox("Show Section Fill")
        self.chk_section_fill.setChecked(True)
        layout.addWidget(self.chk_section_fill)

        self.chk_obj_colors = QCheckBox("Use object colors")
        self.chk_obj_colors.setChecked(sum(1 for t in self.targets if t.checked) > 1)
        self.chk_obj_colors.setToolTip("Tint each object's section with its own color")
        layout.addWidget(self.chk_obj_colors)

        fill_color_layout = QHBoxLayout()
        fill_color_layout.addWidget(QLabel("Color:"))
        self.fill_color_btn = QPushButton()
        self.fill_color_btn.setStyleSheet("background-color: rgb(191, 166, 173); max-width: 50px;")
        self.fill_color_btn.clicked.connect(lambda: self.pick_color(self.fill_color_btn, "fill"))
        fill_color_layout.addWidget(self.fill_color_btn)
        fill_color_layout.addStretch()
        layout.addLayout(fill_color_layout)

        # --- EDGE LINE SETTINGS ---
        layout = section("Edge Lines")

        self.chk_edges = QCheckBox("Show Edge Lines")
        self.chk_edges.setChecked(True)
        layout.addWidget(self.chk_edges)

        edge_color_layout = QHBoxLayout()
        edge_color_layout.addWidget(QLabel("Color:"))
        self.edge_color_btn = QPushButton()
        self.edge_color_btn.setStyleSheet("background-color: rgb(0, 0, 0); max-width: 50px;")
        self.edge_color_btn.clicked.connect(lambda: self.pick_color(self.edge_color_btn, "edge"))
        edge_color_layout.addWidget(self.edge_color_btn)
        edge_color_layout.addStretch()
        layout.addLayout(edge_color_layout)

        edge_width_layout = QHBoxLayout()
        edge_width_layout.addWidget(QLabel("Width:"))
        self.edge_width_spin = QDoubleSpinBox()
        self.edge_width_spin.setRange(0.5, 10.0)
        self.edge_width_spin.setValue(2.0)
        self.edge_width_spin.setSingleStep(0.5)
        edge_width_layout.addWidget(self.edge_width_spin)
        edge_width_layout.addStretch()
        layout.addLayout(edge_width_layout)

        # --- HATCH SETTINGS ---
        layout = section("Hatch")

        self.chk_hatch = QCheckBox("Enable Hatch")
        self.chk_hatch.setChecked(True)
        layout.addWidget(self.chk_hatch)

        spacing_layout = QHBoxLayout()
        spacing_layout.addWidget(QLabel("Spacing (mm):"))
        self.spin_spacing = QDoubleSpinBox()
        self.spin_spacing.setRange(0.5, 20.0)
        self.spin_spacing.setValue(3.0)
        self.spin_spacing.setSingleStep(0.5)
        spacing_layout.addWidget(self.spin_spacing)
        spacing_layout.addStretch()
        layout.addLayout(spacing_layout)

        hatch_color_layout = QHBoxLayout()
        hatch_color_layout.addWidget(QLabel("Color:"))
        self.hatch_color_btn = QPushButton()
        self.hatch_color_btn.setStyleSheet("background-color: rgb(77, 77, 77); max-width: 50px;")
        self.hatch_color_btn.clicked.connect(lambda: self.pick_color(self.hatch_color_btn, "hatch"))
        hatch_color_layout.addWidget(self.hatch_color_btn)
        hatch_color_layout.addStretch()
        layout.addLayout(hatch_color_layout)

        hatch_width_layout = QHBoxLayout()
        hatch_width_layout.addWidget(QLabel("Width:"))
        self.hatch_width_spin = QDoubleSpinBox()
        self.hatch_width_spin.setRange(0.5, 5.0)
        self.hatch_width_spin.setValue(1.0)
        self.hatch_width_spin.setSingleStep(0.5)
        hatch_width_layout.addWidget(self.hatch_width_spin)
        hatch_width_layout.addStretch()
        layout.addLayout(hatch_width_layout)

        hatch_point_layout = QHBoxLayout()
        hatch_point_layout.addWidget(QLabel("Point Size (px):"))
        self.hatch_point_spin = QSpinBox()
        self.hatch_point_spin.setRange(0, 10)
        self.hatch_point_spin.setValue(1)
        self.hatch_point_spin.setSingleStep(1)
        hatch_point_layout.addWidget(self.hatch_point_spin)
        hatch_point_layout.addStretch()
        layout.addLayout(hatch_point_layout)

        # --- FREEZE BUTTON ---
        self.btn_freeze = QPushButton("❄️ Freeze Section (Simple Copy with Folder)")
        self.btn_freeze.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        self.btn_freeze.clicked.connect(self.freeze_section)

        # --- CONNECTIONS ---
        # Plane radios: only react to the button that became checked
        self.radio_xy.toggled.connect(self.on_plane_toggled)
        self.radio_xz.toggled.connect(self.on_plane_toggled)
        self.radio_yz.toggled.connect(self.on_plane_toggled)

        self.slider.valueChanged.connect(self.on_slider_changed)
        self.slider.sliderReleased.connect(self.schedule_update)   # full update after drag
        self.offset_spin.valueChanged.connect(self.on_spin_changed)

        # Geometry-affecting controls -> debounced geometry update
        self.chk_mirror_bg.stateChanged.connect(self.schedule_update)
        self.chk_gpu_clip.stateChanged.connect(self.schedule_update)
        self.chk_section_fill.stateChanged.connect(self.schedule_update)
        self.chk_obj_colors.stateChanged.connect(self.schedule_update)
        self.chk_hatch.stateChanged.connect(self.schedule_update)
        self.spin_spacing.valueChanged.connect(self.schedule_update)
        self.chk_edges.stateChanged.connect(self.schedule_update)
        self.chk_mesh.stateChanged.connect(self.schedule_update)

        # Style-only controls -> cheap style update (no geometry)
        self.spin_trans.valueChanged.connect(self.update_style)
        self.edge_width_spin.valueChanged.connect(self.update_style)
        self.hatch_width_spin.valueChanged.connect(self.update_style)
        self.hatch_point_spin.valueChanged.connect(self.update_style)

        root.addStretch()

        # Scrollable options + a footer that is always visible
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(scroll, 1)

        # Hint shown while more options are hidden below the visible area
        bar = scroll.verticalScrollBar()
        self.more_hint = QPushButton("\u25BC  more options below  \u25BC")
        self.more_hint.setFlat(True)
        self.more_hint.setCursor(Qt.PointingHandCursor)
        self.more_hint.setToolTip("Scroll down")
        self.more_hint.setStyleSheet(
            "QPushButton { color: #c0392b; font-weight: bold; border: none; padding: 3px; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        self.more_hint.clicked.connect(lambda: bar.setValue(bar.maximum()))

        def update_hint(*args):
            self.more_hint.setVisible(bar.maximum() > 0 and bar.value() < bar.maximum() - 4)

        bar.valueChanged.connect(update_hint)
        bar.rangeChanged.connect(update_hint)
        self.more_hint.setVisible(False)
        panel_layout.addWidget(self.more_hint)

        panel_layout.addWidget(self.btn_freeze)

        # In the Tasks panel the standard "Close" button is used instead.
        self.btn_close = QPushButton("Close Analysis")
        self.btn_close.clicked.connect(self.close)
        panel_layout.addWidget(self.btn_close)
        self.panel = panel

    def show_panel(self):
        """Show the options in the Tasks panel (default) or as a dock window."""
        if PANEL_MODE == "task":
            try:
                if not Gui.Control.activeDialog():
                    self.btn_close.setVisible(False)
                    Gui.Control.showDialog(SectionTaskPanel(self, self.panel))
                    self._mode = "task"
                    return
                App.Console.PrintMessage(
                    "Another task dialog is open - showing the section panel as a window.\n"
                )
            except Exception as e:
                App.Console.PrintWarning(f"Tasks panel unavailable ({e}), using a window.\n")
        self.btn_close.setVisible(True)
        self._mode = "dock"
        self.setObjectName("LiveSectionAnalyzerDock")
        pm = _logo_pixmap()
        if pm is not None:
            self.setWindowIcon(QIcon(pm))
        self.panel.setMinimumWidth(300)
        self.setWidget(self.panel)
        area = Qt.LeftDockWidgetArea if str(DOCK_AREA).lower() == "left" else Qt.RightDockWidgetArea
        Gui.getMainWindow().addDockWidget(area, self)
        self.show()

    # ------------------------------------------------------------------
    # UI event handlers (cheap: they never do geometry work directly)
    # ------------------------------------------------------------------
    def schedule_update(self, *args):
        """Many events collapse into one update per timer interval."""
        if self._closed:
            return
        # Throttle (not restart): while the user keeps dragging, updates still
        # happen every interval instead of only after the movement stops.
        if not self._timer.isActive():
            self._timer.start()

    def _fill_list(self):
        self.obj_list.blockSignals(True)
        self.obj_list.clear()
        for t in self.targets:
            item = QListWidgetItem(t.label)
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Checked if t.checked else Qt.Unchecked)
            item.setData(Qt.UserRole, t.key)
            pm = QPixmap(14, 14)
            r, g, b = (int(max(0.0, min(1.0, c)) * 255) for c in t.color[:3])
            pm.fill(QColor(r, g, b))
            item.setIcon(QIcon(pm))
            self.obj_list.addItem(item)
        self.obj_list.blockSignals(False)

    def on_target_toggled(self, item):
        if self._closed:
            return
        key = item.data(Qt.UserRole)
        for t in self.targets:
            if t.key == key:
                t.checked = item.checkState() == Qt.Checked
        self.schedule_update()

    def _check_all(self, flag):
        self.obj_list.blockSignals(True)
        for i in range(self.obj_list.count()):
            self.obj_list.item(i).setCheckState(Qt.Checked if flag else Qt.Unchecked)
        self.obj_list.blockSignals(False)
        for t in self.targets:
            t.checked = bool(flag)
        self.schedule_update()

    def on_plane_toggled(self, checked):
        if checked:
            self.schedule_update()

    def on_slider_changed(self, value):
        """Slider -> spinbox (signals blocked to avoid the feedback loop)"""
        mapped_value = (value / 100.0) * self.offset_range
        self.offset_spin.blockSignals(True)
        self.offset_spin.setValue(mapped_value)
        self.offset_spin.blockSignals(False)
        self.schedule_update()

    def on_spin_changed(self, value):
        """Spinbox -> slider (signals blocked to avoid the feedback loop)"""
        slider_value = int((value / self.offset_range) * 100)
        slider_value = max(-100, min(100, slider_value))
        self.slider.blockSignals(True)
        self.slider.setValue(slider_value)
        self.slider.blockSignals(False)
        self.schedule_update()

    def _sync_slider(self):
        value = self.offset_spin.value()
        slider_value = int((value / self.offset_range) * 100)
        slider_value = max(-100, min(100, slider_value))
        self.slider.blockSignals(True)
        self.slider.setValue(slider_value)
        self.slider.blockSignals(False)

    # ------------------------------------------------------------------
    # Gizmo
    # ------------------------------------------------------------------
    def create_gizmo(self):
        try:
            view = Gui.ActiveDocument.ActiveView
            size = max(self.x_len, self.y_len, self.z_len) * 0.18
            self.gizmo = PlaneGizmo(view, size, self._gizmo_start, self._gizmo_drag, self._gizmo_end,
                                    self._rot_start, self._rot_drag, self._rot_end)
            self.gizmo.set_visible(self.chk_gizmo.isChecked())
        except Exception as e:
            App.Console.PrintWarning(f"Gizmo unavailable: {e}\n")
            self.gizmo = None
            self.chk_gizmo.setChecked(False)
            self.chk_gizmo.setEnabled(False)

    def on_gizmo_toggled(self, *args):
        if self.gizmo:
            self.gizmo.set_visible(self.chk_gizmo.isChecked())

    def plane_axes(self, plane_type):
        """In-plane axis u and half extents (along u and along w) of the current plane."""
        u, w = self._plane_frame[0], self._plane_frame[1]

        def half(v):
            return (abs(v.x) * self.x_len + abs(v.y) * self.y_len + abs(v.z) * self.z_len) / 2 * 1.1
        return u, half(u), half(w)

    def sync_gizmo(self, plane_type, normal, plane_base):
        if not self.gizmo or self.gizmo.dragging:
            return
        u, hu, hw = self.plane_axes(plane_type)
        self.gizmo.set_pose(plane_base, normal, u, getattr(self, "_plane_w0", None))
        self.gizmo.set_extents(hu, hw)

    def _gizmo_start(self):
        self._gizmo_dragging = True
        self._gizmo_start_offset = self.offset_spin.value()

    def _gizmo_drag(self, delta):
        """Called while dragging; returns the offset change actually applied."""
        off = self._gizmo_start_offset + delta
        off = max(-self.offset_range, min(self.offset_range, off))
        self.offset_spin.blockSignals(True)
        self.offset_spin.setValue(off)
        self.offset_spin.blockSignals(False)
        self._sync_slider()
        self.schedule_update()
        return self.offset_spin.value() - self._gizmo_start_offset

    def _rot_start(self, k):
        self._gizmo_dragging = True
        sp = (self.spin_tilt_a, self.spin_tilt_b)[k]
        self._rot_start_val = sp.value()

    def _rot_drag(self, k, deg):
        sp = (self.spin_tilt_a, self.spin_tilt_b)[k]
        sp.blockSignals(True)
        sp.setValue(max(sp.minimum(), min(sp.maximum(), self._rot_start_val + deg)))
        sp.blockSignals(False)
        self.schedule_update()

    def _rot_end(self):
        self._gizmo_dragging = False
        self.schedule_update()

    def _gizmo_end(self):
        self._gizmo_dragging = False
        self.schedule_update()          # full update (hatch etc.) after the drag

    def pick_color(self, button, target):
        """Open color picker dialog"""
        color_dialog = QColorDialog()
        color_dialog.setCurrentColor(button.palette().button().color())

        run = getattr(color_dialog, "exec_", None) or getattr(color_dialog, "exec")
        if run():
            color = color_dialog.currentColor()
            r, g, b = color.red() / 255.0, color.green() / 255.0, color.blue() / 255.0
            button.setStyleSheet(
                f"background-color: rgb({color.red()}, {color.green()}, {color.blue()}); max-width: 50px;"
            )

            if target == "edge":
                self.edge_feat.ViewObject.LineColor = (r, g, b)
            elif target == "hatch":
                self.hatch_feat.ViewObject.LineColor = (r, g, b)
            elif target == "fill":
                self.section_feat.ViewObject.ShapeColor = (r, g, b)
            self.update_style()
            if target == "fill":
                self.schedule_update()          # the mesh fill takes its color at update time

    # ------------------------------------------------------------------
    # Style-only updates (no geometry work)
    # ------------------------------------------------------------------
    @staticmethod
    def _set(vo, prop, value):
        """Assign a ViewObject property only if it actually changes."""
        if getattr(vo, prop) != value:
            setattr(vo, prop, value)

    def update_style(self, *args):
        if self._closed or not self.targets:
            return
        for t in self.targets:
            if t.bg_feat is not None:
                self._set(t.bg_feat.ViewObject, "Transparency", self.spin_trans.value())
        self._set(self.edge_feat.ViewObject, "LineWidth", self.edge_width_spin.value())
        self._set(self.hatch_feat.ViewObject, "LineWidth", self.hatch_width_spin.value())
        self._set(self.hatch_feat.ViewObject, "PointSize", self.hatch_point_spin.value())
        self._apply_mesh_style()

    def _vis(self, feat, flag):
        self._set(feat.ViewObject, "Visibility", bool(flag))

    def _release(self, t):
        """Remove a target's background copy and show the original again."""
        if t.bg_feat is not None:
            try:
                doc = t.bg_feat.Document
                if doc.getObject(t.bg_feat.Name):
                    doc.removeObject(t.bg_feat.Name)
            except Exception:
                pass
            t.bg_feat = None
            t.bg_mode = None
            try:
                t.obj.ViewObject.Visibility = t.was_visible
            except Exception:
                pass

    def _sync_targets(self, use_clip=False):
        """Create / remove the background copies so they match the checked list.
        Copies are only needed for the boolean cut; GPU clipping cuts the originals."""
        doc = App.ActiveDocument
        if not doc:
            return
        trans = self.spin_trans.value() if hasattr(self, "spin_trans") else 70
        for t in self.targets:
            if use_clip:
                self._release(t)
            elif t.checked and t.bg_feat is None:
                t.was_visible = bool(t.obj.ViewObject.Visibility)
                feat = doc.addObject("Part::Feature", "Live_Section_Background")
                feat.ViewObject.ShapeColor = t.color
                feat.ViewObject.DisplayMode = "Shaded"
                feat.ViewObject.Transparency = trans
                feat.purgeTouched()
                t.bg_feat = feat
                t.bg_mode = None
            elif not t.checked and t.bg_feat is not None:
                self._release(t)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
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
        self.lbl_tilt_a.setText("Tilt about %s:" % names[0])
        self.lbl_tilt_b.setText("Tilt about %s:" % names[1])
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
            hit = mesh_section_loops(V, T, np.array((normal.x, normal.y, normal.z)), d, gap)
            if len(self._loop_cache) >= MESH_CACHE_SIZE:
                self._loop_cache.pop(next(iter(self._loop_cache)))
            self._loop_cache[key] = hit
        return hit

    def _fill_quads(self, key, loops):
        """Convex quads of the section fill (cached); see fill_trapezoids()."""
        quads = self._tri_cache.get(key)
        if quads is None:
            quads = fill_trapezoids(loops)
            if len(self._tri_cache) >= MESH_CACHE_SIZE:
                self._tri_cache.pop(next(iter(self._tri_cache)))
            self._tri_cache[key] = quads
        return quads

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
                    if fine and npts <= MESH_MAX_QUAD_POINTS:
                        tkey = (t.key, round(normal.x, 6), round(normal.y, 6), round(normal.z, 6), round(d, 4))
                        quads = self._fill_quads(tkey, loops)             # convex quads: no tearing
                        if len(quads):
                            fill.append(to3(quads.reshape(-1, 2)))        # one transform for all quads
                            fill_counts.extend([4] * len(quads))
                            fill_cols.extend([shade] * len(quads))
                    else:
                        for p in fill_polygons(loops):                    # drag / huge sections
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
        self._timer.setInterval(int(min(250, max(60, total * 300))))

        if DEBUG_TIMING:
            App.Console.PrintMessage(
                "[LSA] %s %d obj (%d skipped by bbox, %d mesh) | bg %.0f  slice %.0f  faces %.0f  "
                "fill %.0f  edges %.0f  hatch %.0f | total %.0f ms\n" % (
                    ("drag" if quick else "full") + ("/mesh" if mesh_mode else ""),
                    len(active), self._skipped, self._meshed,
                    tm["bg"] * 1e3, tm["slice"] * 1e3, tm["faces"] * 1e3,
                    tm["fill"] * 1e3, tm["edges"] * 1e3, tm["hatch"] * 1e3, total * 1e3))

    # ------------------------------------------------------------------
    # Freeze
    # ------------------------------------------------------------------
    @staticmethod
    def _has_shape(feat):
        return feat is not None and feat.Shape is not None and not feat.Shape.isNull()

    def freeze_section(self):
        """Freeze current section view using SimpleCopy with folder"""
        doc = App.ActiveDocument
        if not doc or not any(t.checked for t in self.targets):
            return

        progress = None
        try:
            progress = QProgressDialog("Freezing section ...", None, 0, 0, Gui.getMainWindow())
            progress.setWindowTitle("Live Section Analyzer")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setCancelButton(None)
            progress.show()
            QApplication.processEvents()
        except Exception:
            progress = None
        try:
            # Make sure everything is up to date (including hatch)
            self._timer.stop()
            self.update_section(force_exact=True)      # real geometry, even in mesh mode

            plane_type, normal, plane_base, center_pos = self.get_plane()
            offset_val = self.offset_spin.value()

            tilt = self._tilt_text()
            group_name = f"Frozen_Section_{plane_type}{tilt}_Offset_{offset_val:.1f}"

            # Delete old frozen group
            if self.frozen_group and doc.getObject(self.frozen_group.Name):
                for obj in list(self.frozen_group.Group):
                    try:
                        doc.removeObject(obj.Name)
                    except Exception:
                        pass
                try:
                    doc.removeObject(self.frozen_group.Name)
                except Exception:
                    pass

            self.frozen_group = doc.addObject("App::DocumentObjectGroup", group_name)
            self.frozen_group.Label = f"Frozen Section ({plane_type}{tilt.replace('_', ' ')}, Offset: {offset_val:.1f} mm)"

            copied_objects = []

            # 1. Background of every sectioned object: always a real boolean cut
            #    here, because the live GPU clip does not change the geometry.
            for t in self.targets:
                if not t.checked:
                    continue
                bg_shape = self.compute_cut_solid(
                    t.shape, plane_type, normal, plane_base, self.chk_mirror_bg.isChecked()
                )
                if bg_shape is not None and not bg_shape.isNull():
                    bg_copy = doc.addObject("Part::Feature", f"Section_Background_{plane_type}")
                    bg_copy.Label = f"{t.label} ({plane_type})"
                    bg_copy.Shape = bg_shape
                    bg_copy.ViewObject.ShapeColor = t.color
                    bg_copy.ViewObject.DisplayMode = "Shaded"
                    bg_copy.ViewObject.Transparency = self.spin_trans.value()
                    copied_objects.append(bg_copy)

            # 2. Section fill
            if self.section_feat.ViewObject.Visibility and self._has_shape(self.section_feat):
                fill_copy = doc.addObject("Part::Feature", f"Section_Fill_{plane_type}")
                fill_copy.Shape = self.section_feat.Shape
                fill_copy.Placement = self.section_feat.Placement
                fill_copy.ViewObject.ShapeColor = self.section_feat.ViewObject.ShapeColor
                fill_copy.ViewObject.DiffuseColor = self.section_feat.ViewObject.DiffuseColor
                fill_copy.ViewObject.LineColor = self.section_feat.ViewObject.LineColor
                fill_copy.ViewObject.LineWidth = self.section_feat.ViewObject.LineWidth
                fill_copy.ViewObject.DisplayMode = "Shaded"
                fill_copy.ViewObject.Transparency = 0
                copied_objects.append(fill_copy)

            # 3. Edge lines
            if self.edge_feat.ViewObject.Visibility and self._has_shape(self.edge_feat):
                edge_copy = doc.addObject("Part::Feature", f"Section_Edges_{plane_type}")
                edge_copy.Shape = self.edge_feat.Shape
                edge_copy.Placement = self.edge_feat.Placement
                edge_copy.ViewObject.LineColor = self.edge_feat.ViewObject.LineColor
                edge_copy.ViewObject.LineWidth = self.edge_feat.ViewObject.LineWidth
                edge_copy.ViewObject.DisplayMode = "Wireframe"
                edge_copy.ViewObject.PointSize = 1
                copied_objects.append(edge_copy)

            # 4. Hatch lines
            if self.hatch_feat.ViewObject.Visibility and self._has_shape(self.hatch_feat):
                hatch_copy = doc.addObject("Part::Feature", f"Section_Hatch_{plane_type}")
                hatch_copy.Shape = self.hatch_feat.Shape
                hatch_copy.Placement = self.hatch_feat.Placement
                hatch_copy.ViewObject.LineColor = self.hatch_feat.ViewObject.LineColor
                hatch_copy.ViewObject.LineWidth = self.hatch_feat.ViewObject.LineWidth
                hatch_copy.ViewObject.DisplayMode = "Wireframe"
                hatch_copy.ViewObject.PointSize = self.hatch_point_spin.value()
                copied_objects.append(hatch_copy)

            if not copied_objects:
                if progress is not None:
                    progress.close()
                QMessageBox.warning(None, "Warning", "No section elements found to copy!")
                try:
                    doc.removeObject(self.frozen_group.Name)
                except Exception:
                    pass
                self.frozen_group = None
                return

            for obj in copied_objects:
                self.frozen_group.addObject(obj)

            self.frozen_group.ViewObject.Visibility = True

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.frozen_group)

            for obj in copied_objects:
                obj.purgeTouched()
            self.frozen_group.purgeTouched()

            if progress is not None:
                progress.close()
            QMessageBox.information(
                None,
                "Success",
                f"Section successfully frozen!\n"
                f"Group name: {self.frozen_group.Label}\n"
                f"Element count: {len(copied_objects)}\n"
                f"Plane: {plane_type}, Offset: {offset_val:.1f} mm\n\n"
                f"📁 Contents:\n" +
                "\n".join([f"  • {obj.Label}" for obj in copied_objects])
            )

        except Exception as e:
            App.Console.PrintError(f"Freeze Error: {str(e)}\n")
            QMessageBox.critical(None, "Error", f"Error while freezing section:\n{str(e)}")
        finally:
            if progress is not None:
                progress.close()
            self.schedule_update()                     # back to the live (mesh or exact) view

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


