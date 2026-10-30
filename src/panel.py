class _PanelMixin:
    """Options panel, its event handlers, gizmo callbacks and styles."""

    def init_ui(self):
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(4, 4, 4, 4)

        # --- HEADER: logo, title, version ---
        header = QHBoxLayout()
        pm = _logo_pixmap(32)
        if pm is not None:
            logo = QLabel()
            logo.setPixmap(pm)
            logo.setFixedSize(36, 36)
            header.addWidget(logo)
        title = QLabel(
            "<b>Live Section Analyzer</b><br>"
            "<span style='color: #777; font-size: 10px;'>v%s</span>" % VERSION
        )
        header.addWidget(title, 1)
        self.btn_toolbar = QToolButton()
        self.btn_toolbar.setAutoRaise(True)
        self.btn_toolbar.clicked.connect(self.toggle_toolbar)
        self._sync_toolbar_button()
        header.addWidget(self.btn_toolbar)
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
            b.setMinimumWidth(0)
            b.clicked.connect(slot)
            row.addWidget(b)
        layout.addLayout(row)
        self._fill_list()
        self.obj_list.itemChanged.connect(self.on_target_toggled)

        # --- SECTION PLANE ---
        layout = section("Section Plane")
        plane_layout = QHBoxLayout()
        plane_layout.addWidget(QLabel("Plane:"))
        self.combo_plane = QComboBox()
        self.combo_plane.addItems(["XY", "XZ", "YZ"])
        self.combo_plane.setCurrentText("YZ")
        plane_layout.addWidget(self.combo_plane, 1)
        layout.addLayout(plane_layout)

        self.chk_gizmo = QCheckBox("Show plane gizmo")
        self.chk_gizmo.setToolTip("Drag the arrow in the 3D view to move the plane")
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
        self.btn_tilt_reset.setMinimumWidth(0)
        self.btn_tilt_reset.setToolTip("Back to the axis-aligned plane")
        self.btn_tilt_reset.clicked.connect(self.reset_tilt)
        tilt_layout.addWidget(self.btn_tilt_reset)
        layout.addLayout(tilt_layout)

        # Section engine (part of the Section Plane group)
        mesh_ok = HAS_NP and self.mesh_switch is not None
        self.chk_mesh = QCheckBox("Mesh-based section")
        self.chk_mesh.setChecked(bool(MESH_SECTION) and mesh_ok)
        self.chk_mesh.setEnabled(mesh_ok)
        self.chk_mesh.setToolTip(
            "Builds outline, hatch and fill from the tessellation instead of OpenCascade: "
            "much faster on big or curved parts, but curves are polygonal. Each object gets its own hatch angle and "
            "spacing. Freeze Section always uses exact geometry."
        )
        layout.addWidget(self.chk_mesh)

        # Offset (part of the Section Plane group)
        max_dim = max(self.x_len, self.y_len, self.z_len)
        self.offset_range = max(max_dim * 0.6, 10.0)

        offset_layout = QHBoxLayout()
        offset_layout.addWidget(QLabel("Offset:"))

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

        range_label = QLabel(f"<i>Range: ±{self.offset_range:.1f} mm</i>")
        range_label.setStyleSheet("color: #666; font-size: 10px;")
        range_label.setToolTip("Based on the object size")
        layout.addWidget(range_label)

        # Background (part of the Section Plane group)
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
        self.chk_mirror_bg = QCheckBox("Mirror Background")
        self.chk_mirror_bg.setToolTip("Show the opposite half")
        self.chk_mirror_bg.setChecked(False)
        mirror_layout.addWidget(self.chk_mirror_bg)
        mirror_layout.addStretch()
        layout.addLayout(mirror_layout)

        # --- SECTION FILL ---
        layout = section("Section Fill", expanded=False)

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
        layout = section("Edge Lines", expanded=False)

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
        layout = section("Hatch", expanded=False)

        self.chk_hatch = QCheckBox("Enable Hatch")
        self.chk_hatch.setChecked(True)
        layout.addWidget(self.chk_hatch)

        spacing_layout = QHBoxLayout()
        spacing_layout.addWidget(QLabel("Spacing (mm):"))
        self.spin_spacing = QDoubleSpinBox()
        self.spin_spacing.setRange(0.5, 500.0)
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
        self.btn_freeze = QPushButton("❄️ Freeze Section")
        self.btn_freeze.setToolTip("Simple copy of the section, in its own folder")
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
        self.combo_plane.currentIndexChanged.connect(self.schedule_update)

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

    def _sync_toolbar_button(self):
        try:
            on = toolbar_installed()
        except Exception:
            on = False
        self.btn_toolbar.setText("− Toolbar" if on else "+ Toolbar")
        self.btn_toolbar.setToolTip(
            f"Remove the '{TOOLBAR_NAME}' toolbar" if on else
            f"Add a '{TOOLBAR_NAME}' toolbar with a button for this macro (all workbenches, "
            f"shortcut {TOOLBAR_SHORTCUT} if free)")

    def toggle_toolbar(self):
        try:
            if toolbar_installed():
                uninstall_toolbar()
                App.Console.PrintMessage(f"Live Section: '{TOOLBAR_NAME}' toolbar removed.\n")
            else:
                cmd, key = install_toolbar()
                note = f", shortcut {key}" if key else f" ({TOOLBAR_SHORTCUT} is taken, no shortcut)"
                App.Console.PrintMessage(f"Live Section: '{TOOLBAR_NAME}' toolbar added{note}.\n")
        except Exception as e:
            QMessageBox.warning(None, "Live Section", f"Could not change the toolbar: {e}")
        self._sync_toolbar_button()

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
        self.panel.setMinimumWidth(PANEL_WIDTH)
        self.setWidget(self.panel)
        area = Qt.LeftDockWidgetArea if str(DOCK_AREA).lower() == "left" else Qt.RightDockWidgetArea
        mw = Gui.getMainWindow()
        mw.addDockWidget(area, self)
        self.show()
        # Otherwise the dock inherits whatever width its side of the window last had
        mw.resizeDocks([self], [PANEL_WIDTH], Qt.Horizontal)

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
        self._sync_clip_transparency(self.clip_ok and self.chk_gpu_clip.isChecked())
        self._set(self.edge_feat.ViewObject, "LineWidth", self.edge_width_spin.value())
        self._set(self.hatch_feat.ViewObject, "LineWidth", self.hatch_width_spin.value())
        self._set(self.hatch_feat.ViewObject, "PointSize", self.hatch_point_spin.value())
        self._apply_mesh_style()

    def _vis(self, feat, flag):
        self._set(feat.ViewObject, "Visibility", bool(flag))

    def _sync_clip_transparency(self, use_clip):
        """GPU clipping shows the originals, so the transparency goes on them directly.
        0 % leaves an object as the user made it."""
        trans = self.spin_trans.value()
        for t in self.targets:
            if use_clip and t.checked and trans > 0:
                self._apply_transparency(t, trans)
            else:
                self._restore_transparency(t)

    def _apply_transparency(self, t, trans):
        try:
            vo = t.obj.ViewObject
            if t.saved_look is None:
                t.saved_look = {p: getattr(vo, p) for p in ("Transparency", "ShapeAppearance", "DiffuseColor")
                                if hasattr(vo, p)}
            self._set(vo, "Transparency", trans)
        except Exception:
            pass

    def _restore_transparency(self, t):
        """Put back the object's own look, per-face colors included."""
        if t.saved_look is None:
            return
        look, t.saved_look = t.saved_look, None
        try:
            for prop, value in look.items():
                setattr(t.obj.ViewObject, prop, value)
        except Exception:
            pass

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
        self._sync_clip_transparency(use_clip)
        for t in self.targets:
            if use_clip:
                self._release(t)
            elif t.checked and t.bg_feat is None:
                t.was_visible = bool(t.obj.ViewObject.Visibility)
                feat = doc.addObject("Part::Feature", "Live_Section_Background")
                _hide_in_tree(feat)
                feat.ViewObject.ShapeColor = t.color
                feat.ViewObject.DisplayMode = "Shaded"
                feat.ViewObject.Transparency = trans
                feat.purgeTouched()
                t.bg_feat = feat
                t.bg_mode = None
            elif not t.checked and t.bg_feat is not None:
                self._release(t)



