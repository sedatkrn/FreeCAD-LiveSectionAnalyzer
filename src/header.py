"""LiveSectionAnalyzer.FCMacro

Interactive, real-time section analysis for FreeCAD.
Select a solid, run the macro, and move the section plane with a slider,
a spin box or by dragging a gizmo directly in the 3D view.

Version: 0.8.0   (keep in sync with VERSION below)
Author: Sedat Kuran
License: MIT (see LICENSE)
Repository: https://github.com/sedatkrn/FreeCAD-LiveSectionAnalyzer
Copyright (c) 2026 Sedat Kuran

This file is generated from src/ by tools/build_macro.py; edit the files there.
"""

import FreeCAD as App
import FreeCADGui as Gui
import Part
import math
import time

from PySide.QtWidgets import (
    QColorDialog, QDockWidget, QDoubleSpinBox, QMessageBox, QLabel, QCheckBox,
    QComboBox, QPushButton, QSlider, QSpinBox, QWidget,
    QHBoxLayout, QVBoxLayout, QScrollArea, QToolButton, QFrame,
    QDialogButtonBox, QSizePolicy, QListWidget, QListWidgetItem, QApplication, QProgressDialog,
)

from PySide.QtGui import QIcon, QPixmap, QColor

from PySide.QtCore import Qt, QTimer, QObject, QEvent

try:
    import numpy as np
    HAS_NP = True
except Exception:
    np = None
    HAS_NP = False

try:
    from pivy import coin
    HAS_COIN = True
except Exception:
    coin = None
    HAS_COIN = False

# Where the options panel lives:
#   "dock" -> its own dockable / floating window (the Tasks panel stays free, so measuring
#             and other task dialogs work while the section is open)
#   "task" -> FreeCAD's Tasks panel (Combo View)
# "task" falls back to "dock" automatically when another task dialog is open.
PANEL_MODE = "dock"

# Side of the main window the dock opens on ("left" or "right"); drag it anywhere afterwards,
# or float it by double-clicking its title.
DOCK_AREA = "right"

# Width the dock opens with, in pixels; it can be dragged wider afterwards.
PANEL_WIDTH = 315

# If the wrong half of the object is clipped away, flip this value.
CLIP_KEEP_NEGATIVE = True

# Safety limit for the number of hatch lines generated per update.
MAX_HATCH_LINES = 1500

# Let the gizmo be grabbed on the plane itself (surface and outline), not only
# on the arrow. Off by default: the plane covers the model on screen, and while
# it grabs clicks you cannot select faces under it (e.g. for Assembly joints).
GIZMO_PICK_PLANE = False

# The gizmo arrow and rotation handles keep this size on screen (half the arrow
# length, in pixels) while you zoom; the plane outline still follows the model.
# 0 = everything scales with the model, as before 0.7.
GIZMO_SCREEN_PX = 110

# Follow parts that move (Assembly solver, dragging, placement edits). After the
# last change the fine section is rebuilt once the scene has been still this long.
FOLLOW_SCENE = True
SCENE_SETTLE_MS = 300

# v0.5 (experimental): print per-update timings (slice / faces / fill / edges /
# hatch / total, objects skipped by the bounding-box test) to the Report view.
DEBUG_TIMING = True

# v0.5 (experimental): while the plane is being dragged, objects whose exact slice
# took longer than this many ms are shown as a fast outline computed from their
# tessellation (numpy). The exact section is computed when the drag ends.
FAST_PREVIEW_MS = 60

# Max cached slices (all objects together).
SLICE_CACHE_SIZE = 256

# With nothing selected, scenes up to this many visible solids are sectioned
# completely; bigger scenes start with nothing checked.
AUTO_SECTION_MAX_OBJECTS = 12

# The section fill / edges / hatch lie exactly in the clip plane. They are
# nudged by this fraction of the model size so the clip does not cut them.
OVERLAY_OFFSET_REL = 1e-4

# v0.5 (experimental): build contour, hatch and fill from the tessellation instead of
# OpenCascade. This is only the start state of the "Mesh-based section" checkbox in the
# panel; the exact path stays available for comparison and is always used by Freeze.
# Curves look polygonal at a coarse tolerance.
MESH_SECTION = True

# Tessellation tolerance as a fraction of the object's bounding-box diagonal: coarse while
# the plane is being dragged, fine once it rests.
MESH_TOL_REL = 1e-3
MESH_TOL_FINE_REL = 1e-4

# Draw hatch and fill while dragging too (mesh section only). Turn off if a huge
# assembly stutters.
MESH_DRAG_HATCH = True

# Hatch style per sectioned object (exact and mesh section alike): (angle in degrees,
# spacing factor). Neighbouring parts get different entries, so touching sections can be
# told apart. One entry, e.g. ((45.0, 1.0),), hatches everything the same way.
HATCH_STYLES = ((45.0, 1.0), (135.0, 1.0), (0.0, 1.5), (90.0, 1.5))

# Max cached mesh sections (all objects together).
MESH_CACHE_SIZE = 128
# Safety limits (the first tick of "Mesh-based section" tessellates every checked object)
MESH_FINE_MAX_TRIS = 8000       # fine mesh only when the coarse one has at most this many triangles
MESH_TOL_MIN = 0.01             # mm, tessellation tolerance never goes below this (threads!)
MESH_FINE_PREDICT = 25.0        # fine mesh costs ~this times the coarse one; skipped if it would exceed the budget
MESH_MAX_FILL_POINTS = 40000    # no fill polygons for sections with more loop points than this
MESH_FINE_BUDGET_S = 1.5        # per update: no further fine tessellations after this much time


