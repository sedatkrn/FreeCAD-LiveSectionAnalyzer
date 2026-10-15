# ----------------------------------------------------------------------
# Toolbar button: registers the macro as a command on a global toolbar
# (shown in every workbench), the same as Tools -> Customize -> Macros.
# ----------------------------------------------------------------------
TOOLBAR_NAME = "Live Section"
TOOLBAR_SHORTCUT = "Shift+S"      # only assigned when no other command uses it
_TOOLBAR_PARAM = "User parameter:BaseApp/Workbench/Global/Toolbar"
_MACRO_FILE = globals().get("__file__")


def _macro_path():
    """Absolute path of this macro file, or None if it cannot be found."""
    import os
    if _MACRO_FILE and os.path.isfile(_MACRO_FILE):
        return os.path.abspath(_MACRO_FILE)
    guess = os.path.join(App.getUserMacroDir(True), "LiveSectionAnalyzer.FCMacro")
    return guess if os.path.isfile(guess) else None


def _macro_script_name(path):
    """Commands refer to macros in the user macro folder by file name only."""
    import os
    if os.path.normpath(os.path.dirname(path)) == os.path.normpath(App.getUserMacroDir(True)):
        return os.path.basename(path)
    return path


def _toolbar_group():
    root = App.ParamGet(_TOOLBAR_PARAM)
    for g in root.GetGroups():
        if App.ParamGet(f"{_TOOLBAR_PARAM}/{g}").GetString("Name") == TOOLBAR_NAME:
            return g
    return None


def toolbar_installed():
    return _toolbar_group() is not None


def _reload_toolbars():
    try:
        Gui.activeWorkbench().reloadActive()
    except Exception:
        pass


def install_toolbar():
    """Create the command and the toolbar. Returns (command name, shortcut or "")."""
    import base64
    import os
    path = _macro_path()
    if path is None:
        raise RuntimeError("the macro file was not found; run it from Macro -> Macros...")
    script = _macro_script_name(path)
    icon = os.path.join(os.path.dirname(path), "LiveSectionAnalyzer.png")
    if not os.path.isfile(icon):
        with open(icon, "wb") as fh:
            fh.write(base64.b64decode(_LOGO_PNG_B64))
    shortcut = "" if Gui.Command.listByShortcut(TOOLBAR_SHORTCUT) else TOOLBAR_SHORTCUT
    cmd = Gui.Command.findCustomCommand(script)
    if cmd is None:
        cmd = Gui.Command.createCustomCommand(
            script, "Live Section Analyzer",
            "Interactive section view (Live Section Analyzer)",
            "Opens the Live Section Analyzer panel",
            "Interactive section view", icon, shortcut)
    else:
        shortcut = Gui.Command.get(cmd).getShortcut()
    if _toolbar_group() is None:
        root = App.ParamGet(_TOOLBAR_PARAM)
        n = 1
        while f"Custom_{n}" in root.GetGroups():
            n += 1
        grp = App.ParamGet(f"{_TOOLBAR_PARAM}/Custom_{n}")
        grp.SetString("Name", TOOLBAR_NAME)
        grp.SetBool("Active", True)
        grp.SetString(cmd, "FreeCAD")
    _reload_toolbars()
    return cmd, shortcut


def uninstall_toolbar():
    """Remove the toolbar and the command created by install_toolbar()."""
    g = _toolbar_group()
    if g is not None:
        App.ParamGet(_TOOLBAR_PARAM).RemGroup(g)
    path = _macro_path()
    if path is not None:
        cmd = Gui.Command.findCustomCommand(_macro_script_name(path))
        if cmd is not None:
            Gui.Command.removeCustomCommand(cmd)
    _reload_toolbars()


