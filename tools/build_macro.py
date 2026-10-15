#!/usr/bin/env python3
"""Join the files in src/ into the single macro users download.

    python3 tools/build_macro.py           # write macro/LiveSectionAnalyzer.FCMacro
    python3 tools/build_macro.py --check   # fail if the macro is not up to date

Edit the files in src/, never the macro: it is overwritten by this script.
The parts share one namespace (they are pasted one after another), so a part may
use names defined in the parts before it.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
MACRO = os.path.join(ROOT, "macro", "LiveSectionAnalyzer.FCMacro")

PARTS = [
    "header.py",      # docstring, imports, settings
    "mesh_core.py",   # pure numpy section core (tests/test_mesh_core.py)
    "branding.py",    # VERSION and the embedded icon
    "gizmo.py",       # 3D plane gizmo and its event filter
    "widgets.py",     # collapsible section, task panel adapter
    "targets.py",     # scene observer, listed objects
    "analyzer.py",    # LiveSectionAnalyzer: panel and update pipeline
    "run.py",         # starts the macro
]


def build():
    out = []
    for name in PARTS:
        with open(os.path.join(SRC, name), encoding="utf-8", newline="") as fh:
            text = fh.read()
        if not text.endswith("\n"):
            text += "\n"
        out.append(text)
    return "".join(out)


def main():
    text = build()
    if "--check" in sys.argv:
        with open(MACRO, encoding="utf-8", newline="") as fh:
            if fh.read() != text:
                sys.exit("macro/LiveSectionAnalyzer.FCMacro is out of date: run python3 tools/build_macro.py")
        print("macro is up to date")
        return
    with open(MACRO, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print(f"wrote {os.path.relpath(MACRO, ROOT)} ({text.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
