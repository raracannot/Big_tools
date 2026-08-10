# 交点打断

import math
import bpy
import bmesh
import numpy as np
from mathutils import Vector, Matrix
from gpu_extras.batch import batch_for_shader
from ...utils.gpu_utils import draw_circle_2d, draw_lines_2d, draw_hud_text


INTERSECT_COLOR = (1.0, 0.2, 1.0, 0.8)
INTERSECT_RADIUS = 10
CIRCLE_SEGMENTS_64 = 64


def _draw_intersect_point_2d(pos, color=None):
    if not pos:
        return
    draw_color = color or INTERSECT_COLOR
    draw_circle_2d(pos, INTERSECT_RADIUS, draw_color, filled=False)
    draw_circle_2d(pos, 3, draw_color, filled=True)


class IntersectPreviewDrawer:
    def __init__(self):
        self.points_3d = []
        self.color = INTERSECT_COLOR

    def draw(self, context):
        for pt in self.points_3d:
            co2d = bpy_extras.view3d_utils.location_3d_to_region_2d(
                context.region, context.region_data, pt)
            if co2d:
                _draw_intersect_point_2d(co2d, self.color)


def _get_edge_coords_np(obj):
    if obj.type != 'MESH' or obj.mode != 'EDIT':
        return None
    bm = bmesh.from_edit_mesh(obj.data)
    selected_edges = [e for e in bm.edges if e.select]
    if not selected_edges:
        return None
    verts = np.array([v.co for v in bm.verts], dtype=np.float64)
    edge_verts = np.array([[e.verts[0].index, e.verts[1].index] for e in selected_edges], dtype=np.int32)
    world_mat = np.array(obj.matrix_world, dtype=np.float64)
    return verts, edge_verts, world_mat


def _find_intersections(verts_a, edges_a, mat_a, verts_b, edges_b, mat_b, threshold=1e-6):
    pts = []
    for e1 in edges_a:
        p1 = mat_a[:3, :3] @ verts_a[e1[0]] + mat_a[:3, 3]
        p2 = mat_a[:3, :3] @ verts_a[e1[1]] + mat_a[:3, 3]
        for e2 in edges_b:
            q1 = mat_b[:3, :3] @ verts_b[e2[0]] + mat_b[:3, 3]
            q2 = mat_b[:3, :3] @ verts_b[e2[1]] + mat_b[:3, 3]
            d1 = p2 - p1
            d2 = q2 - q1
            cross = np.cross(d1, d2)
            cross_len = np.linalg.norm(cross)
            if cross_len < 1e-12:
                continue
            n1 = np.cross(d2, cross)
            n1 = n1 / np.linalg.norm(n1)
            n2 = np.cross(d1, cross)
            n2 = n2 / np.linalg.norm(n2)
            t = np.dot(n2, q1 - p1) / np.dot(n2, d1)
            u = np.dot(n1, p1 - q1) / np.dot(n1, d2)
            if 0 <= t <= 1 and 0 <= u <= 1:
                pt = p1 + d1 * t
                pts.append(Vector(pt))
    return pts


class RARA_OT_Model_IntersectEdges(bpy.types.Operator):
    bl_idname = "rara.model_intersect_edges"
    bl_label = "交点打断 (交互预览)"
    bl_description = "使用Numpy加速检测选中边线的交点，支持实时预览和阈值调整\n【W】切换焊接模式\n【Ctrl+滚轮】粗调检测阈值\n【Shift+滚轮】细调检测阈值\n【回车/左键】确认执行打断\n【ESC/右键】取消退出"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    auto_weld: bpy.props.BoolProperty(default=True)
    threshold: bpy.props.FloatProperty(default=1e-4, min=0.0, precision=5)

    def invoke(self, context, event):
        self.obj = context.active_object
        if not self.obj or self.obj.type != 'MESH' or self.obj.mode != 'EDIT':
            self.report({'WARNING'}, "请选择一个编辑模式下的网格对象")
            return {'CANCELLED'}

        self.draw_handle = None
        self.preview_drawer = IntersectPreviewDrawer()
        self.intersect_points = []
        self._hud_text = ""

        args = (self, context)
        self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_callback, args, 'WINDOW', 'POST_PIXEL')
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)

        context.window_manager.modal_handler_add(self)
        self._update_preview(context)
        self._update_hud()
        return {'RUNNING_MODAL'}

    def _update_hud(self):
        pts = len(self.intersect_points)
        weld = "焊接" if self.auto_weld else "不焊接"
        self._hud_text = f"交点打断 | 交点: {pts} | W: 焊接模式({weld}) | 阈值: {self.threshold:.5f} | 回车: 确认 | ESC: 取消"

    def _update_preview(self, context):
        self.intersect_points.clear()
        data = _get_edge_coords_np(self.obj)
        if data is None:
            return
        verts, edges, mat = data
        found = _find_intersections(verts, edges, mat, verts, edges, mat, self.threshold)
        if found:
            self.intersect_points.extend(found)
        self.preview_drawer.points_3d = self.intersect_points

    def _draw_callback(self, op, context):
        if context.region != getattr(self, 'init_region', None):
            return
        self.preview_drawer.draw(context)
        self._draw_hud()

    def _draw_hud(self):
        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, bpy.context)

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self.finish(context)
                return {'CANCELLED'}

            if event.type == 'W' and event.value == 'PRESS':
                self.auto_weld = not self.auto_weld
                self.preview_drawer.color = (0.2, 0.8, 1.0, 0.8) if self.auto_weld else (1.0, 0.2, 0.2, 0.8)
                self._update_hud()
                return {'RUNNING_MODAL'}

            if event.type in {'RET', 'LEFTMOUSE'} and event.value == 'PRESS':
                self._execute(context)
                self.finish(context)
                return {'FINISHED'}

            if event.type == 'WHEELUPMOUSE':
                if event.ctrl:
                    self.threshold *= 10
                elif event.shift:
                    self.threshold *= 1.1
                else:
                    return {'PASS_THROUGH'}
                self.threshold = max(1e-8, min(1.0, self.threshold))
                self._update_preview(context)
                self._update_hud()
                return {'RUNNING_MODAL'}

            if event.type == 'WHEELDOWNMOUSE':
                if event.ctrl:
                    self.threshold /= 10
                elif event.shift:
                    self.threshold /= 1.1
                else:
                    return {'PASS_THROUGH'}
                self.threshold = max(1e-8, min(1.0, self.threshold))
                self._update_preview(context)
                self._update_hud()
                return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"交点打断出错: {str(e)}")
            return {'CANCELLED'}

    def _execute(self, context):
        bm = bmesh.from_edit_mesh(self.obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        world_inv = self.obj.matrix_world.inverted()
        local_pts = [world_inv @ pt for pt in self.intersect_points]

        bm_points = []
        for pt in local_pts:
            bm_points.append(bm.verts.new(pt))

        data = _get_edge_coords_np(self.obj)
        if data is None:
            return
        verts, edges, mat = data

        for e_idx, e in enumerate(edges):
            v1 = bm.verts[e[0]]
            v2 = bm.verts[e[1]]
            edge_len = (v2.co - v1.co).length

            edge_pts = []
            for bv in bm_points:
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
                bm.edges.ensure_lookup_table()
                old_edge = bm.edges.get((current_start, remaining_end)) or bm.edges.get((remaining_end, current_start))
                if old_edge:
                    bm.edges.remove(old_edge)
                bm.edges.new([current_start, bv])
                bm.edges.new([bv, remaining_end])
                if self.auto_weld:
                    bmesh.ops.remove_doubles(bm, verts=[bv, current_start, remaining_end], dist=self.threshold * 10)
                current_start = bv

        bmesh.update_edit_mesh(self.obj.data)
        self.report({'INFO'}, f"打断完成: {len(local_pts)} 个交点")

    def finish(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self.draw_handle:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
            self.draw_handle = None
        context.area.tag_redraw()


classes = (RARA_OT_Model_IntersectEdges,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)