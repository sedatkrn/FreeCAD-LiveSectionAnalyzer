class _FreezeMixin:
    """Freeze Section: copies the current section into static objects."""

    # ------------------------------------------------------------------
    # Freeze
    # ------------------------------------------------------------------
    @staticmethod
    def _has_shape(feat):
        return feat is not None and feat.Shape is not None and not feat.Shape.isNull()

    def freeze_section(self):
        """Freeze current section view using SimpleCopy with folder"""
        doc = App.ActiveDocument
        if not doc or not any(t.checked for t in self.targets):
            return

        progress = None
        try:
            progress = QProgressDialog("Freezing section ...", None, 0, 0, Gui.getMainWindow())
            progress.setWindowTitle("Live Section Analyzer")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setCancelButton(None)
            progress.show()
            QApplication.processEvents()
        except Exception:
            progress = None
        try:
            # Make sure everything is up to date (including hatch)
            self._timer.stop()
            self.update_section(force_exact=True)      # real geometry, even in mesh mode

            plane_type, normal, plane_base, center_pos = self.get_plane()
            offset_val = self.offset_spin.value()

            tilt = self._tilt_text()
            group_name = f"Frozen_Section_{plane_type}{tilt}_Offset_{offset_val:.1f}"

            # Delete old frozen group
            if self.frozen_group and doc.getObject(self.frozen_group.Name):
                for obj in list(self.frozen_group.Group):
                    try:
                        doc.removeObject(obj.Name)
                    except Exception:
                        pass
                try:
                    doc.removeObject(self.frozen_group.Name)
                except Exception:
                    pass

            self.frozen_group = doc.addObject("App::DocumentObjectGroup", group_name)
            self.frozen_group.Label = f"Frozen Section ({plane_type}{tilt.replace('_', ' ')}, Offset: {offset_val:.1f} mm)"

            copied_objects = []

            # 1. Background of every sectioned object: always a real boolean cut
            #    here, because the live GPU clip does not change the geometry.
            for t in self.targets:
                if not t.checked:
                    continue
                bg_shape = self.compute_cut_solid(
                    t.shape, plane_type, normal, plane_base, self.chk_mirror_bg.isChecked()
                )
                if bg_shape is not None and not bg_shape.isNull():
                    bg_copy = doc.addObject("Part::Feature", f"Section_Background_{plane_type}")
                    bg_copy.Label = f"{t.label} ({plane_type})"
                    bg_copy.Shape = bg_shape
                    bg_copy.ViewObject.ShapeColor = t.color
                    bg_copy.ViewObject.DisplayMode = "Shaded"
                    bg_copy.ViewObject.Transparency = self.spin_trans.value()
                    copied_objects.append(bg_copy)

            # 2. Section fill
            if self.section_feat.ViewObject.Visibility and self._has_shape(self.section_feat):
                fill_copy = doc.addObject("Part::Feature", f"Section_Fill_{plane_type}")
                fill_copy.Shape = self.section_feat.Shape
                fill_copy.Placement = self.section_feat.Placement
                fill_copy.ViewObject.ShapeColor = self.section_feat.ViewObject.ShapeColor
                fill_copy.ViewObject.DiffuseColor = self.section_feat.ViewObject.DiffuseColor
                fill_copy.ViewObject.LineColor = self.section_feat.ViewObject.LineColor
                fill_copy.ViewObject.LineWidth = self.section_feat.ViewObject.LineWidth
                fill_copy.ViewObject.DisplayMode = "Shaded"
                fill_copy.ViewObject.Transparency = 0
                copied_objects.append(fill_copy)

            # 3. Edge lines
            if self.edge_feat.ViewObject.Visibility and self._has_shape(self.edge_feat):
                edge_copy = doc.addObject("Part::Feature", f"Section_Edges_{plane_type}")
                edge_copy.Shape = self.edge_feat.Shape
                edge_copy.Placement = self.edge_feat.Placement
                edge_copy.ViewObject.LineColor = self.edge_feat.ViewObject.LineColor
                edge_copy.ViewObject.LineWidth = self.edge_feat.ViewObject.LineWidth
                edge_copy.ViewObject.DisplayMode = "Wireframe"
                edge_copy.ViewObject.PointSize = 1
                copied_objects.append(edge_copy)

            # 4. Hatch lines
            if self.hatch_feat.ViewObject.Visibility and self._has_shape(self.hatch_feat):
                hatch_copy = doc.addObject("Part::Feature", f"Section_Hatch_{plane_type}")
                hatch_copy.Shape = self.hatch_feat.Shape
                hatch_copy.Placement = self.hatch_feat.Placement
                hatch_copy.ViewObject.LineColor = self.hatch_feat.ViewObject.LineColor
                hatch_copy.ViewObject.LineWidth = self.hatch_feat.ViewObject.LineWidth
                hatch_copy.ViewObject.DisplayMode = "Wireframe"
                hatch_copy.ViewObject.PointSize = self.hatch_point_spin.value()
                copied_objects.append(hatch_copy)

            if not copied_objects:
                if progress is not None:
                    progress.close()
                QMessageBox.warning(None, "Warning", "No section elements found to copy!")
                try:
                    doc.removeObject(self.frozen_group.Name)
                except Exception:
                    pass
                self.frozen_group = None
                return

            for obj in copied_objects:
                self.frozen_group.addObject(obj)

            self.frozen_group.ViewObject.Visibility = True

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(self.frozen_group)

            for obj in copied_objects:
                obj.purgeTouched()
            self.frozen_group.purgeTouched()

            if progress is not None:
                progress.close()
            QMessageBox.information(
                None,
                "Success",
                f"Section successfully frozen!\n"
                f"Group name: {self.frozen_group.Label}\n"
                f"Element count: {len(copied_objects)}\n"
                f"Plane: {plane_type}, Offset: {offset_val:.1f} mm\n\n"
                f"📁 Contents:\n" +
                "\n".join([f"  • {obj.Label}" for obj in copied_objects])
            )

        except Exception as e:
            App.Console.PrintError(f"Freeze Error: {str(e)}\n")
            QMessageBox.critical(None, "Error", f"Error while freezing section:\n{str(e)}")
        finally:
            if progress is not None:
                progress.close()
            self.schedule_update()                     # back to the live (mesh or exact) view



