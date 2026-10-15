# Changelog

## Unreleased

- The plane gizmo is grabbed only by its arrow (`GIZMO_PICK_PLANE = False`); faces under the plane can be selected again.
- GPU clipping no longer swaps the sectioned objects for `Live_Section_Background` copies: the originals stay visible and selectable (e.g. for Assembly joints). Copies are only used for the boolean cut.
- The section follows moving parts (Assembly solver, placement edits): moved meshes are transformed instead of re-tessellated; changed shapes are rebuilt. `FOLLOW_SCENE`, `SCENE_SETTLE_MS`.

## 0.5.2 – 2026-10-13

- Tilted section plane: red and blue rotation rings on the gizmo (drag to tilt) and two angle spin boxes (rotation about the plane's in-plane axes) plus Reset; works with mesh and exact engines, hatch, cut solid and freeze. Offset is measured along the tilted normal.
- Gizmo rotation handles are short arcs with a grab ball; picking and dragging fixed in portrait viewports.
- Faster section fill in the finished mesh view: the quads are transformed and handed to Coin as one array instead of thousands of small ones; `fill_trapezoids` is vectorized.

## 0.5.1 – 2026-10-12

- Fine tessellation is now skipped for parts whose coarse mesh was slow or large (`MESH_FINE_MAX_TRIS`, `MESH_FINE_PREDICT`), and the tolerance never goes below `MESH_TOL_MIN` (0.01 mm). Fixes multi-minute stalls on threaded parts seen with FreeCAD 1.1.4 (OCC 7.8).

## 0.5.0 – 2026-10-12

- Section fill of the finished (non-drag) mesh view is cut into convex quads (`fill_trapezoids`, cached) instead of one bridged polygon, so Coin's polygon tessellator can no longer tear sections with many holes. Sections above `MESH_MAX_QUAD_POINTS` and the drag preview keep the polygon fill.
- Background transparency now defaults to 0 (faster, clearer); wait cursor during full updates and a progress dialog while *Freeze Section* runs.
- Mesh section safety limits (`MESH_FINE_MAX_TRIS`, `MESH_FINE_BUDGET_S`, `MESH_MAX_FILL_POINTS`, `MESH_MAX_HOLES`): heavy parts (threads, large bodies) keep the coarse mesh instead of a fine tessellation, huge sections skip the fill, keyhole search is bounded. Fixes FreeCAD freezing when the option was ticked on a big assembly.
- `DEBUG_TIMING`: per-update timings (background / slice / faces / fill / edges / hatch) in the Report view.
- Objects the plane does not touch are skipped by a bounding-box test before slicing (~3x faster on a 60-solid demo).
- Fast drag preview: slow objects are outlined from their tessellation (numpy) while the plane moves; the exact section follows when you let go. `FAST_PREVIEW_MS` sets the threshold. Needs numpy (bundled with FreeCAD); falls back to exact slicing without it.
- Adaptive update delay: the wait before the next update grows with the duration of the last one (60-250 ms).
- Mesh-based section (*Mesh-based section* checkbox in the Section Plane group, on by default, `MESH_SECTION` for the start state): outline, hatch and fill are built from the tessellation with numpy and drawn as Coin nodes, no OpenCascade slicing. Hatch and fill also follow the plane while dragging; mesh tolerance is coarse while dragging and fine afterwards. Curves are polygonal at coarse tolerance. Freeze Section still uses exact geometry.
- Each sectioned object is hatched at its own angle and spacing (`HATCH_STYLES`: 45, 135, 0, 90 degrees...), in the exact section, the mesh section and Freeze alike, so touching parts can be told apart. A single entry, e.g. `((45.0, 1.0),)`, gives the old one-direction hatch. The hatch lines are now anchored at the origin (a pattern that does not shift with the object), and the exact hatch costs one boolean per object instead of one in total.
- The options now open in their own dock window on the right side of the main window (`PANEL_MODE = "dock"`, `DOCK_AREA`), so the Tasks panel stays free for measuring and other tools while a section is open. `PANEL_MODE = "task"` brings the old Tasks-panel behaviour back.
- Project logo: shown in the README, and a small icon (embedded in the macro, no extra file needed) in the panel header and on the dock window. Sources are in `docs/` (`logo.png`, `icon.png`).
- Changing a color in the panel now refreshes the mesh section immediately.
- `tests/test_mesh_core.py`: unit tests of the numpy core (`python3 tests/test_mesh_core.py`).

## 0.4.0 – 2026-10-10
- The macro no longer needs a selection: with nothing selected it clips everything visible (scenes of up to 12 solids are sectioned completely, bigger ones start with nothing checked; `AUTO_SECTION_MAX_OBJECTS`).
- **Multiple objects / assemblies:** an *Objects* list shows every visible solid. The plane clips everything on screen; the checked objects also get a section fill, outline and hatch, tinted with their own colors. Freeze Section copies all checked objects.
- Options now open in FreeCAD's Tasks panel with a standard Close button (falls back to a window if another task dialog is open; `PANEL_MODE` at the top of the macro).
- Fixed: parts inside assemblies, links and App::Part containers jumped to their local position; the section now uses the shape with all placements applied and hides only the selected instance.
- Gizmo: bigger arrow pick area and the plane itself (surface and outline) can be dragged; `GIZMO_PICK_PLANE` turns the plane grab off.
- Panel is scrollable, shows a "more options below" hint, and has collapsible sections; Freeze Section stays visible at the bottom.

## 0.3.0
- Draggable 3D plane gizmo (Qt event filter on the 3D viewport; hover highlight).
- Mirror Background fixed: it now flips the kept half instead of reflecting the solid, so the object no longer moves.
- Gizmo drawn on top of transparent geometry.
- Closing the panel removes the dock widget completely.

## 0.2.0
- Performance rewrite: GPU clipping, slice-based section outline, single boolean hatch (capped), throttled updates, quick mode while dragging, slice cache, fewer recomputes and property writes.

## 0.1.0
- Initial macro: boolean-cut section with fill, edges and hatch.
