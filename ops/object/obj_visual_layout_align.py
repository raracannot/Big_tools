# 可视化排布与对齐 (合并版)
# A: 对齐 | S: 缩放排布 | R: 旋转

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector, Quaternion
from bpy_extras import view3d_utils
import math
from ...utils.gpu_utils import draw_hud_text, draw_lines_3d, make_bbox_wireframe, CIRCLE_VERTS_FAN, CIRCLE_VERTS_16
from ...utils.math_utils import compute_obb_orientation, collect_world_vertices, get_group_obb_orientation
from ...constants import CIRCLE_HANDLE_RADIUS, BBOX_EDGES

# ============================================================
# 常量: 缩放/旋转模式 圆形控件
# ============================================================

ROT_RING_SEGMENTS = 32
ROT_RING_VERTS = [
    (math.cos(2 * math.pi * i / ROT_RING_SEGMENTS), math.sin(2 * math.pi * i / ROT_RING_SEGMENTS), 0.0)
    for i in range(ROT_RING_SEGMENTS + 1)
]

SCALE_ROT_HANDLES = {
    'X_MAX': (0, 1, (1.0, 0.2, 0.2, 0.8)),
    'X_MIN': (0, -1, (1.0, 0.2, 0.2, 0.8)),
    'Y_MAX': (1, 1, (0.2, 1.0, 0.2, 0.8)),
    'Y_MIN': (1, -1, (0.2, 1.0, 0.2, 0.8)),
    'Z_MAX': (2, 1, (0.2, 0.2, 1.0, 0.8)),
    'Z_MIN': (2, -1, (0.2, 0.2, 1.0, 0.8)),
}

# ============================================================
# 常量: 对齐模式 控件形状
# ============================================================

_DIAMOND_VERTS = [(0, 0), (0, 1), (-1, 0), (0, -1), (1, 0), (0, 1)]
_DIAMOND_OUTLINE = [(0, 1), (-1, 0), (0, -1), (1, 0), (0, 1)]

_CIRCLE_FILL = [(0.0, 0.0)] + CIRCLE_VERTS_16
_CIRCLE_OUTLINE = CIRCLE_VERTS_16

SHAPES = {
    'CIRCLE': (_CIRCLE_FILL, _CIRCLE_OUTLINE, 'TRI_FAN', 'LINE_STRIP'),
    'DIAMOND': (_DIAMOND_VERTS, _DIAMOND_OUTLINE, 'TRI_FAN', 'LINE_STRIP'),
}


def _dist_to_segment_2d(p, a, b):
    ab = b - a
    ap = p - a
    if ab.length_squared == 0:
        return ap.length
    t = max(0.0, min(1.0, ap.dot(ab) / ab.length_squared))
    proj = a + t * ab
    return (p - proj).length


# ============================================================
# 统一操作器
# ============================================================
class RARA_OT_VisualLayoutAlign(bpy.types.Operator):
    bl_idname = "rara.model_visual_layout_align"
    bl_label = "可视化排布与对齐"
    bl_description = "A: 对齐 | S: 缩放排布 | R: 旋转 | TAB: 切换轴向"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.area.type == 'VIEW_3D' 
                and context.mode == 'OBJECT'
                and any(obj.type == 'MESH' for obj in context.selected_objects))

    MODE_ALIGN = 'ALIGN'
    MODE_SCALE = 'SCALE'
    MODE_ROTATE = 'ROTATE'

    def _build_align_handles(self):
        axis_labels = ['X', 'Y', 'Z']
        h = {}
        h['AXIS_LINE_X'] = ({1: 0, 2: 0}, 'ALIGN', (1.0, 0.3, 0.3, 0.8), 'LINE', 0)
        h['AXIS_LINE_Y'] = ({0: 0, 2: 0}, 'ALIGN', (0.3, 1.0, 0.3, 0.8), 'LINE', 1)
        h['AXIS_LINE_Z'] = ({0: 0, 1: 0}, 'ALIGN', (0.3, 0.3, 1.0, 0.8), 'LINE', 2)
        edge_color = (0.9, 0.9, 0.2, 0.9)
        for i in range(3):
            j = (i + 1) % 3
            k = 3 - i - j
            for vi in [-1, 1]:
                for vj in [-1, 1]:
                    v = Vector((0, 0, 0))
                    v[i], v[j] = vi, vj
                    h[f'EDGE_{i}_{j}_{vi}_{vj}'] = (
                        {i: vi, j: vj}, 'ALIGN', edge_color, 'EDGE_LINE', v, k
                    )
        colors = [(1.0, 0.2, 0.2, 0.9), (0.2, 1.0, 0.2, 0.9), (0.2, 0.5, 1.0, 0.9)]
        for i in range(3):
            v_min, v_max = Vector(), Vector()
            v_min[i], v_max[i] = -1.1, 1.1
            h[f'AXIS_{i}_MIN'] = ({i: -1}, 'ALIGN', colors[i], 'CIRCLE', v_min)
            h[f'AXIS_{i}_MAX'] = ({i: 1}, 'ALIGN', colors[i], 'CIRCLE', v_max)
        dist_color = (1.0, 0.6, 0.0, 1.0)
        for i, label in enumerate(axis_labels):
            v_pos, v_neg = Vector((0, 0, 0)), Vector((0, 0, 0))
            v_pos[i], v_neg[i] = 1.2, -1.2
            h[f'DIST_{label}_POS'] = ({i: 2}, 'DISTRIBUTE', dist_color, 'DIAMOND', v_pos)
            h[f'DIST_{label}_NEG'] = ({i: 2}, 'DISTRIBUTE', dist_color, 'DIAMOND', v_neg)
        return h

    def invoke(self, context, event):
        if len([o for o in context.selected_objects if o.type == 'MESH']) < 2:
            self.report({'WARNING'}, "\u8bf7\u81f3\u5c11\u9009\u62e9\u4e24\u4e2a\u7f51\u683c\u5bf9\u8c61")
            return {'CANCELLED'}

        self.draw_handle_view = None
        self.draw_handle_px = None
        self.objects_data = []
        self.min_v = Vector()
        self.max_v = Vector()
        self.group_extents = Vector()
        self.orientation_matrix = Matrix.Identity(4)
        self.center_world = Vector()

        self.current_mode = self.MODE_ALIGN

        self.orient_modes = ['GLOBAL', 'LOCAL', 'CURSOR', 'MIN_BBOX']
        self.orient_names = ['全局轴', '活动对象轴', '游标轴', '最小包围框轴']
        self.current_orient_idx = 0

        self.trans_interp_modes = ['BBOX', 'END_OBJECTS']
        self.trans_interp_names = ['边界框比例', '端头对象比例']
        self.current_trans_interp_idx = 1

        self.rot_pivot_modes = ['BBOX_CENTER', 'INDIVIDUAL_ORIGINS']
        self.rot_pivot_names = ['边界框总轴心', '各对象轴心']
        self.current_rot_pivot_idx = 1

        self.hovered_handle = None
        self.hovered_type = None
        self.active_handle = None
        self.active_type = None
        self.drag_start_mouse = Vector((0, 0))
        self.drag_start_min = Vector()
        self.drag_start_max = Vector()
        self.drag_start_matrices = {}
        self.last_selected_names = set()

        self.align_handles = self._build_align_handles()

        if not self._update_bbox_data(context):
            return {'CANCELLED'}

        args = (self, context)
        self.draw_handle_view = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback_view, args, 'WINDOW', 'POST_VIEW')
        self.draw_handle_px = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback_px, args, 'WINDOW', 'POST_PIXEL')

        context.window_manager.modal_handler_add(self)
        self._update_status(context)
        return {'RUNNING_MODAL'}

    def _update_status(self, context):
        mode_names = {self.MODE_ALIGN: '对齐 [A]', self.MODE_SCALE: '缩放排布 [S]', self.MODE_ROTATE: '旋转 [R]'}
        mode_str = mode_names[self.current_mode]
        orient_str = self.orient_names[self.current_orient_idx]
        extra = ""
        if self.current_mode == self.MODE_SCALE:
            extra = f" | V: 平移比例 [{self.trans_interp_names[self.current_trans_interp_idx]}]"
        elif self.current_mode == self.MODE_ROTATE:
            extra = f" | C: 旋转轴心 [{self.rot_pivot_names[self.current_rot_pivot_idx]}]"
        msg = f"{mode_str} | TAB: 轴向 [{orient_str}]{extra} | 右键/ESC 退出"
        context.workspace.status_text_set(msg)
        self._hud_text = msg

    # ============================================================
    # 模态事件分发
    # ============================================================
    def modal(self, context, event):
        try:
            context.area.tag_redraw()
            current_sel = set(obj.name for obj in context.selected_objects if obj.type == 'MESH')
            if current_sel != self.last_selected_names and not self.active_handle:
                self._update_bbox_data(context)

            if event.type in {'RIGHTMOUSE', 'ESC', 'RET'}:
                self._finish(context)
                return {'FINISHED'}

            if event.type == 'TAB' and event.value == 'PRESS':
                self.current_orient_idx = (self.current_orient_idx + 1) % len(self.orient_modes)
                self._update_bbox_data(context)
                self._update_status(context)
                return {'RUNNING_MODAL'}

            if event.type == 'A' and event.value == 'PRESS':
                self.current_mode = self.MODE_ALIGN
                self.hovered_handle = None
                self.hovered_type = None
                self.active_handle = None
                self.active_type = None
                self._update_status(context)
                return {'RUNNING_MODAL'}

            if event.type == 'S' and event.value == 'PRESS':
                self.current_mode = self.MODE_SCALE
                self.hovered_handle = None
                self.hovered_type = None
                self.active_handle = None
                self.active_type = None
                self._update_status(context)
                return {'RUNNING_MODAL'}

            if event.type == 'R' and event.value == 'PRESS':
                self.current_mode = self.MODE_ROTATE
                self.hovered_handle = None
                self.hovered_type = None
                self.active_handle = None
                self.active_type = None
                self._update_status(context)
                return {'RUNNING_MODAL'}

            if self.current_mode == self.MODE_SCALE and event.type == 'V' and event.value == 'PRESS':
                self.current_trans_interp_idx = (self.current_trans_interp_idx + 1) % len(self.trans_interp_modes)
                self._update_status(context)
                return {'RUNNING_MODAL'}

            if self.current_mode == self.MODE_ROTATE and event.type == 'C' and event.value == 'PRESS':
                self.current_rot_pivot_idx = (self.current_rot_pivot_idx + 1) % len(self.rot_pivot_modes)
                self._update_status(context)
                return {'RUNNING_MODAL'}

            mouse_pos = Vector((event.mouse_region_x, event.mouse_region_y))
            region, rv3d = context.region, context.region_data

            if self.current_mode == self.MODE_ALIGN:
                return self._modal_align(context, event, mouse_pos, region, rv3d)
            elif self.current_mode in {self.MODE_SCALE, self.MODE_ROTATE}:
                return self._modal_scale_rot(context, event, mouse_pos, region, rv3d)

            return {'PASS_THROUGH'}
        except Exception as e:
            self._finish(context)
            self.report({'ERROR'}, f"可视化排布与对齐出错: {str(e)}")
            return {'CANCELLED'}

    # ============================================================
    # 对齐模式 - 模态处理
    # ============================================================
    def _modal_align(self, context, event, mouse_pos, region, rv3d):
        if not self.group_center or len(self.object_names) < 2:
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            self.hovered_handle = self._get_hovered_align_handle(mouse_pos, region, rv3d)
            return {'PASS_THROUGH'}

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self.hovered_handle:
                self._align_objects(self.hovered_handle)
                self._update_bbox_data(context)
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}

    def _get_hovered_align_handle(self, mouse_pos, region, rv3d):
        types = [('DIAMOND', 15.0), ('CIRCLE', 15.0)]
        for check_type, threshold in types:
            best_name = None
            best_dist = threshold
            for name, info in self.align_handles.items():
                shape_type = info[3]
                if check_type == 'DIAMOND' and info[1] != 'DISTRIBUTE':
                    continue
                if check_type == 'CIRCLE' and (shape_type != 'CIRCLE' or info[1] != 'ALIGN'):
                    continue
                if shape_type in ('LINE', 'EDGE_LINE'):
                    continue
                pos3d = self._get_align_handle_pos(name)
                pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d)
                if pos2d:
                    dist = (pos2d - mouse_pos).length
                    if dist < best_dist:
                        best_dist = dist
                        best_name = name
            if best_name:
                return best_name

        min_line_dist = 10.0
        best_line = None
        for name, info in self.align_handles.items():
            shape_type = info[3]
            if shape_type == 'LINE':
                axis_idx = info[4]
                p1, p2 = self.group_center.copy(), self.group_center.copy()
                p1[axis_idx] = self.group_min[axis_idx]
                p2[axis_idx] = self.group_max[axis_idx]
                p1_world = self.orientation_matrix @ p1
                p2_world = self.orientation_matrix @ p2
                p1_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, p1_world)
                p2_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, p2_world)
                if p1_2d and p2_2d:
                    dist = _dist_to_segment_2d(mouse_pos, p1_2d, p2_2d)
                    if dist < min_line_dist:
                        min_line_dist = dist
                        best_line = name
            elif shape_type == 'EDGE_LINE':
                pos3d = self._get_align_handle_pos(name)
                edge_axis = info[5]
                half_len = self.group_extents[edge_axis] * 0.5
                edge_dir = self.orientation_matrix.col[edge_axis].to_3d().normalized()
                p1_world = pos3d - edge_dir * half_len
                p2_world = pos3d + edge_dir * half_len
                p1_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, p1_world)
                p2_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, p2_world)
                if p1_2d and p2_2d:
                    dist = _dist_to_segment_2d(mouse_pos, p1_2d, p2_2d)
                    if dist < min_line_dist:
                        min_line_dist = dist
                        best_line = name
        return best_line

    def _get_align_handle_pos(self, handle_name):
        if not self.group_center:
            return Vector()
        shape_type = self.align_handles[handle_name][3]
        if shape_type == 'LINE':
            return Vector()
        pos_mult = self.align_handles[handle_name][4]
        padding = 1.0
        local_pos = self.group_center + Vector((
            self.group_extents.x * pos_mult.x * padding,
            self.group_extents.y * pos_mult.y * padding,
            self.group_extents.z * pos_mult.z * padding,
        ))
        return self.orientation_matrix @ local_pos

    def _calculate_deltas(self, handle_name):
        deltas = {}
        if not self.group_center:
            return deltas
        axes_dict, action_type = self.align_handles[handle_name][:2]
        valid_objs = [bpy.data.objects.get(n) for n in self.object_names if bpy.data.objects.get(n)]
        if not valid_objs:
            return deltas
        inv_matrix = self.orientation_matrix.inverted()
        if action_type == 'ALIGN':
            for obj in valid_objs:
                corners = [inv_matrix @ (obj.matrix_world @ Vector(c)) for c in obj.bound_box]
                delta_local = Vector((0, 0, 0))
                for axis_idx, pos_type in axes_dict.items():
                    obj_min = min(v[axis_idx] for v in corners)
                    obj_max = max(v[axis_idx] for v in corners)
                    obj_cen = (obj_min + obj_max) / 2.0
                    if pos_type == -1:
                        target_val = self.group_min[axis_idx]
                    elif pos_type == 1:
                        target_val = self.group_max[axis_idx]
                    else:
                        target_val = self.group_center[axis_idx]
                    if pos_type == -1:
                        d = target_val - obj_min
                    elif pos_type == 1:
                        d = target_val - obj_max
                    else:
                        d = target_val - obj_cen
                    delta_local[axis_idx] = d
                deltas[obj.name] = delta_local
        elif action_type == 'DISTRIBUTE':
            axis_idx = list(axes_dict.keys())[0]

            def get_cen(o):
                c = [inv_matrix @ (o.matrix_world @ Vector(v)) for v in o.bound_box]
                return (min(v[axis_idx] for v in c) + max(v[axis_idx] for v in c)) / 2.0

            sorted_objs = sorted(valid_objs, key=get_cen)
            if len(sorted_objs) > 2:
                first_cen = get_cen(sorted_objs[0])
                last_cen = get_cen(sorted_objs[-1])
                step = (last_cen - first_cen) / (len(sorted_objs) - 1)
                for i, obj in enumerate(sorted_objs):
                    delta_local = Vector((0, 0, 0))
                    if i != 0 and i != len(sorted_objs) - 1:
                        current_cen = get_cen(obj)
                        target_cen = first_cen + i * step
                        delta_local[axis_idx] = target_cen - current_cen
                    deltas[obj.name] = delta_local
        return deltas

    def _align_objects(self, handle_name):
        deltas = self._calculate_deltas(handle_name)
        for name, delta_local in deltas.items():
            obj = bpy.data.objects.get(name)
            if obj:
                delta_world = self.orientation_matrix.to_3x3() @ delta_local
                obj.matrix_world.translation += delta_world
        bpy.ops.ed.undo_push(message=f"可视化操作: {handle_name}")

    # ============================================================
    # 缩放/旋转模式 - 模态处理
    # ============================================================
    def _modal_scale_rot(self, context, event, mouse_pos, region, rv3d):
        if not self.objects_data:
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            if self.active_handle:
                self._handle_drag_scale_rot(mouse_pos, region, rv3d)
                return {'RUNNING_MODAL'}
            else:
                self.hovered_handle, self.hovered_type = self._get_hovered_scale_rot(mouse_pos, region, rv3d)
                return {'PASS_THROUGH'}

        elif event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                if self.hovered_handle:
                    self.active_handle = self.hovered_handle
                    self.active_type = self.hovered_type
                    self.drag_start_mouse = mouse_pos
                    self.drag_start_min = self.min_v.copy()
                    self.drag_start_max = self.max_v.copy()
                    self.drag_start_matrices.clear()
                    for item in self.objects_data:
                        obj = bpy.data.objects.get(item['name'])
                        if obj:
                            self.drag_start_matrices[obj.name] = obj.matrix_world.copy()
                    return {'RUNNING_MODAL'}
                return {'PASS_THROUGH'}
            elif event.value == 'RELEASE':
                if self.active_handle:
                    self.active_handle = None
                    self.active_type = None
                    self._update_bbox_data(context)
                    return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        if event.type not in {'MOUSEMOVE', 'LEFTMOUSE', 'RIGHTMOUSE', 'ESC', 'RET', 'TAB', 'A', 'S', 'R', 'V', 'C'}:
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def _get_hovered_scale_rot(self, mouse_pos, region, rv3d):
        if not self.objects_data:
            return None, None
        closest_handle = None
        closest_type = None
        min_dist = 40.0
        for name, info in SCALE_ROT_HANDLES.items():
            pos3d = self._get_scale_rot_handle_pos(name)
            pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d)
            if pos2d:
                dist = (pos2d - mouse_pos).length
                if dist < min_dist:
                    min_dist = dist
                    closest_handle = name
                    if self.current_mode == self.MODE_SCALE:
                        closest_type = 'TRANS'
                    else:
                        closest_type = 'ROT' if dist < CIRCLE_HANDLE_RADIUS * 1.5 else 'ROT'
        return closest_handle, closest_type

    def _get_scale_rot_handle_pos(self, handle_name):
        c = (self.min_v + self.max_v) / 2.0
        local_pos = c.copy()
        if handle_name == 'X_MAX':
            local_pos.x = self.max_v.x
        elif handle_name == 'X_MIN':
            local_pos.x = self.min_v.x
        elif handle_name == 'Y_MAX':
            local_pos.y = self.max_v.y
        elif handle_name == 'Y_MIN':
            local_pos.y = self.min_v.y
        elif handle_name == 'Z_MAX':
            local_pos.z = self.max_v.z
        elif handle_name == 'Z_MIN':
            local_pos.z = self.min_v.z
        return self.orientation_matrix @ local_pos

    def _handle_drag_scale_rot(self, mouse_pos, region, rv3d):
        axis_idx, direction, _ = SCALE_ROT_HANDLES[self.active_handle]
        pos3d_start = self._get_scale_rot_handle_pos(self.active_handle)
        axis_vec_local = Vector((0, 0, 0))
        axis_vec_local[axis_idx] = direction
        axis_vec_world = self.orientation_matrix @ axis_vec_local

        if self.current_mode == self.MODE_SCALE:
            pos2d_start = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d_start)
            pos2d_end = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d_start + axis_vec_world)
            if not pos2d_start or not pos2d_end:
                return
            screen_axis_vec = pos2d_end - pos2d_start
            if screen_axis_vec.length < 0.001:
                return
            proj_dist = (mouse_pos - self.drag_start_mouse).dot(screen_axis_vec.normalized())
            delta_3d = proj_dist * 0.01
            self.min_v = self.drag_start_min.copy()
            self.max_v = self.drag_start_max.copy()
            if direction == 1:
                self.max_v[axis_idx] = max(self.min_v[axis_idx] + 0.01,
                                           self.drag_start_max[axis_idx] + delta_3d)
            else:
                self.min_v[axis_idx] = min(self.max_v[axis_idx] - 0.01,
                                           self.drag_start_min[axis_idx] - delta_3d)
            delta_min = self.min_v - self.drag_start_min
            delta_max = self.max_v - self.drag_start_max
            trans_mode = self.trans_interp_modes[self.current_trans_interp_idx]
            for item in self.objects_data:
                obj = bpy.data.objects.get(item['name'])
                if not obj:
                    continue
                orig_mat = self.drag_start_matrices.get(obj.name)
                if not orig_mat:
                    continue
                weight = item['rel'] if trans_mode == 'BBOX' else item['end_rel']
                obj_delta_local = Vector((
                    delta_min.x * (1 - weight.x) + delta_max.x * weight.x,
                    delta_min.y * (1 - weight.y) + delta_max.y * weight.y,
                    delta_min.z * (1 - weight.z) + delta_max.z * weight.z,
                ))
                obj_delta_world = self.orientation_matrix @ obj_delta_local
                obj.matrix_world = Matrix.Translation(obj_delta_world) @ orig_mat

        elif self.current_mode == self.MODE_ROTATE:
            delta_mouse = mouse_pos - self.drag_start_mouse
            base_angle = (delta_mouse.x + delta_mouse.y) * 0.01 * direction
            pivot_mode = self.rot_pivot_modes[self.current_rot_pivot_idx]
            for item in self.objects_data:
                obj = bpy.data.objects.get(item['name'])
                if not obj:
                    continue
                orig_mat = self.drag_start_matrices.get(obj.name)
                if not orig_mat:
                    continue
                rel_val = item['rel'][axis_idx]
                weight = rel_val if direction == 1 else (1.0 - rel_val)
                obj_angle = base_angle * weight
                rot_quat = Quaternion(axis_vec_world, obj_angle)
                rot_mat = rot_quat.to_matrix().to_4x4()
                if pivot_mode == 'BBOX_CENTER':
                    pivot = self.center_world
                else:
                    pivot = orig_mat.translation
                trans_to_origin = Matrix.Translation(-pivot)
                trans_back = Matrix.Translation(pivot)
                obj.matrix_world = trans_back @ rot_mat @ trans_to_origin @ orig_mat

    # ============================================================
    # 数据更新 (共享)
    # ============================================================
    def _update_bbox_data(self, context):
        objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
        self.last_selected_names = set(obj.name for obj in objects)
        if len(objects) < 2:
            self.objects_data.clear()
            self.group_center = None
            self.object_names = []
            return False

        self.object_names = [obj.name for obj in objects]
        mode = self.orient_modes[self.current_orient_idx]
        if mode == 'GLOBAL':
            self.orientation_matrix = Matrix.Identity(4)
        elif mode == 'LOCAL':
            active = context.active_object
            self.orientation_matrix = active.matrix_world.to_3x3().to_4x4() if active else Matrix.Identity(4)
        elif mode == 'CURSOR':
            self.orientation_matrix = context.scene.cursor.matrix.to_3x3().to_4x4()
        elif mode == 'MIN_BBOX':
            self.orientation_matrix = get_group_obb_orientation(objects)

        inv_matrix = self.orientation_matrix.inverted()
        min_v = Vector((float('inf'), float('inf'), float('inf')))
        max_v = Vector((float('-inf'), float('-inf'), float('-inf')))
        obj_centers = []
        for obj in objects:
            corners = [inv_matrix @ (obj.matrix_world @ Vector(c)) for c in obj.bound_box]
            for v in corners:
                min_v.x = min(min_v.x, v.x)
                min_v.y = min(min_v.y, v.y)
                min_v.z = min(min_v.z, v.z)
                max_v.x = max(max_v.x, v.x)
                max_v.y = max(max_v.y, v.y)
                max_v.z = max(max_v.z, v.z)
            center_local = sum(corners, Vector()) / 8.0
            obj_centers.append((obj.name, center_local))

        self.min_v = min_v
        self.max_v = max_v
        self.group_min = min_v
        self.group_max = max_v
        self.center_world = self.orientation_matrix @ ((min_v + max_v) / 2.0)
        self.group_center = (min_v + max_v) / 2.0
        self.group_extents = (max_v - min_v) / 2.0

        bbox_size = max_v - min_v
        min_c = Vector((float('inf'), float('inf'), float('inf')))
        max_c = Vector((float('-inf'), float('-inf'), float('-inf')))
        for _, c in obj_centers:
            for i in range(3):
                min_c[i] = min(min_c[i], c[i])
                max_c[i] = max(max_c[i], c[i])
        c_size = max_c - min_c

        self.objects_data.clear()
        for name, center_local in obj_centers:
            rel_x = (center_local.x - min_v.x) / bbox_size.x if bbox_size.x > 0.0001 else 0.5
            rel_y = (center_local.y - min_v.y) / bbox_size.y if bbox_size.y > 0.0001 else 0.5
            rel_z = (center_local.z - min_v.z) / bbox_size.z if bbox_size.z > 0.0001 else 0.5
            e_rel_x = (center_local.x - min_c.x) / c_size.x if c_size.x > 0.0001 else 0.5
            e_rel_y = (center_local.y - min_c.y) / c_size.y if c_size.y > 0.0001 else 0.5
            e_rel_z = (center_local.z - min_c.z) / c_size.z if c_size.z > 0.0001 else 0.5
            self.objects_data.append({
                'name': name,
                'rel': Vector((rel_x, rel_y, rel_z)),
                'end_rel': Vector((e_rel_x, e_rel_y, e_rel_z)),
            })
        return True

    # ============================================================
    # 3D 视图绘制 (共享包围盒 + 模式特定控件)
    # ============================================================
    def _draw_callback_view(self, op, context):
        if not self.objects_data:
            return

        if self.current_mode == self.MODE_ALIGN:
            self._draw_view_align(context)
        else:
            self._draw_view_scale_rot(context)

    def _draw_view_align(self, context):
        if not getattr(self, 'group_center', None) or len(self.object_names) < 2:
            return
        rv3d = context.region_data
        depth_test = 'NONE' if context.space_data.shading.show_xray else 'LESS_EQUAL'

        lines = make_bbox_wireframe(self.group_min, self.group_max, self.orientation_matrix)
        draw_lines_3d(lines, (1.0, 1.0, 1.0, 0.1), 1.0, depth_test)

        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set(depth_test)
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader.bind()

        for name, info in self.align_handles.items():
            if info[3] != 'LINE':
                continue
            axis_idx = info[4]
            base_color = info[2]
            p1, p2 = self.group_center.copy(), self.group_center.copy()
            p1[axis_idx] = self.group_min[axis_idx]
            p2[axis_idx] = self.group_max[axis_idx]
            p1_world = self.orientation_matrix @ p1
            p2_world = self.orientation_matrix @ p2
            is_hovered = (name == self.hovered_handle)
            color = (1.0, 1.0, 1.0, 1.0) if is_hovered else base_color
            gpu.state.line_width_set(6.0 if is_hovered else 3.0)
            batch_axis = batch_for_shader(shader, 'LINES', {"pos": [p1_world, p2_world]})
            shader.uniform_float("color", color)
            batch_axis.draw(shader)

        view_inv = rv3d.view_matrix.inverted()
        cam_pos = view_inv.translation
        cam_right = view_inv.col[0].to_3d().normalized()
        cam_up = view_inv.col[1].to_3d().normalized()

        for name, info in self.align_handles.items():
            if info[3] == 'LINE':
                continue
            axes_dict, action_type, base_color, shape_type = info[:4]
            pos3d = self._get_align_handle_pos(name)
            is_hovered = (name == self.hovered_handle)
            color = (1.0, 1.0, 1.0, 1.0) if is_hovered else base_color

            if shape_type == 'EDGE_LINE':
                edge_axis = info[5]
                half_len = self.group_extents[edge_axis] * 0.25
                edge_dir_world = self.orientation_matrix.col[edge_axis].to_3d().normalized()
                p1 = pos3d - edge_dir_world * half_len
                p2 = pos3d + edge_dir_world * half_len
                gpu.state.line_width_set(4.0 if is_hovered else 2.0)
                batch_edge = batch_for_shader(shader, 'LINES', {"pos": [p1, p2]})
                shader.uniform_float("color", color)
                batch_edge.draw(shader)
                continue

            dist = (pos3d - cam_pos).length if rv3d.is_perspective else rv3d.view_distance
            base_radius = dist * 0.006
            if is_hovered:
                base_radius *= 1.4
            verts_2d, outline_2d, draw_mode, outline_mode = SHAPES[shape_type]
            verts_3d = [pos3d + cam_right * (v[0] * base_radius) + cam_up * (v[1] * base_radius) for v in verts_2d]
            outline_3d = [pos3d + cam_right * (v[0] * base_radius) + cam_up * (v[1] * base_radius) for v in outline_2d]
            batch_fill = batch_for_shader(shader, draw_mode, {"pos": verts_3d})
            shader.uniform_float("color", color)
            batch_fill.draw(shader)
            gpu.state.line_width_set(2.0)
            batch_outline = batch_for_shader(shader, outline_mode, {"pos": outline_3d})
            shader.uniform_float("color", (0.0, 0.0, 0.0, 0.8))
            batch_outline.draw(shader)

        preview_deltas = None
        if self.hovered_handle and self.align_handles[self.hovered_handle][1] in ('ALIGN', 'DISTRIBUTE'):
            preview_deltas = self._calculate_deltas(self.hovered_handle)
        if preview_deltas:
            preview_lines = []
            inv_matrix = self.orientation_matrix.inverted()
            for name, delta_local in preview_deltas.items():
                obj = bpy.data.objects.get(name)
                if obj:
                    corners_local = [inv_matrix @ (obj.matrix_world @ Vector(c)) for c in obj.bound_box]
                    new_corners_local = [c + delta_local for c in corners_local]
                    new_corners_world = [self.orientation_matrix @ c for c in new_corners_local]
                    for e in BBOX_EDGES:
                        preview_lines.extend([new_corners_world[e[0]], new_corners_world[e[1]]])
            if preview_lines:
                draw_lines_3d(preview_lines, (1.0, 0.8, 0.0, 0.8), 2.0, 'NONE')

        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('NONE')

    def _draw_view_scale_rot(self, context):
        if not self.objects_data:
            return

        lines = make_bbox_wireframe(self.min_v, self.max_v, self.orientation_matrix)
        draw_lines_3d(lines, (1.0, 0.8, 0.2, 0.5), 2.0, 'LESS_EQUAL')

        if self.current_mode == self.MODE_ROTATE:
            self._draw_rot_rings(context)

    def _draw_rot_rings(self, context):
        rv3d = context.region_data
        cam_pos = rv3d.view_matrix.inverted().translation
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader.bind()
        for name, info in SCALE_ROT_HANDLES.items():
            axis_idx, _, color = info
            pos3d = self._get_scale_rot_handle_pos(name)
            dist_to_cam = (pos3d - cam_pos).length
            ring_radius = dist_to_cam * 0.03
            ring_mat = Matrix.Identity(3)
            if axis_idx == 0:
                ring_mat = Matrix.Rotation(math.pi / 2, 3, 'Y')
            elif axis_idx == 1:
                ring_mat = Matrix.Rotation(math.pi / 2, 3, 'X')
            ring_world_mat = self.orientation_matrix.to_3x3() @ ring_mat
            ring_lines = []
            for i in range(ROT_RING_SEGMENTS):
                v1 = Vector(ROT_RING_VERTS[i]) * ring_radius
                v2 = Vector(ROT_RING_VERTS[i + 1]) * ring_radius
                ring_lines.append(pos3d + ring_world_mat @ v1)
                ring_lines.append(pos3d + ring_world_mat @ v2)
            is_active = (name == self.active_handle or name == self.hovered_handle)
            draw_color = (1.0, 1.0, 1.0, 1.0) if is_active else color
            gpu.state.line_width_set(8.0 if is_active else 6.0)
            batch_ring = batch_for_shader(shader, 'LINES', {"pos": ring_lines})
            shader.uniform_float("color", draw_color)
            batch_ring.draw(shader)
        gpu.state.blend_set('NONE')
        gpu.state.line_width_set(1.0)
        gpu.state.depth_test_set('NONE')

    # ============================================================
    # 像素空间绘制 (HUD + 模式特定控件)
    # ============================================================
    def _draw_callback_px(self, op, context):
        if not self.objects_data:
            self._draw_hud_only(context)
            return
        if self.current_mode == self.MODE_ALIGN:
            self._draw_hud_only(context)
        elif self.current_mode == self.MODE_ROTATE:
            self._draw_hud_only(context)
        elif self.current_mode == self.MODE_SCALE:
            self._draw_px_scale(context)

    def _draw_hud_only(self, context):
        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)

    def _draw_px_scale(self, context):
        region, rv3d = context.region, context.region_data
        gpu.state.blend_set('ALPHA')
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader.bind()
        for name, info in SCALE_ROT_HANDLES.items():
            pos3d = self._get_scale_rot_handle_pos(name)
            pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d)
            if not pos2d:
                continue
            is_active = (name == self.active_handle or name == self.hovered_handle)
            color = (1.0, 1.0, 1.0, 1.0) if is_active else info[2]
            scale = 1.5 if is_active else 1.0
            s = CIRCLE_HANDLE_RADIUS * scale
            verts = [(pos2d.x + v[0] * s, pos2d.y + v[1] * s) for v in CIRCLE_VERTS_FAN]
            batch = batch_for_shader(shader, 'TRI_FAN', {"pos": verts})
            shader.uniform_float("color", color)
            batch.draw(shader)
        gpu.state.blend_set('NONE')

        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)

    # ============================================================
    # 清理
    # ============================================================
    def _finish(self, context):
        if self.draw_handle_view:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_view, 'WINDOW')
        if self.draw_handle_px:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_px, 'WINDOW')
        context.workspace.status_text_set(None)


classes = (RARA_OT_VisualLayoutAlign,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)