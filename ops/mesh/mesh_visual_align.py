# 可视化对齐
import bpy
import bmesh
import gpu
from gpu_extras.batch import batch_for_shader
import mathutils
from bpy_extras import view3d_utils
import math
import numpy as np
from ...utils.gpu_utils import draw_circle_2d, draw_hud_text
from ...utils.rara_snapper import RaraSnapper


def draw_callback_visual_align_3d(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(3.0)

    colors = {
        'X': (1.0, 0.2, 0.2, 1.0),
        'Y': (0.2, 1.0, 0.2, 1.0),
        'Z': (0.2, 0.2, 1.0, 1.0)
    }

    orient_matrix = self._get_orient_matrix(context)

    for axis, color in colors.items():
        if getattr(self, 'is_dragging', False) and getattr(self, 'active_axis', None) != axis:
            draw_color = (color[0] * 0.3, color[1] * 0.3, color[2] * 0.3, 0.5)
        elif getattr(self, 'active_axis', None) == axis:
            draw_color = (color[0], color[1], color[2], 1.0)
        else:
            draw_color = (color[0] * 0.8, color[1] * 0.8, color[2] * 0.8, 0.8)

        axis_idx = {'X': 0, 'Y': 1, 'Z': 2}[axis]
        axis_dir = orient_matrix.col[axis_idx]
        end_point = self.center_world + axis_dir * self.axis_length

        batch_line = batch_for_shader(shader, 'LINES', {"pos": [self.center_world, end_point]})
        shader.bind()
        shader.uniform_float("color", draw_color)
        batch_line.draw(shader)

    if getattr(self, 'is_dragging', False) and getattr(self, 'snapped_pos_world', None):
        gpu.state.line_width_set(1.0)
        active_axis = getattr(self, 'active_axis', None)
        if active_axis:
            active_idx = {'X': 0, 'Y': 1, 'Z': 2}[active_axis]
            active_end = self.center_world + orient_matrix.col[active_idx] * self.axis_length

            batch_snap_line = batch_for_shader(shader, 'LINES', {"pos": [active_end, self.snapped_pos_world]})
            shader.uniform_float("color", (1.0, 1.0, 0.0, 1.0))
            batch_snap_line.draw(shader)

            u_idx, v_idx = [i for i in (0, 1, 2) if i != active_idx]
            U = orient_matrix.col[u_idx].normalized()
            V = orient_matrix.col[v_idx].normalized()

            radius = self.axis_length * 0.15
            segments = 32
            circle_3d_verts = []

            for i in range(segments + 1):
                angle = 2.0 * math.pi * i / segments
                p = self.snapped_pos_world + radius * (math.cos(angle) * U + math.sin(angle) * V)
                circle_3d_verts.append(p)

            batch_snap_circle = batch_for_shader(shader, 'LINE_STRIP', {"pos": circle_3d_verts})
            gpu.state.line_width_set(2.0)

            gpu.state.depth_test_set('LESS_EQUAL')
            batch_snap_circle.draw(shader)
            gpu.state.depth_test_set('NONE')


def draw_callback_visual_align_2d(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    region = context.region
    rv3d = context.region_data

    colors = {
        'X': (1.0, 0.2, 0.2, 1.0),
        'Y': (0.2, 1.0, 0.2, 1.0),
        'Z': (0.2, 0.2, 1.0, 1.0)
    }

    orient_matrix = self._get_orient_matrix(context)

    for axis, color in colors.items():
        axis_idx = {'X': 0, 'Y': 1, 'Z': 2}[axis]
        axis_dir = orient_matrix.col[axis_idx]
        end_point = self.center_world + axis_dir * self.axis_length

        pos_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, end_point)
        if not pos_2d:
            continue

        if getattr(self, 'is_dragging', False) and getattr(self, 'active_axis', None) != axis:
            draw_color = (color[0] * 0.3, color[1] * 0.3, color[2] * 0.3, 0.5)
            inner_r, outer_r = 6, 8
        elif getattr(self, 'active_axis', None) == axis:
            draw_color = color
            inner_r, outer_r = 10, 12
        else:
            draw_color = (color[0] * 0.8, color[1] * 0.8, color[2] * 0.8, 0.8)
            inner_r, outer_r = 7, 9

        draw_circle_2d(pos_2d, inner_r, draw_color, filled=True)
        draw_circle_2d(pos_2d, outer_r, draw_color, filled=False)

    if getattr(self, 'is_dragging', False) and getattr(self, 'snapped_pos_world', None):
        snap_pos_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, self.snapped_pos_world)
        if snap_pos_2d:
            draw_circle_2d(snap_pos_2d, 5, (1.0, 1.0, 0.0, 1.0), filled=True)
            draw_circle_2d(snap_pos_2d, 7, (1.0, 1.0, 0.0, 1.0), filled=False)

    hud = getattr(self, '_hud_text', None)
    if hud:
        draw_hud_text(hud, context)


class RARA_OT_Model_VisualAlign(bpy.types.Operator):
    bl_idname = "rara.model_visual_align"
    bl_label = "可视化对齐"
    bl_description = "通过GPU绘制的可视化手柄，将选中的顶点对齐到吸附点\n【TAB】切换坐标系 | 【B】吸附开关 | 【ESC】取消"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    current_orient_idx = 0

    def invoke(self, context, event):
        if context.mode != 'EDIT_MESH':
            self.report({'WARNING'}, "请在编辑模式下使用")
            return {'CANCELLED'}

        self.init_region = context.region
        self.draw_handle_3d = None
        self.draw_handle_2d = None
        self.bm = None
        self.obj = None
        self.center_world = mathutils.Vector((0, 0, 0))
        self.axis_length = 2.0
        self.active_axis = None
        self.is_dragging = False
        self.mouse_pos = (0, 0)
        self.snapped_pos_world = None
        self.last_sel_hash = None
        self._snap_enabled = True

        self.orientations = ['LOCAL', 'WORLD', 'VIEW', 'CURSOR']
        self.orient_names_zh = {
            'LOCAL': '局部',
            'WORLD': '世界',
            'VIEW': '视图',
            'CURSOR': '游标'
        }

        self.obj = context.edit_object
        self.snapper = RaraSnapper()
        self.snapper.build_cache(context, scope='SELECTED')
        self._update_center_and_cache()

        sel_verts_for_bbox = [v for v in self.bm.verts if v.select]
        if sel_verts_for_bbox:
            wm = self.obj.matrix_world
            min_v = mathutils.Vector((float('inf'),) * 3)
            max_v = mathutils.Vector((float('-inf'),) * 3)
            for v in sel_verts_for_bbox:
                wc = wm @ v.co
                min_v.x = min(min_v.x, wc.x)
                min_v.y = min(min_v.y, wc.y)
                min_v.z = min(min_v.z, wc.z)
                max_v.x = max(max_v.x, wc.x)
                max_v.y = max(max_v.y, wc.y)
                max_v.z = max(max_v.z, wc.z)
            dims = max_v - min_v
            self.axis_length = max(dims) * 0.6 + 0.2
        else:
            self.axis_length = 2.0

        args = (self, context)
        self.draw_handle_3d = bpy.types.SpaceView3D.draw_handler_add(
            draw_callback_visual_align_3d, args, 'WINDOW', 'POST_VIEW'
        )
        self.draw_handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            draw_callback_visual_align_2d, args, 'WINDOW', 'POST_PIXEL'
        )

        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)

        self.update_header(context)
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        mode = self.orientations[RARA_OT_Model_VisualAlign.current_orient_idx]
        snap = "全吸附" if self._snap_enabled else "顶点"
        self._hud_text = f"【可视化对齐】坐标系: {self.orient_names_zh[mode]} | B: 吸附({snap}) | TAB: 切换坐标系 | ESC: 取消"

    def _update_center_and_cache(self):
        self.obj.update_from_editmode()
        self.bm = bmesh.from_edit_mesh(self.obj.data)
        self.bm.verts.ensure_lookup_table()

        sel_verts = [v for v in self.bm.verts if v.select]

        self.last_sel_hash = hash(tuple(v.index for v in sel_verts))

        if sel_verts:
            matrix_world = self.obj.matrix_world
            center_local = sum((v.co for v in sel_verts), mathutils.Vector()) / len(sel_verts)
            self.center_world = matrix_world @ center_local

        num_verts = len(self.obj.data.vertices)
        if num_verts > 0:
            coords = np.zeros(num_verts * 3, dtype=np.float32)
            self.obj.data.vertices.foreach_get("co", coords)
            coords = coords.reshape((num_verts, 3))

            ones = np.ones((num_verts, 1), dtype=np.float32)
            coords_4d = np.hstack((coords, ones))

            matrix_world = np.array(self.obj.matrix_world, dtype=np.float32)
            self.verts_world_4d = coords_4d @ matrix_world.T
            self.verts_world_3d = self.verts_world_4d[:, :3]
        else:
            self.verts_world_4d = None

    def _get_orient_matrix(self, context):
        mode = self.orientations[RARA_OT_Model_VisualAlign.current_orient_idx]
        if mode == 'WORLD':
            return mathutils.Matrix.Identity(3)
        elif mode == 'LOCAL':
            return self.obj.matrix_world.to_3x3().normalized()
        elif mode == 'VIEW':
            return context.region_data.view_matrix.inverted().to_3x3().normalized()
        elif mode == 'CURSOR':
            return context.scene.cursor.matrix.to_3x3().normalized()
        return mathutils.Matrix.Identity(3)

    def _find_nearest(self, context, mouse_pos):
        """使用 RaraSnapper 查找最近顶点"""
        event = type('Event', (), {
            'mouse_region_x': mouse_pos[0],
            'mouse_region_y': mouse_pos[1]
        })()
        pt, snap_type = self.snapper.find_nearest(
            context, event,
            snap_vertex=True,
            snap_half=self._snap_enabled,
            snap_third=self._snap_enabled,
            snap_edge=self._snap_enabled,
            snap_face=self._snap_enabled
        )
        return pt

    def _apply_alignment(self, context):
        if not self.snapped_pos_world or not self.active_axis:
            return

        orient_matrix = self._get_orient_matrix(context)
        axis_idx = {'X': 0, 'Y': 1, 'Z': 2}[self.active_axis]
        axis_dir_world = orient_matrix.col[axis_idx].normalized()

        target_world = self.snapped_pos_world
        matrix_world = self.obj.matrix_world
        matrix_world_inv = matrix_world.inverted()

        for v in self.bm.verts:
            if v.select:
                v_world = matrix_world @ v.co
                dist = (target_world - v_world).dot(axis_dir_world)
                v_world_aligned = v_world + axis_dir_world * dist
                v.co = matrix_world_inv @ v_world_aligned

        bmesh.update_edit_mesh(self.obj.data)
        self._update_center_and_cache()

    def finish(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

        if self.draw_handle_3d:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_3d, 'WINDOW')
            self.draw_handle_3d = None
        if self.draw_handle_2d:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_2d, 'WINDOW')
            self.draw_handle_2d = None

        self.verts_world_4d = None
        self.verts_world_3d = None
        context.area.tag_redraw()

    def modal(self, context, event):
        try:
            context.area.tag_redraw()

            if event.type == 'MOUSEMOVE':
                self.mouse_pos = (event.mouse_region_x, event.mouse_region_y)

            if event.type == 'TIMER':
                if not self.is_dragging:
                    try:
                        current_hash = hash(tuple(v.index for v in self.bm.verts if v.select))
                        if current_hash != self.last_sel_hash:
                            self._update_center_and_cache()
                    except ReferenceError:
                        if context.mode == 'EDIT_MESH' and self.obj == context.edit_object:
                            self._update_center_and_cache()
                        else:
                            self.finish(context)
                            return {'CANCELLED'}
                return {'PASS_THROUGH'}

            nav_events = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}
            if event.type in nav_events or event.type.startswith('NUMPAD_'):
                return {'PASS_THROUGH'}

            if event.type == 'ESC' and event.value == 'PRESS':
                self.finish(context)
                return {'CANCELLED'}

            if event.type == 'B' and event.value == 'PRESS':
                self._snap_enabled = not self._snap_enabled
                self.update_header(context)
                self.report({'INFO'}, f"吸附: {'全吸附' if self._snap_enabled else '仅顶点'}")
                return {'RUNNING_MODAL'}

            if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                if self.is_dragging:
                    self.is_dragging = False
                    self.snapped_pos_world = None
                    return {'RUNNING_MODAL'}
                else:
                    return {'PASS_THROUGH'}

            if event.type == 'TAB' and event.value == 'PRESS':
                RARA_OT_Model_VisualAlign.current_orient_idx = (RARA_OT_Model_VisualAlign.current_orient_idx + 1) % len(self.orientations)
                mode = self.orientations[RARA_OT_Model_VisualAlign.current_orient_idx]
                self.report({'INFO'}, f"坐标系切换为: {self.orient_names_zh[mode]}")
                self.update_header(context)
                return {'RUNNING_MODAL'}

            region = context.region
            rv3d = context.region_data

            orient_matrix = self._get_orient_matrix(context)
            axes = {
                'X': self.center_world + orient_matrix.col[0] * self.axis_length,
                'Y': self.center_world + orient_matrix.col[1] * self.axis_length,
                'Z': self.center_world + orient_matrix.col[2] * self.axis_length
            }

            if event.type == 'MOUSEMOVE':
                if not self.is_dragging:
                    self.active_axis = None
                    min_dist = 20.0
                    for axis, pos_world in axes.items():
                        pos_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pos_world)
                        if pos_2d:
                            dist = (pos_2d - mathutils.Vector(self.mouse_pos)).length
                            if dist < min_dist:
                                min_dist = dist
                                self.active_axis = axis
                    return {'PASS_THROUGH'}
                else:
                    self.snapped_pos_world = self._find_nearest(context, self.mouse_pos)
                    return {'RUNNING_MODAL'}

            elif event.type == 'LEFTMOUSE':
                if event.value == 'PRESS':
                    if self.active_axis:
                        self.is_dragging = True
                        return {'RUNNING_MODAL'}
                    else:
                        return {'PASS_THROUGH'}

                elif event.value == 'RELEASE':
                    if self.is_dragging:
                        self._apply_alignment(context)
                        self.is_dragging = False
                        self.snapped_pos_world = None
                        return {'RUNNING_MODAL'}
                    else:
                        return {'PASS_THROUGH'}

            return {'PASS_THROUGH'}

        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"可视化对齐出错: {str(e)}")
            return {'CANCELLED'}


classes = (RARA_OT_Model_VisualAlign,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)