# Contributing

Issues and pull requests are welcome.

- Report the FreeCAD version, OS and a short description (or a sample part) with every bug.
- The macro users download stays a single self-contained `.FCMacro` file with no extra dependencies beyond FreeCAD, PySide and pivy. It is **generated**: edit the parts in `src/` and run `python3 tools/build_macro.py`. Never edit `macro/LiveSectionAnalyzer.FCMacro` by hand (CI checks that it matches `src/`).
- Test with a simple part and a complex one; check dragging the gizmo, Mirror Background on/off, and Freeze Section.
- Run `python3 tools/build_macro.py`, `python -m py_compile macro/LiveSectionAnalyzer.FCMacro` and `python3 tests/test_mesh_core.py` (plus `python3 tests/test_logo.py`, needs Pillow) before submitting.
- Update `CHANGELOG.md` for user-visible changes.

By contributing you agree your work is released under the MIT license.
