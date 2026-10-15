# Architecture

Users get one file, `macro/LiveSectionAnalyzer.FCMacro`. It is built from the parts in `src/` by
`tools/build_macro.py`, which pastes them one after another (one shared namespace, order set in
`PARTS`): `header.py` (imports, settings), `mesh_core.py`, `branding.py`, `gizmo.py`, `widgets.py`,
`targets.py`, then the mixins of the main class, then `analyzer.py` and `run.py`. Edit `src/`, then
rebuild; `--check` fails when the macro is stale.

`LiveSectionAnalyzer` is assembled from mixins, one per file:

| File | Class | Contents |
|---|---|---|
| `scene.py` | `_SceneMixin` | collecting the objects, global shapes, following moved parts |
| `panel.py` | `_PanelMixin` | options panel, event handlers, gizmo callbacks, styles |
| `geometry.py` | `_GeometryMixin` | plane, GPU clip, boolean cut, exact slice and hatch |
| `mesh_section.py` | `_MeshSectionMixin` | mesh-based section as Coin nodes |
| `freeze.py` | `_FreezeMixin` | Freeze Section |
| `analyzer.py` | `LiveSectionAnalyzer` | `__init__`, document features, `update_section()`, cleanup |

## Classes

- **`LiveSectionAnalyzer(QDockWidget)`** – the update logic and the panel contents. Creates temporary document features, wires the UI and owns the gizmo. The options widget is shown in this dock window (default, `PANEL_MODE = "dock"`, side set by `DOCK_AREA`) or in the Tasks panel (`PANEL_MODE = "task"`, `SectionTaskPanel`), which also falls back to the dock when another task dialog is open.
- **`SectionTaskPanel`** / **`_Collapsible`** – adapter for FreeCAD's task dialog protocol (standard Close button) and a small foldable section widget.
- **`_Target`** – one listed object: the object, its shape in global coordinates, its color and its background copy.
- **`PlaneGizmo`** – Coin3D scene nodes (plane outline + arrow) with hit-testing in screen space and a drag computed from the closest point between the mouse ray and the plane-normal axis. Rendered under `SoFCUnifiedSelection` (on some builds nodes appended to the returned scene graph are not drawn) with a `SoAnnotation` root so it shows on top of transparent geometry.
- **`_GizmoEventFilter`** – Qt event filter on the 3D view viewport. Mouse events over the gizmo are consumed, so dragging does not rotate the camera or change selection.

## Objects

The panel lists every visible solid of the document as a `_Target` (object + shape in global coordinates, resolved with `Part.getShape` so parent/link placements are applied). Checked targets get a background copy, a slice, a fill, outline and hatch. The GPU clip plane sits at the root of the 3D view, so it clips everything; the gizmo is inserted *before* it and the in-plane overlays are nudged by `OVERLAY_OFFSET_REL` so they are not cut.

## Update pipeline

1. A 60 ms throttle timer (not restarted on every event) calls `update_section()`.
2. **Clip**: GPU mode moves the scene-wide `SoClipPlane` (`set_clip`). Backgrounds of checked objects are copies; without GPU clipping they are boolean cuts (`compute_cut_solid`).
3. **Section**: `get_slice()` uses `Shape.slice` per checked object (cached, `SLICE_CACHE_SIZE`), `make_faces()` builds faces (`Part::FaceMakerBullseye`) per object; fill colors follow the object colors.
4. **Hatch**: `build_hatch_per_object()` runs `build_hatch()` (one boolean) for each checked object with its own angle and spacing factor from `HATCH_STYLES`, limited by `MAX_HATCH_LINES` per object.
5. While dragging, a quick mode skips hatch and boolean work; the full result is built on release.
6. Features are marked with `purgeTouched()` instead of a full recompute; view properties are written only when changed.

## Mesh section (v0.5-experimental)

With *Section Plane -> Mesh-based section* on (`MESH_SECTION` sets the start state), step 3 and 4 above are replaced by `_update_mesh_section()`:

1. `_mesh_for()` tessellates the object once per level (coarse / fine, `MESH_TOL_REL`, `MESH_TOL_FINE_REL`) and welds the vertices (`mesh_weld`).
2. `mesh_section_loops()` chains the plane/triangle segments into loops (cached per object, level and plane).
3. `fill_polygons()` (nesting + keyhole bridging) and `hatch_segments()` (even-odd scanlines, per-object angle) work on the loops in plane coordinates.
4. The result goes into Coin nodes (`_create_mesh_nodes()`: fill, outline, hatch) that sit before the clip plane, like the drag preview. The exact features are hidden while this runs.

The numpy functions between `BEGIN MESH CORE` and `END MESH CORE` do not touch FreeCAD; `tests/test_mesh_core.py` cuts that block out of the macro and tests it on its own. `freeze_section()` calls `update_section(force_exact=True)` so frozen copies are real geometry, then returns to the live view.

## Mirror

Mirror flips which side of the plane is kept (`flip` in `set_clip` / `compute_cut_solid`). The shape is never reflected.

## Freeze

`freeze_section()` forces a final update and copies background (always a real boolean cut), section and hatch into a `Frozen_Section_*` group.

## Tunables

`PANEL_MODE`, `DOCK_AREA`, `CLIP_KEEP_NEGATIVE`, `GIZMO_PICK_PLANE`, `AUTO_SECTION_MAX_OBJECTS`, `MAX_HATCH_LINES`, `SLICE_CACHE_SIZE`, `OVERLAY_OFFSET_REL`, `DEBUG_TIMING`, `FAST_PREVIEW_MS`, `MESH_SECTION`, `MESH_TOL_REL`, `MESH_TOL_FINE_REL`, `MESH_DRAG_HATCH`, `HATCH_STYLES`, `MESH_CACHE_SIZE` at the top of the file.
