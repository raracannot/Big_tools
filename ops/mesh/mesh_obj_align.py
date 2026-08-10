# 三点对齐

import math
import bpy
import bmesh
import numpy as np
from mathutils import Vector, Matrix
from bpy_extras import view3d_utils
from ...utils.gpu_utils import draw_circle_2d, draw_dashed_line_2d, make_bbox_wireframe, draw_lines_3d
from ...utils.rara_snapper import RaraSnapper
from ...utils.math_utils import ray_cast, compute_3point_transform, is_double_click
from ...utils.draw_callbacks import draw_align



class ThreePointAlignDrawer:
    def __init__(self, group_size=2, color_map=None):
        self.points = []
        self.active_idx = None
        self.group_size = group_size
        self.color_map = color_map or [
            (1.0, 0.5, 1.0, 1.0),
            (1.0, 1.0, 0.5, 1.0),
            (0.5, 1.0, 1.0, 1.0),
        ]

    def draw(self, context):
        region = context.region
        rv3d = context.space_data.region_3d
        if not region or not rv3d:
            return

        points_2d = []
        for pt in self.points:
            co2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pt)
            if co2d is None:
                return
            points_2d.append(co2d)

        num = len(self.points)
        if num == 0:
            return

        for start in range(0, num, self.group_size):
            group_pts = points_2d[start:start + self.group_size]
            if len(group_pts) >= 2:
                col_idx = start // self.group_size
                color = self.color_map[col_idx] if col_idx < len(self.color_map) else (1, 1, 1, 1)
                draw_dashed_line_2d(group_pts, color, dash_length=14, gap_length=10)

        for i, co2d in enumerate(points_2d):
            col_idx = i // self.group_size
            color = self.color_map[col_idx] if col_idx < len(self.color_map) else (1, 1, 1, 1)
            if self.active_idx == i:
                color = (1.0, 1.0, 1.0, 1.0)
            draw_circle_2d(co2d, 6, color, filled=True)
            draw_circle_2d(co2d, 8, color, filled=False)

    def pick_point(self, context, event, radius=12):
        region = context.region
        rv3d = context.space_data.region_3d
        mouse = (event.mouse_region_x, event.mouse_region_y)
        for idx, pt in enumerate(self.points):
            co2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pt)
            if co2d and math.hypot(mouse[0] - co2d[0], mouse[1] - co2d[1]) < radius:
                return idx
        return None


class RARA_OT_Model_Align(bpy.types.Operator):
    bl_idname = "rara.model_three_point_align"
    bl_label = "三点对齐"
    bl_description = "通过三组对应点（6个点）计算空间变换矩阵，实现两套坐标系的精确对齐\n【左键】放置/拖动控制点（每两个点为一组，共三组）\n【左键双击】翻转同组两个点的顺序\n【B】切换自动吸附到顶点功能\n【S】切换缩放模式（位移+旋转+缩放 / 仅位移+旋转）\n【X】清空所有定位点\n【回车/右键】确认执行对齐\n【ESC】取消退出"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH' and context.mode in {'EDIT_MESH', 'OBJECT'}

    _handle = None
    _handle_view = None
    _timer = None
    _drag_idx = None
    _shift_snap = True
    _keep_scale = False
    _cached_mode = None

    def invoke(self, context, event):
        self._cached_editable_objects = []
        self._cached_selected_objects = []
        self._cached_bm_selection = {}

        self.snapper = RaraSnapper()
        self.snapper.build_cache(context, scope='ALL')

        self.drawer = ThreePointAlignDrawer(group_size=2)
        self._drag_idx = None
        self.drawer.active_idx = None
        self._preview_pt = None  # 鼠标移动时的预览点
        self._align_mode = 'MOVE'  # 'MOVE' or 'DUPLICATE'
        self.init_region = context.region
        self.initialize_selection_set(context)

        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(draw_align, args, 'WINDOW', 'POST_PIXEL')
        self._handle_view = bpy.types.SpaceView3D.draw_handler_add(self._draw_preview_3d, (context,), 'WINDOW', 'POST_VIEW')
        self._timer = context.window_manager.event_timer_add(time_step=0.03, window=context.window)

        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        pts = len(self.drawer.points)
        snap = "开" if self._shift_snap else "关"
        mode_name = "复制" if self._align_mode == 'DUPLICATE' else "移动"
        self._hud_text = f"【三点对齐】点数: {pts}/6 | C: 模式切换 [{mode_name}] | 左键: 放置 | B: 吸附({snap}) | S: 缩放 | X: 清空 | 回车: 确认 | ESC: 取消"

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}

            if event.type == 'ESC' and event.value == 'PRESS':
                self.finish(context)
                return {'FINISHED'}

            if event.type == 'B' and event.value == 'PRESS':
                self._shift_snap = not self._shift_snap
                snap = "开启" if self._shift_snap else "关闭"
                self.report({'INFO'}, f"自动吸附：{snap}")
                self.update_header(context)
                return {'RUNNING_MODAL'}

            if event.type == 'S' and event.value == 'PRESS':
                self._keep_scale = not self._keep_scale
                mode = "位移+旋转" if self._keep_scale else "位移+旋转+缩放"
                self.report({'INFO'}, f"缩放模式：{mode}")
                self.update_header(context)
                return {'RUNNING_MODAL'}

            if event.type == 'C' and event.value == 'PRESS':
                self._align_mode = 'DUPLICATE' if self._align_mode == 'MOVE' else 'MOVE'
                mode_name = "复制" if self._align_mode == 'DUPLICATE' else "移动"
                self.report({'INFO'}, f"对齐模式：{mode_name}")
                self.update_header(context)
                return {'RUNNING_MODAL'}

            if event.type == 'X' and event.value == 'PRESS':
                self.drawer.points.clear()
                self._drag_idx = None
                self.drawer.active_idx = None
                self.update_header(context)
                self.report({'INFO'}, "已清空所有定位点")
                return {'RUNNING_MODAL'}

            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                idx = self.drawer.pick_point(context, event, radius=12)
                if idx is not None:
                    if is_double_click(0.3):
                        line_idx = idx // self.drawer.group_size
                        start = line_idx * self.drawer.group_size
                        if start + 1 < len(self.drawer.points):
                            self.drawer.points[start], self.drawer.points[start + 1] = \
                                self.drawer.points[start + 1], self.drawer.points[start]
                            self.report({'INFO'}, f"翻转第{line_idx + 1} 组点")
                        return {'RUNNING_MODAL'}
                    else:
                        self._drag_idx = idx
                        self.drawer.active_idx = idx
                        self._preview_pt = None
                else:
                    if len(self.drawer.points) < 6 and self._preview_pt is not None:
                        self.drawer.points.append(self._preview_pt)
                        self._drag_idx = len(self.drawer.points) - 1
                        self.drawer.active_idx = self._drag_idx
                        self._preview_pt = None
                        self.update_header(context)
                        self.report({'INFO'}, f"新增第{len(self.drawer.points)} 个点")
                    elif len(self.drawer.points) >= 6:
                        self.report({'WARNING'}, "已达最大点数（6个），请按X 清空")
                return {'RUNNING_MODAL'}

            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self._drag_idx = None
                self.drawer.active_idx = None
                return {'RUNNING_MODAL'}

            if event.type == 'MOUSEMOVE':
                if self._drag_idx is not None:
                    if self._shift_snap:
                        new_pos = self.snapper.find_nearest(
                            context, event, snap_vertex=True, snap_half=True,
                            penetrate=context.space_data.shading.show_xray)
                        new_pos = new_pos[0] if new_pos else None
                    else:
                        _, new_pos, _ = ray_cast(context, event)
                    if new_pos is not None:
                        self.drawer.points[self._drag_idx] = new_pos
                else:
                    self.drawer.active_idx = self.drawer.pick_point(context, event)
                    if len(self.drawer.points) < 6:
                        if self._shift_snap:
                            result = self.snapper.find_nearest(
                                context, event, snap_vertex=True, snap_half=True,
                                penetrate=context.space_data.shading.show_xray)
                            self._preview_pt = result[0] if result else None
                        else:
                            _, self._preview_pt, _ = ray_cast(context, event)
                    else:
                        self._preview_pt = None
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}

            if event.type in {'RET', 'RIGHTMOUSE'} and event.value == 'PRESS':
                if len(self.drawer.points) == 6:
                    src = [self.drawer.points[i] for i in [0, 2, 4]]
                    dst = [self.drawer.points[i] for i in [1, 3, 5]]
                    mat = compute_3point_transform(src, dst, self._keep_scale)
                    self.restore_selection_set(context)

                    if self._align_mode == 'DUPLICATE':
                        if context.mode == 'EDIT_MESH':
                            for obj in context.editable_objects:
                                if obj.type == 'MESH' and obj.mode == 'EDIT':
                                    bm = bmesh.from_edit_mesh(obj.data)
                                    if any(v.select for v in bm.verts):
                                        context.view_layer.objects.active = obj
                                        bpy.ops.mesh.duplicate_move()
                            for obj in context.editable_objects:
                                if obj.type == 'MESH' and obj.mode == 'EDIT':
                                    bm = bmesh.from_edit_mesh(obj.data)
                                    sel_verts = [v for v in bm.verts if v.select]
                                    world_inv = obj.matrix_world.inverted()
                                    for v in sel_verts:
                                        co_new = mat @ (obj.matrix_world @ v.co)
                                        v.co = world_inv @ co_new
                                    bmesh.update_edit_mesh(obj.data)
                        else:
                            bpy.ops.object.duplicate(linked=False)
                            for obj in context.selected_objects:
                                obj.matrix_world = mat @ obj.matrix_world
                        self.report({'INFO'}, "三点对齐完成（复制模式）")
                    else:
                        if context.mode == 'EDIT_MESH':
                            for obj in context.editable_objects:
                                if obj.type == 'MESH' and obj.mode == 'EDIT':
                                    bm = bmesh.from_edit_mesh(obj.data)
                                    sel_verts = [v for v in bm.verts if v.select]
                                    world_inv = obj.matrix_world.inverted()
                                    for v in sel_verts:
                                        co_new = mat @ (obj.matrix_world @ v.co)
                                        v.co = world_inv @ co_new
                                    bmesh.update_edit_mesh(obj.data)
                            self.report({'INFO'}, "三点对齐完成（编辑模式）")
                        else:
                            for obj in context.selected_objects:
                                obj.matrix_world = mat @ obj.matrix_world
                            self.report({'INFO'}, "三点对齐完成（物体模式）")

                    self.finish(context)
                    return {'FINISHED'}
                else:
                    self.report({'WARNING'}, f"当前点数 {len(self.drawer.points)}/6，请继续添加点")
                    return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}

        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"三点对齐出错，已安全退出: {str(e)}")
            return {'CANCELLED'}

    def _draw_preview_3d(self, context):
        if context.region != getattr(self, "init_region", None):
            return
        if len(self.drawer.points) != 6:
            return

        src = [self.drawer.points[i] for i in [0, 2, 4]]
        dst = [self.drawer.points[i] for i in [1, 3, 5]]
        mat = compute_3point_transform(src, dst, self._keep_scale)

        if self._align_mode == 'DUPLICATE':
            original_lines = self._get_preview_lines(context, Matrix.Identity(4))
            if original_lines:
                draw_lines_3d(original_lines, (1.0, 0.6, 0.1, 0.4))

        preview_lines = self._get_preview_lines(context, mat)
        if preview_lines:
            draw_lines_3d(preview_lines, (1.0, 0.8, 0.0, 0.8))

    def _get_preview_lines(self, context, mat):
        lines = []
        if context.mode == 'EDIT_MESH':
            for obj in context.editable_objects:
                if obj.type != 'MESH' or obj.mode != 'EDIT':
                    continue
                bm = bmesh.from_edit_mesh(obj.data)
                sel_verts = [v for v in bm.verts if v.select]
                if not sel_verts:
                    continue
                world_coords = [mat @ (obj.matrix_world @ v.co) for v in sel_verts]
                min_v = Vector((float('inf'), float('inf'), float('inf')))
                max_v = Vector((float('-inf'), float('-inf'), float('-inf')))
                for c in world_coords:
                    if c.x < min_v.x: min_v.x = c.x
                    if c.y < min_v.y: min_v.y = c.y
                    if c.z < min_v.z: min_v.z = c.z
                    if c.x > max_v.x: max_v.x = c.x
                    if c.y > max_v.y: max_v.y = c.y
                    if c.z > max_v.z: max_v.z = c.z
                lines.extend(make_bbox_wireframe(min_v, max_v))
        else:
            for obj in context.selected_objects:
                if obj.type != 'MESH':
                    continue
                new_mat = mat @ obj.matrix_world
                corners = [new_mat @ Vector(c) for c in obj.bound_box]
                min_v = Vector((float('inf'), float('inf'), float('inf')))
                max_v = Vector((float('-inf'), float('-inf'), float('-inf')))
                for c in corners:
                    if c.x < min_v.x: min_v.x = c.x
                    if c.y < min_v.y: min_v.y = c.y
                    if c.z < min_v.z: min_v.z = c.z
                    if c.x > max_v.x: max_v.x = c.x
                    if c.y > max_v.y: max_v.y = c.y
                    if c.z > max_v.z: max_v.z = c.z
                lines.extend(make_bbox_wireframe(min_v, max_v))
        return lines

    def finish(self, context):
        if self._handle:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        if self._handle_view:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle_view, 'WINDOW')
            self._handle_view = None
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.area.tag_redraw()

    def initialize_selection_set(self, context):
        self._cached_mode = context.mode
        self._cached_bm_selection.clear()
        self._cached_editable_objects.clear()
        self._cached_selected_objects.clear()

        if context.mode == 'EDIT_MESH':
            self._cached_editable_objects = list(context.editable_objects)
            for obj in self._cached_editable_objects:
                if obj.type == 'MESH' and obj.mode == 'EDIT':
                    bm = bmesh.from_edit_mesh(obj.data)
                    self._cached_bm_selection[obj] = (
                        [v.index for v in bm.verts if v.select],
                        [e.index for e in bm.edges if e.select],
                        [f.index for f in bm.faces if f.select],
                    )
        elif context.mode == 'OBJECT':
            self._cached_selected_objects = list(context.selected_objects)

    def restore_selection_set(self, context):
        if self._cached_mode == 'EDIT_MESH':
            for obj, (v_idx, e_idx, f_idx) in self._cached_bm_selection.items():
                if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
                    bm = bmesh.from_edit_mesh(obj.data)
                    for v in bm.verts:
                        v.select = v.index in v_idx
                    for e in bm.edges:
                        e.select = e.index in e_idx
                    for f in bm.faces:
                        f.select = f.index in f_idx
                    bmesh.update_edit_mesh(obj.data, loop_triangles=False)
        elif self._cached_mode == 'OBJECT':
            bpy.ops.object.select_all(action='DESELECT')
            for obj in self._cached_selected_objects:
                if obj:
                    obj.select_set(True)


classes = (RARA_OT_Model_Align,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)