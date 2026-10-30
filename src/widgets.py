def _hide_in_tree(feat):
    """Keep a helper object out of the tree view; big documents stay readable."""
    try:
        feat.ViewObject.ShowInTree = False
    except Exception:
        pass                  # older FreeCAD: the object just stays listed


class _Collapsible(QWidget):
    """A titled section that can be folded to keep the panel short."""

    def __init__(self, title, expanded=True):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._title = title
        self.toggle = QToolButton()
        self.toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.clicked.connect(self._apply)
        outer.addWidget(self.toggle)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(6, 0, 0, 4)
        outer.addWidget(self.body)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        outer.addWidget(line)
        self._apply()

    def _apply(self, *args):
        on = self.toggle.isChecked()
        self.toggle.setText(("\u25BE " if on else "\u25B8 ") + self._title)
        self.body.setVisible(on)


class SectionTaskPanel:
    """Adapter that shows the options in FreeCAD's Tasks panel."""

    def __init__(self, owner, widget):
        self.owner = owner
        self.form = widget

    def getStandardButtons(self):
        btn = QDialogButtonBox.Close
        try:
            return int(btn)           # PySide2
        except TypeError:
            return btn.value          # PySide6 (enum flag)

    def reject(self):
        self.owner.shutdown(close_dialog=False)
        return True

    accept = reject

    def isAllowedAlterSelection(self):
        return True

    def isAllowedAlterView(self):
        return True

    def isAllowedAlterDocument(self):
        return True


