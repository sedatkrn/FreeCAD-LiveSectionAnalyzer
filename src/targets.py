class _SceneObserver:
    """Document observer: flags placement / shape changes of scene objects."""

    PROPS = ("Placement", "Shape", "LinkPlacement", "LinkedObject", "Group")

    def __init__(self, owner):
        self.owner = owner

    def slotDeletedDocument(self, doc):
        try:
            if doc.Name == self.owner._doc_name:
                self.owner.on_document_closed()
        except Exception:
            pass

    def slotChangedObject(self, obj, prop):
        try:
            if prop not in self.PROPS or obj.Name.startswith(("Live_", "Section_")):
                return
            self.owner.on_scene_changed()
        except Exception:
            pass


class _Target:
    """One object of the scene: its global shape and (when sectioned) its copy."""

    def __init__(self, obj, shape, checked=False):
        self.obj = obj
        self.shape = shape
        self.checked = checked
        self.key = (obj.Document.Name, obj.Name)
        self.label = obj.Label
        self.color = LiveSectionAnalyzer._display_color(obj)
        self.bbox = shape.BoundBox
        self.slice_ms = 0.0         # duration of the last exact slice
        self.mesh = None            # (vertices, triangles) numpy arrays, built on demand
        self.meshes = {}            # welded meshes for the mesh section: 0 = coarse, 1 = fine
        self.plane_index = {}       # fine -> (vertices, normal, mesh_plane_index()), see _plane_index
        self.bg_feat = None
        self.bg_mode = None
        self.was_visible = True
        self.saved_look = None      # the object's own look while GPU clipping tints it
        self.src = None             # (top object, subname) when picked from the selection


