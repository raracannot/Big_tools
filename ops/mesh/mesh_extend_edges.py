# 延申线段

import bpy
import bmesh
import mathutils


class RARA_OT_ModelExtendEdges(bpy.types.Operator):
    bl_idname = "rara.model_extend_edges"
    bl_label = "延申线段"
    bl_description = "检测并延申选中几何体中所有边的交点"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def _is_point_on_segment(self, point, v1, v2):
        return (point - v1).length + (point - v2).length - (v1 - v2).length < 1e-6

    def _find_intersections(self, edges):
        intersections = {}
        for i, edge1 in enumerate(edges):
            for j, edge2 in enumerate(edges):
                if i != j:
                    v1, v2 = edge1.verts
                    v3, v4 = edge2.verts
                    line1 = (v1.co, v2.co)
                    line2 = (v3.co, v4.co)
                    intersect = mathutils.geometry.intersect_line_line(line1[0], line1[1], line2[0], line2[1])
                    if intersect:
                        intersection_point = (intersect[0] + intersect[1]) / 2
                        if (not self._is_point_on_segment(intersection_point, v1.co, v2.co) and
                            not self._is_point_on_segment(intersection_point, v3.co, v4.co) and
                            intersection_point not in [v1.co, v2.co, v3.co, v4.co]):
                            if edge1 not in intersections:
                                intersections[edge1] = []
                            if edge2 not in intersections:
                                intersections[edge2] = []
                            intersections[edge1].append(intersection_point)
                            intersections[edge2].append(intersection_point)
        return intersections

    def _extend_edges_at_intersections(self, bm, intersections):
        for edge, points in intersections.items():
            v1, v2 = edge.verts
            for point in points:
                closest_v = min(v1, v2, key=lambda v: (v.co - point).length)
                closest_v.co = point

    def execute(self, context):
        obj = context.active_object
        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            selected_edges = [e for e in bm.edges if e.select]
            intersections = self._find_intersections(selected_edges)
            self._extend_edges_at_intersections(bm, intersections)
            bmesh.update_edit_mesh(obj.data)
            self.report({'INFO'}, f"延申了 {len(intersections)} 条边上的交点")
        else:
            self.report({'WARNING'}, "请选择一个编辑模式下的网格对象")
        return {'FINISHED'}


classes = (RARA_OT_ModelExtendEdges,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)