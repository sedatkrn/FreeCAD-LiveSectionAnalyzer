class _GizmoEventFilter(QObject):
    """Qt event filter: forwards mouse events of the 3D view to the gizmo."""

    def __init__(self, gizmo):
        super().__init__()
        self.gizmo = gizmo

    def eventFilter(self, obj, ev):
        try:
            return bool(self.gizmo.handle_qt_event(ev))
        except Exception as e:
            App.Console.PrintError(f"Gizmo error: {e}\n")
            return False


class PlaneGizmo:
    """Double-headed arrow + plane outline, draggable along the plane normal.

    Dragging is implemented with a Qt event filter on the 3D viewport. It sees
    mouse events before FreeCAD's navigation/selection code, so a drag on the
    arrow neither rotates the view nor changes the selection.
    """
    PICK_PIXELS = 22
    COLOR = (1.0, 0.55, 0.0)
    COLOR_HOT = (1.0, 0.9, 0.2)

    RING_RES = 12                             # segments of the short arc
    ARC_HALF_DEG = 22.0                       # arc = handle +- this
    HANDLE_DEG = (55.0, 35.0)                 # handle position on each ring (off the arrow)
    RING_COLORS = ((0.95, 0.15, 0.15), (0.2, 0.4, 1.0))
    RING_HOT = ((1.0, 0.6, 0.6), (0.6, 0.75, 1.0))

    def __init__(self, view, size, on_start, on_drag, on_end,
                 on_rot_start=None, on_rot_drag=None, on_rot_end=None):
        self.on_rot_start, self.on_rot_drag, self.on_rot_end = on_rot_start, on_rot_drag, on_rot_end
        self.rot_active = None                    # 0 / 1 while a ring is being dragged
        self._rot = None
        self.fixed_axis = App.Vector(0, 0, 1)
        self.view = view
        self.viewer = view.getViewer()
        self.widget = view.graphicsView().viewport()
        self.size = size
        self.on_start = on_start
        self.on_drag = on_drag
        self.on_end = on_end

        self.origin = App.Vector(0, 0, 0)
        self.normal = App.Vector(0, 1, 0)
        self.u_axis = App.Vector(1, 0, 0)
        self.w_axis = App.Vector(0, 0, 1)
        self.half_u = size
        self.half_w = size
        self.dragging = False
        self.hot = False
        self._anchor = None
        self._grab_t = 0.0
        self._removed = False

        self._build_scene()
        self.sg = self._render_group(view.getSceneGraph())
        # first child: the scene-wide clip plane (added later) comes after it,
        # so the gizmo itself is never clipped
        self.sg.insertChild(self.root, 0)

        self._filter = _GizmoEventFilter(self)
        self.widget.installEventFilter(self._filter)

    # -- scene ---------------------------------------------------------
    @staticmethod
    def _render_group(sg):
        """Node that is actually rendered. Newer FreeCAD builds return a parent
        separator from getSceneGraph() and ignore children appended after the
        selection root, so attach to the SoFCUnifiedSelection node itself."""
        if sg.getTypeId().getName() == "SoFCUnifiedSelection":
            return sg
        for i in range(sg.getNumChildren()):
            child = sg.getChild(i)
            if child.getTypeId().getName() == "SoFCUnifiedSelection":
                return child
        return sg

    def _build_scene(self):
        L = self.size
        r = L * 0.035
        head_r = L * 0.11
        head_h = L * 0.26
        self.reach = L + head_h
        self._head_h = head_h
        self.k = 1.0                          # handle scale, set while rendering (_on_render)

        # SoAnnotation renders after the scene with depth test off, so the gizmo
        # stays visible even behind semi-transparent geometry
        self.root = coin.SoAnnotation()
        self.switch = coin.SoSwitch()
        self.switch.whichChild.setValue(coin.SO_SWITCH_ALL)
        self.root.addChild(self.switch)

        # Computes the handle scale from the camera of the frame being drawn, so
        # zooming never shows a frame with the old size (no popping).
        self._render_cb = coin.SoCallback()
        self._render_cb.setCallback(self._on_render)
        self.switch.addChild(self._render_cb)

        grp = coin.SoSeparator()
        self.switch.addChild(grp)

        # never let FreeCAD's selection / preselection pick the gizmo
        pick = coin.SoPickStyle()
        pick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        grp.addChild(pick)

        lm = coin.SoLightModel()
        lm.model.setValue(coin.SoLightModel.BASE_COLOR)
        grp.addChild(lm)

        self.tf = coin.SoTransform()
        grp.addChild(self.tf)

        self.color = coin.SoBaseColor()
        self.color.rgb.setValue(*self.COLOR)

        # plane outline (lies in the local XZ plane, local Y = normal)
        outline = coin.SoSeparator()
        grp.addChild(outline)
        outline.addChild(self.color)
        ds = coin.SoDrawStyle()
        ds.lineWidth.setValue(2.0)
        outline.addChild(ds)
        self.coords = coin.SoCoordinate3()
        outline.addChild(self.coords)
        ls = coin.SoIndexedLineSet()
        ls.coordIndex.setValues(0, 6, [0, 1, 2, 3, 0, -1])
        outline.addChild(ls)
        self.set_extents(L, L)

        # rotation rings (world-oriented, centred on the plane origin)
        self.ring_rs = (L * 1.0, L * 1.0)
        rings = coin.SoAnnotation()
        self.switch.addChild(rings)
        rpick = coin.SoPickStyle()
        rpick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        rings.addChild(rpick)
        rlm = coin.SoLightModel()
        rlm.model.setValue(coin.SoLightModel.BASE_COLOR)
        rings.addChild(rlm)
        self.rtf = coin.SoTransform()
        rings.addChild(self.rtf)
        self.ring_scale = coin.SoScale()          # rings are built at k = 1 and scaled here
        rings.addChild(self.ring_scale)
        self.ring_col, self.ring_coords, self.ring_handle = [], [], []
        for k in range(2):
            sep = coin.SoSeparator()
            rings.addChild(sep)
            col = coin.SoBaseColor()
            col.rgb.setValue(*self.RING_COLORS[k])
            sep.addChild(col)
            rds = coin.SoDrawStyle()
            rds.lineWidth.setValue(3.0)
            sep.addChild(rds)
            co = coin.SoCoordinate3()
            sep.addChild(co)
            ln = coin.SoLineSet()
            ln.numVertices.setValue(self.RING_RES + 1)
            sep.addChild(ln)
            ht = coin.SoTransform()
            sep.addChild(ht)
            ball = coin.SoSphere()
            ball.radius.setValue(L * 0.06)
            sep.addChild(ball)
            self.ring_col.append(col)
            self.ring_coords.append(co)
            self.ring_handle.append(ht)

        # arrow, drawn on top of everything
        arrow = coin.SoSeparator()
        grp.addChild(arrow)
        depth = coin.SoDepthBuffer()
        depth.test.setValue(False)
        arrow.addChild(depth)
        self.arrow_scale = coin.SoScale()
        arrow.addChild(self.arrow_scale)
        arrow.addChild(self.color)

        shaft = coin.SoCylinder()
        shaft.radius.setValue(r)
        shaft.height.setValue(2 * L)
        arrow.addChild(shaft)

        for sign in (1, -1):
            sep = coin.SoSeparator()
            t = coin.SoTransform()
            t.translation.setValue(0, sign * (L + head_h / 2), 0)
            if sign < 0:
                t.rotation.setValue(coin.SbRotation(coin.SbVec3f(1, 0, 0), math.pi))
            sep.addChild(t)
            cone = coin.SoCone()
            cone.bottomRadius.setValue(head_r)
            cone.height.setValue(head_h)
            sep.addChild(cone)
            arrow.addChild(sep)

    def _apply_scale(self, k):
        """Scale arrow and rotation handles by k (the plane outline is not scaled)."""
        L = self.size
        self.k = k
        self.arrow_scale.scaleFactor.setValue(k, k, k)
        self.ring_scale.scaleFactor.setValue(k, k, k)
        self.reach = (L + self._head_h) * k
        self.ring_rs = (L * k, L * k)

    def _on_render(self, _data, action):
        """SoCallback: keep the arrow GIZMO_SCREEN_PX pixels long (half length)."""
        if GIZMO_SCREEN_PX <= 0 or not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            return
        try:
            state = action.getState()
            h = max(coin.SoViewportRegionElement.get(state).getViewportSizePixels()[1], 1)
            o = self.origin
            world = coin.SoViewVolumeElement.get(state).getWorldToScreenScale(
                coin.SbVec3f(o.x, o.y, o.z), GIZMO_SCREEN_PX / h)
            k = world / max(self.size, 1e-9)
        except Exception:
            return
        if k > 0 and abs(k - self.k) > 1e-4 * self.k:
            self._apply_scale(k)

    def set_extents(self, half_u, half_w):
        self.half_u, self.half_w = half_u, half_w
        pts = [(-half_u, 0, -half_w), (half_u, 0, -half_w),
               (half_u, 0, half_w), (-half_u, 0, half_w)]
        self.coords.point.setValues(0, 4, pts)

    def _ring_defs(self):
        """(axis, first basis direction) of the two rotation rings."""
        return ((self.u_axis, self.normal), (self.fixed_axis, self.u_axis))

    def _ring_point(self, k, deg, R=None):
        a, e1 = self._ring_defs()[k]
        e2 = a.cross(e1)
        t = math.radians(deg)
        R = self.ring_rs[k] if R is None else R
        return e1 * (math.cos(t) * R) + e2 * (math.sin(t) * R)

    def _update_rings(self):
        self.rtf.translation.setValue(self.origin.x, self.origin.y, self.origin.z)
        for k in range(2):
            hd = self.HANDLE_DEG[k]
            pts = []
            for i in range(self.RING_RES + 1):
                v = self._ring_point(k, hd - self.ARC_HALF_DEG + 2 * self.ARC_HALF_DEG * i / self.RING_RES,
                                     self.size)
                pts.append((v.x, v.y, v.z))
            self.ring_coords[k].point.setValues(0, len(pts), pts)
            h = self._ring_point(k, hd, self.size)
            self.ring_handle[k].translation.setValue(h.x, h.y, h.z)

    def set_pose(self, origin, normal, u_axis, fixed_axis=None):
        """origin/normal/u_axis are App.Vector; local X -> u, local Y -> normal."""
        if fixed_axis is not None:
            self.fixed_axis = App.Vector(fixed_axis)
        self.normal = App.Vector(normal)
        n = App.Vector(normal)
        u = App.Vector(u_axis)
        w = u.cross(n)
        self.u_axis, self.w_axis = u, w
        m = App.Matrix()
        m.A11, m.A21, m.A31 = u.x, u.y, u.z
        m.A12, m.A22, m.A32 = n.x, n.y, n.z
        m.A13, m.A23, m.A33 = w.x, w.y, w.z
        q = App.Rotation(m).Q
        self.tf.rotation.setValue(coin.SbRotation(q[0], q[1], q[2], q[3]))
        self._set_origin(origin)

    def _set_origin(self, origin):
        self.origin = App.Vector(origin)
        self.tf.translation.setValue(origin.x, origin.y, origin.z)
        self._update_rings()

    def set_visible(self, flag):
        self.switch.whichChild.setValue(coin.SO_SWITCH_ALL if flag else coin.SO_SWITCH_NONE)

    def is_visible(self):
        return self.switch.whichChild.getValue() != coin.SO_SWITCH_NONE

    # -- picking math (x, y: widget pixels, origin top-left) ------------
    def _volume(self):
        rm = self.viewer.getSoRenderManager()
        vp = rm.getViewportRegion()
        return rm.getCamera().getViewVolume(vp.getViewportAspectRatio())

    def _squeeze(self):
        """In a portrait viewport Coin shrinks the picture by width/height (ADJUST_CAMERA);
        the view volume does not include that, so it is applied here."""
        rm = self.viewer.getSoRenderManager()
        return min(rm.getViewportRegion().getViewportAspectRatio(), 1.0)

    def _norm(self, x, y):
        a = max(self._squeeze(), 1e-3)
        nx = x / max(self.widget.width(), 1)
        ny = 1.0 - y / max(self.widget.height(), 1)
        return coin.SbVec2f(0.5 + (nx - 0.5) / a, 0.5 + (ny - 0.5) / a)

    def _scr(self, p, vv=None):
        """World point -> widget pixels (origin top-left)."""
        vv = vv or self._volume()
        s = vv.projectToScreen(coin.SbVec3f(p.x, p.y, p.z)).getValue()
        a = self._squeeze()
        return ((0.5 + (s[0] - 0.5) * a) * self.widget.width(),
                (1.0 - (0.5 + (s[1] - 0.5) * a)) * self.widget.height())

    @staticmethod
    def _dist_to_segment(x, y, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 < 1e-6 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / seg2))
        return math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy))

    @staticmethod
    def _inside_polygon(x, y, pts):
        """Point in convex polygon (screen space)."""
        sign = 0
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
            if abs(cross) < 1e-9:
                continue
            s_ = 1 if cross > 0 else -1
            if sign == 0:
                sign = s_
            elif s_ != sign:
                return False
        return True

    def hit_ring(self, x, y):
        """Index of the rotation handle (ball) under the pixel, or None."""
        if self.on_rot_drag is None or not self.is_visible():
            return None
        vv = self._volume()
        w, h = self.widget.width(), self.widget.height()
        best, best_d = None, self.PICK_PIXELS * 0.8
        for k in range(2):
            p = self.origin + self._ring_point(k, self.HANDLE_DEG[k])
            sx, sy = self._scr(p, vv)
            d = math.hypot(x - sx, y - sy)
            if d < best_d:
                best, best_d = k, d
        return best

    def _ring_angle(self, x, y, grazing_ok=False):
        """Angle (rad) of the mouse ray hit on the ring plane, in the grabbed basis."""
        a, e1, e2 = self._rot["basis"]
        p0, p1 = self._volume().projectPointToLine(self._norm(x, y))
        q0 = App.Vector(*p0.getValue())
        d = App.Vector(*p1.getValue()) - q0
        den = d.dot(a)
        if abs(den) < (1e-6 if grazing_ok else 0.25) * max(d.Length, 1e-9):
            return None
        t = (self._rot["center"] - q0).dot(a) / den
        v = q0 + d * t - self._rot["center"]
        return math.atan2(v.dot(e2), v.dot(e1))

    def _start_tangent(self, x, y):
        """Edge-on ring: map mouse motion along the ring's screen tangent to an angle."""
        a, e1, e2 = self._rot["basis"]
        vv = self._volume()
        w, h = self.widget.width(), self.widget.height()
        R = self.ring_rs[self._rot["k"]]
        c = self._rot["center"]

        def scr(t):
            return self._scr(c + e1 * (math.cos(t) * R) + e2 * (math.sin(t) * R), vv)

        best_t = math.radians(self.HANDLE_DEG[self._rot["k"]])
        h_ = 0.05
        (ax, ay), (bx, by) = scr(best_t - h_), scr(best_t + h_)
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-3:
            L = 1.0
            bx, by, ax, ay = 1.0, 0.0, 0.0, 0.0
        self._rot["tan"] = ((bx - ax) / L, (by - ay) / L, max(L / (2 * h_), 1e-3), x, y)

    def _set_ring_hot(self, k, flag):
        for i in range(2):
            on = flag and i == k
            self.ring_col[i].rgb.setValue(*(self.RING_HOT[i] if on else self.RING_COLORS[i]))

    def hit_test(self, x, y):
        vv = self._volume()
        w, h = self.widget.width(), self.widget.height()

        def to_px(p):
            return self._scr(p, vv)

        # arrow
        a = to_px(self.origin - self.normal * self.reach)
        b = to_px(self.origin + self.normal * self.reach)
        if self._dist_to_segment(x, y, a, b) <= self.PICK_PIXELS:
            return True

        if not GIZMO_PICK_PLANE:
            return False

        # plane: outline band and surface
        u = self.u_axis * self.half_u
        v = self.w_axis * self.half_w
        o = self.origin
        corners = [to_px(o - u - v), to_px(o + u - v), to_px(o + u + v), to_px(o - u + v)]
        if self._inside_polygon(x, y, corners):
            return True
        return any(
            self._dist_to_segment(x, y, corners[i], corners[(i + 1) % 4]) <= self.PICK_PIXELS * 0.6
            for i in range(4)
        )

    def _axis_param(self, x, y, anchor):
        """t of the axis point (anchor + t*normal) closest to the mouse ray."""
        p0, p1 = self._volume().projectPointToLine(self._norm(x, y))
        q0 = App.Vector(*p0.getValue())
        d = App.Vector(*p1.getValue()) - q0
        n = self.normal
        w0 = anchor - q0
        b = n.dot(d)
        c = d.dot(d)
        denom = c - b * b            # n.n == 1
        if abs(denom) < 1e-9 * max(c, 1.0):
            return None              # looking straight along the axis
        return (b * d.dot(w0) - c * n.dot(w0)) / denom

    def _set_hot(self, flag):
        if flag != self.hot:
            self.hot = flag
            self.color.rgb.setValue(*(self.COLOR_HOT if flag else self.COLOR))

    # -- Qt events -----------------------------------------------------
    @staticmethod
    def _pos(ev):
        p = ev.position() if hasattr(ev, "position") else ev.pos()
        return p.x(), p.y()

    def handle_qt_event(self, ev):
        """Returns True when the event was consumed."""
        if self._removed:
            return False
        et = ev.type()

        if et == QEvent.MouseButtonPress:
            if ev.button() != Qt.LeftButton or ev.modifiers() != Qt.NoModifier:
                return False
            if not self.is_visible():
                return False
            x, y = self._pos(ev)
            k = self.hit_ring(x, y)
            if k is not None:
                a, e1 = self._ring_defs()[k]
                self._rot = {"k": k, "center": App.Vector(self.origin),
                             "basis": (App.Vector(a), App.Vector(e1), a.cross(e1)), "last": None,
                             "total": 0.0}
                ang = self._ring_angle(x, y)
                if ang is None:
                    self._start_tangent(x, y)          # ring seen edge-on: drag along its screen tangent
                else:
                    self._rot["last"] = ang
                self.rot_active = k
                self._set_ring_hot(k, True)
                self.on_rot_start(k)
                return True
            if not self.hit_test(x, y):
                return False
            t = self._axis_param(x, y, self.origin)
            if t is None:
                return False
            self.dragging = True
            self._anchor = App.Vector(self.origin)
            self._grab_t = t
            self._set_hot(True)
            self.on_start()
            return True

        if et == QEvent.MouseMove:
            x, y = self._pos(ev)
            if self.rot_active is not None:
                if "tan" in self._rot:
                    tx, ty, ppr, x0, y0 = self._rot["tan"]
                    self._rot["total"] = ((x - x0) * tx + (y - y0) * ty) / ppr
                    self.on_rot_drag(self.rot_active, math.degrees(self._rot["total"]))
                    return True
                ang = self._ring_angle(x, y)
                if ang is not None:
                    dlt = ang - self._rot["last"]
                    dlt = (dlt + math.pi) % (2 * math.pi) - math.pi      # unwrap
                    self._rot["last"] = ang
                    self._rot["total"] += dlt
                    self.on_rot_drag(self.rot_active, math.degrees(self._rot["total"]))
                return True
            if self.dragging:
                t = self._axis_param(x, y, self._anchor)
                if t is not None:
                    applied = self.on_drag(t - self._grab_t)
                    self._set_origin(self._anchor + self.normal * applied)
                return True
            if self.is_visible() and not ev.buttons():
                self._set_hot(self.hit_test(x, y))
                self._set_ring_hot(self.hit_ring(x, y), True) if self.hit_ring(x, y) is not None \
                    else self._set_ring_hot(-1, False)
            return False

        if et == QEvent.MouseButtonRelease and self.rot_active is not None:
            self.rot_active = None
            self._rot = None
            self._set_ring_hot(-1, False)
            self.on_rot_end()
            return True

        if et == QEvent.MouseButtonRelease and self.dragging:
            self.dragging = False
            self._set_hot(False)
            self.on_end()
            return True

        return False

    # -- cleanup -------------------------------------------------------
    def remove(self):
        if self._removed:
            return
        self._removed = True
        self.dragging = False
        try:
            self.widget.removeEventFilter(self._filter)
        except Exception:
            pass
        try:
            self.sg.removeChild(self.root)
        except Exception:
            pass


