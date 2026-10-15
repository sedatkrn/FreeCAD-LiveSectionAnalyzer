<p align="center"><img src="docs/logo.png" alt="FreeCAD Clipping Macro" width="220"></p>

# Addon - Addition: LiveSectionAnalyzer

Description:

LiveSectionAnalyzer is a FreeCAD macro for interactive, real-time section views. A clipping plane (XY/XZ/YZ) is moved with a slider, a spin box or a 3D gizmo; the section is shown with fill, outline and hatch, and updates while you drag.

Works on multiple objects and assemblies (App::Part containers and links keep their placement)
Fast GPU clipping (Coin3D SoClipPlane) plus a mesh-based section engine (outline/hatch/fill from the tessellation), ~10–30 ms per update on a 17-part assembly
Per-object hatch direction, adjustable colors, line widths, hatch spacing
Own dock window, so the Tasks panel stays free for the Measure tool
Freeze Section stores the view as real, independent FreeCAD objects

Repository: https://github.com/sedatkrn/FreeCAD-LiveSectionAnalyzer
Latest release: v0.6.0
License: MIT
Tested with FreeCAD 1.1.x and 27.1 dev on Linux; announced and tested by users on the FreeCAD forum.
The repository has a package.xml, a LICENSE file, README (EN/TR), CHANGELOG and unit tests for the numeric core.
I will maintain the addon.

[Türkçe](README.tr.md)

![Section plane with gizmo](screenshots/section-yz-isometric.png)

## Features

- **Live section** on the XY, XZ or YZ plane, moved with a slider, a spin box or **by dragging a gizmo** in the 3D view.
- **Multiple objects and assemblies** – pick the parts to section from a list; the plane clips everything you see, so you can check clearances between parts. Parts inside App::Part containers and links keep their assembly position.
- **Options in their own dock window** – scrollable, collapsible sections, drag it anywhere or float it; the Tasks panel stays free for measuring (`PANEL_MODE = "task"` puts the options back into the Tasks panel).
- **Fast GPU clipping** (Coin3D `SoClipPlane`) – the model is not re-cut with a boolean on every move, so even complex parts stay responsive.
- **Tilted section plane** – tilt the plane with the two rotation handles on the gizmo (red / blue balls) or the angle boxes; works with hatch, fill, mesh/exact engines and *Freeze Section*.
- **Mesh-based section** (on by default) – outline, hatch and fill are built from the tessellation instead of OpenCascade, so dragging the plane through large assemblies and curved parts stays fluid (10–30 ms per update on a 17-part pump assembly). Each part gets its own hatch direction. *Freeze Section* always uses exact geometry; untick the option to use the exact engine live.
- **Section outline, fill and hatch** generated from the real cross-section (`Shape.slice`), with adjustable colors, line width, hatch spacing and point size.
- **Mirror Background** – keep the opposite half of the part. Only the clipping plane changes; the object never moves.
- **Freeze Section** – store the current view as normal, independent FreeCAD objects in a group.
- Background transparency, optional boolean-cut mode (when GPU clipping is off).

![Dragging the gizmo](screenshots/gizmo-drag.gif)

![Two objects sectioned](screenshots/multi-object.png)

### Mirror Background

![Mirror Background off / on](screenshots/mirror-comparison.png)

### Top view (XY)

![XY section](screenshots/section-xy-top.png)

## Installation

1. Download [`macro/LiveSectionAnalyzer.FCMacro`](macro/LiveSectionAnalyzer.FCMacro).
2. Copy it into your FreeCAD macro folder (**Macro → Macros…** shows the path; on Linux typically `~/.local/share/FreeCAD/v1-1/Macro/` (FreeCAD 1.1)).
3. Run it from **Macro → Macros…**.
4. Optional: press **Add to toolbar** in the panel header. A *Live Section* toolbar with the macro's button appears in every workbench, with the shortcut **Shift+S** if no other command uses it. The same button removes it again.

## Usage

1. Optionally select one or more solids (Part, Body, links, …) in the tree. Without a selection everything visible is clipped.
2. Run the macro. The options open in a dock window on the right.
3. Choose the plane (XY / XZ / YZ) and move it:
   - drag the orange arrow in the 3D view, or
   - use the slider / spin box.
4. Tune appearance in the panel (fill, edges, hatch, transparency).
5. Press **Freeze Section** to keep the result, **Close** to remove all temporary objects.

## Panel options

| Option | Meaning |
|---|---|
| Objects | Checked objects get the section fill/outline/hatch; everything visible is clipped. *Refresh* re-reads the visible solids |
| Show plane gizmo | Show/hide the draggable plane and arrow in the 3D view |
| Offset | Plane position; range follows the object size |
| Transparency | Transparency of the clipped background body |
| Fast GPU clipping | Clip on the GPU instead of a boolean cut |
| Mirror Background | Keep the opposite half of the part |
| Section Fill / Edge Lines / Hatch | Cross-section styling |
| Freeze Section | Copy the current section into a group of static objects |

## Tested with

- FreeCAD 1.1.4 and a 27.1 development build, Linux (Manjaro).
- Windows and macOS are untested; reports welcome.

## Limitations

- Objects that are not checked are clipped but get no section face (they look hollow).
- The section is a snapshot: if you move parts afterwards (e.g. an assembly solver), close and reopen the analysis.
- GPU clipping changes only the display; the real boolean cut is used for *Freeze* and when GPU clipping is off.
- Hatch lines are capped (`MAX_HATCH_LINES = 1500`) to keep very large sections fast; increase spacing instead of the cap if you see gaps.
- If the wrong half is clipped on your setup, flip `CLIP_KEEP_NEGATIVE` at the top of the macro.

## Documentation

- [Roadmap (v0.5 experiments)](docs/ROADMAP.md)
- [Architecture notes](docs/ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT – see [LICENSE](LICENSE). © 2026 Sedat Kuran
