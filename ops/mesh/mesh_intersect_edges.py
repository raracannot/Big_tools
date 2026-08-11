# 交点打断 — 非模态版本

import bpy
import bmesh
from mathutils import Vector
from mathutils.geometry import intersect_line_line


def _find_self_intersections(bm, selected_edges, threshold):
    intersection_points = []

    edge_list = list(selected_edges)
    count = len(edge_list)

    for i in range(count):
        e1 = edge_list[i]
        p1 = e1.verts[0].co.copy()
        p2 = e1.verts[1].co.copy()

        for j in range(i + 1, count):
            e2 = edge_list[j]

            if e1.verts[0] in e2.verts or e1.verts[1] in e2.verts:
                continue

            q1 = e2.verts[0].co.copy()
            q2 = e2.verts[1].co.copy()

            isect = intersect_line_line(p1, p2, q1, q2)

            if isect is None:
                continue

            pt1, pt2 = isect
            if pt1 is None or pt2 is None:
                continue

            if (pt1 - pt2).length > threshold:
                continue

            intersection_points.append((pt1 + pt2) / 2.0)

    return intersection_points


class RARA_OT_Model_IntersectEdges(bpy.types.Operator):
    bl_idname = "rara.model_intersect_edges"
    bl_label = "交点打断"
    bl_description = "检测选中边线的交点，在交点处打断边线"
    bl_options = {'REGISTER', 'UNDO'}

    threshold: bpy.props.FloatProperty(
        name="检测阈值",
        default=1e-4,
        min=0.0,
        precision=5,
        description="两条线段交点距离小于此阈值时视为相交"
    )

    auto_weld: bpy.props.BoolProperty(
        name="自动焊接",
        default=True,
        description="在交点处自动合并重合顶点"
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "threshold")
        layout.prop(self, "auto_weld")

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        selected_edges = [e for e in bm.edges if e.select and not e.hide]
        if len(selected_edges) < 2:
            self.report({'WARNING'}, "请至少选择两条可见边线")
            return {'CANCELLED'}

        intersection_points = _find_self_intersections(bm, selected_edges, self.threshold)

        if not intersection_points:
            self.report({'INFO'}, "未检测到交点")
            return {'FINISHED'}

        new_verts = [bm.verts.new(pt) for pt in intersection_points]
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        for e in selected_edges:
            if not e.is_valid:
                continue

            v1 = e.verts[0]
            v2 = e.verts[1]
            edge_len = (v2.co - v1.co).length

            edge_pts = []
            for bv in new_verts:
                if not bv.is_valid:
                    continue
                d1 = (bv.co - v1.co).length
                d2 = (bv.co - v2.co).length
                if d1 + d2 < edge_len + self.threshold:
                    edge_pts.append((d1, bv))

            if not edge_pts:
                continue

            edge_pts.sort(key=lambda x: x[0])

            current_start = v1
            remaining_end = v2

            for dist, bv in edge_pts:
                if not current_start.is_valid or not remaining_end.is_valid or not bv.is_valid:
                    continue
                bm.edges.ensure_lookup_table()
                old_edge = bm.edges.get((current_start, remaining_end))
                if old_edge is None:
                    old_edge = bm.edges.get((remaining_end, current_start))
                if old_edge and old_edge.is_valid:
                    bm.edges.remove(old_edge)
                bm.edges.new([current_start, bv])
                bm.edges.new([bv, remaining_end])
                if self.auto_weld:
                    bmesh.ops.remove_doubles(bm, verts=[bv], dist=self.threshold * 10)
                current_start = bv

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"打断完成: {len(intersection_points)} 个交点")
        return {'FINISHED'}


classes = (RARA_OT_Model_IntersectEdges,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
