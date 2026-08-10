# 可视化对齐(对象包围盒)

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector
from bpy_extras import view3d_utils
import math
from ...utils.gpu_utils import draw_hud_text, draw_lines_3d, make_bbox_wireframe, CIRCLE_VERTS_16
from ...utils.math_utils import compute_obb_orientation, collect_world_vertices, get_group_obb_orientation
from ...constants import BBOX_EDGES


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


class RARA_OT_VisualAlignBox(bpy.types.Operator):
    bl_idname = "rara.model_visual_align_box"
    bl_label = "开启可视化对齐"
    bl_description = "支持双向等距分布控件，支持TAB切换对齐轴向，完美预览"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.area.type == 'VIEW_3D' 
                and context.mode == 'OBJECT'
                and any(obj.type == 'MESH' for obj in context.selected_objects))

    def invoke(self, context, event):
        if len(context.selected_objects) < 2:
            self.report({'WARNING'}, "请至少选择两个对象进行对齐")
            return {'CANCELLED'}

        self.draw_handle_view = None
        self.hovered_handle = None
        self.group_center = None

        self.active_handle = None
        self.drag_start_mouse = Vector((0, 0))
        self.drag_start_min = Vector()
        self.drag_start_max = Vector()
        self.drag_start_matrices = {}
        self.objects_data = []

        self.modes = ['GLOBAL', 'LOCAL', 'CURSOR', 'MIN_BBOX']
        self.mode_names = ['全局轴', '活动对象轴', '游标轴', '最小包围框轴']
        self.current_mode_idx = 0
        self.orientation_matrix = Matrix.Identity(4)

        self.handles_info = {}
        self.handles_info['AXIS_LINE_X'] = ({1: 0, 2: 0}, 'ALIGN', (1.0, 0.3, 0.3, 0.8), 'LINE', 0)
        self.handles_info['AXIS_LINE_Y'] = ({0: 0, 2: 0}, 'ALIGN', (0.3, 1.0, 0.3, 0.8), 'LINE', 1)
        self.handles_info['AXIS_LINE_Z'] = ({0: 0, 1: 0}, 'ALIGN', (0.3, 0.3, 1.0, 0.8), 'LINE', 2)

        edge_color = (0.9, 0.9, 0.2, 0.9)
        for i in range(3):
            j = (i + 1) % 3
            k = 3 - i - j
            for vi in [-1, 1]:
                for vj in [-1, 1]:
                    v = Vector()
                    v[i], v[j] = vi, vj
                    self.handles_info[f'EDGE_{i}_{j}_{vi}_{vj}'] = ({i: vi, j: vj}, 'ALIGN', edge_color, 'EDGE_LINE', v, k)

        face_color = (0.2, 0.5, 1.0, 1.0)
        for i in range(3):
            self.handles_info[f'FACE_{i}_MAX'] = ({i: 1}, 'FACE', face_color, 'FACE', i, 1)
            self.handles_info[f'FACE_{i}_MIN'] = ({i: -1}, 'FACE', face_color, 'FACE', i, -1)

        colors = [(1.0, 0.2, 0.2, 0.9), (0.2, 1.0, 0.2, 0.9), (0.2, 0.5, 1.0, 0.9)]
        for i in range(3):
            v_min, v_max = Vector(), Vector()
            v_min[i], v_max[i] = -1.1, 1.1
            self.handles_info[f'AXIS_{i}_MIN'] = ({i: -1}, 'ALIGN', colors[i], 'CIRCLE', v_min)
            self.handles_info[f'AXIS_{i}_MAX'] = ({i: 1}, 'ALIGN', colors[i], 'CIRCLE', v_max)

        dist_color = (1.0, 0.6, 0.0, 1.0)
        self.handles_info['DIST_X_POS'] = ({0: 2}, 'DISTRIBUTE', dist_color, 'DIAMOND', Vector((1.2, 0, 0)))
        self.handles_info['DIST_X_NEG'] = ({0: 2}, 'DISTRIBUTE', dist_color, 'DIAMOND', Vector((-1.2, 0, 0)))
        self.handles_info['DIST_Y_POS'] = ({1: 2}, 'DISTRIBUTE', dist_color, 'DIAMOND', Vector((0, 1.2, 0)))
        self.handles_info['DIST_Y_NEG'] = ({1: 2}, 'DISTRIBUTE', dist_color, 'DIAMOND', Vector((0, -1.2, 0)))
        self.handles_info['DIST_Z_POS'] = ({2: 2}, 'DISTRIBUTE', dist_color, 'DIAMOND', Vector((0, 0, 1.2)))
        self.handles_info['DIST_Z_NEG'] = ({2: 2}, 'DISTRIBUTE', dist_color, 'DIAMOND', Vector((0, 0, -1.2)))

        self._update_bbox_data(context)

        args = (self, context)
        self.draw_handle_view = bpy.types.SpaceView3D.draw_handler_add(self._draw_callback_view, args, 'WINDOW', 'POST_VIEW')
        self.draw_handle_hud = bpy.types.SpaceView3D.draw_handler_add(self._draw_hud_callback, args, 'WINDOW', 'POST_PIXEL')

        context.window_manager.modal_handler_add(self)
        self._update_status(context)
        return {'RUNNING_MODAL'}

    def _update_status(self, context):
        mode_str = self.mode_names[self.current_mode_idx]
        msg = f"左键: 对齐/拖动 | 面控制: 偏移排布 | 菱形: 等距 | TAB: 轴向 [{mode_str}] | 右键/ESC: 退出"
        context.workspace.status_text_set(msg)
        self._hud_text = msg

    def _draw_hud_callback(self, op, context):
        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)

    def _update_bbox_data(self, context):
        self.object_names = [obj.name for obj in context.selected_objects if obj.type == 'MESH']
        if len(self.object_names) < 1:
            self.group_center = None
            return
        mode = self.modes[self.current_mode_idx]
        if mode == 'GLOBAL':
            self.orientation_matrix = Matrix.Identity(4)
        elif mode == 'LOCAL':
            active = context.active_object
            self.orientation_matrix = active.matrix_world.to_3x3().to_4x4() if active else Matrix.Identity(4)
        elif mode == 'CURSOR':
            self.orientation_matrix = context.scene.cursor.matrix.to_3x3().to_4x4()
        elif mode == 'MIN_BBOX':
            objs = [bpy.data.objects.get(n) for n in self.object_names if bpy.data.objects.get(n)]
            self.orientation_matrix = get_group_obb_orientation(objs)
        inv_matrix = self.orientation_matrix.inverted()
        min_v = Vector((float('inf'), float('inf'), float('inf')))
        max_v = Vector((float('-inf'), float('-inf'), float('-inf')))
        valid = False
        for name in self.object_names:
            obj = bpy.data.objects.get(name)
            if not obj:
                continue
            valid = True
            corners = [inv_matrix @ (obj.matrix_world @ Vector(c)) for c in obj.bound_box]
            for v in corners:
                min_v.x = min(min_v.x, v.x); min_v.y = min(min_v.y, v.y); min_v.z = min(min_v.z, v.z)
                max_v.x = max(max_v.x, v.x); max_v.y = max(max_v.y, v.y); max_v.z = max(max_v.z, v.z)
        if valid:
            self.group_min = min_v
            self.group_max = max_v
            self.group_center = (min_v + max_v) / 2.0
            self.group_extents = (max_v - min_v) / 2.0
            bbox_size = max_v - min_v
            self.objects_data.clear()
            for name in self.object_names:
                obj = bpy.data.objects.get(name)
                if not obj:
                    continue
                corners = [inv_matrix @ (obj.matrix_world @ Vector(c)) for c in obj.bound_box]
                cen = sum(corners, Vector()) / 8.0
                self.objects_data.append({
                    'name': name,
                    'rel': Vector((
                        (cen.x - min_v.x) / bbox_size.x if bbox_size.x > 0.0001 else 0.5,
                        (cen.y - min_v.y) / bbox_size.y if bbox_size.y > 0.0001 else 0.5,
                        (cen.z - min_v.z) / bbox_size.z if bbox_size.z > 0.0001 else 0.5,
                    ))
                })
        else:
            self.group_center = None

    def _get_handle_pos_world(self, handle_name):
        if not self.group_center:
            return Vector()
        shape_type = self.handles_info[handle_name][3]
        if shape_type == 'LINE':
            return Vector()
        if shape_type == 'FACE':
            face_axis = self.handles_info[handle_name][4]
            face_dir = self.handles_info[handle_name][5]
            c = self.group_center.copy()
            extent = self.group_max[face_axis] - self.group_min[face_axis]
            offset = 0.0
            if face_dir == 1:
                c[face_axis] = self.group_max[face_axis] + offset
            else:
                c[face_axis] = self.group_min[face_axis] - offset
            return self.orientation_matrix @ c
        pos_mult = self.handles_info[handle_name][4]
        padding = 1.0
        local_pos = self.group_center + Vector((
            self.group_extents.x * pos_mult.x * padding,
            self.group_extents.y * pos_mult.y * padding,
            self.group_extents.z * pos_mult.z * padding
        ))
        return self.orientation_matrix @ local_pos

    def _calculate_deltas(self, handle_name):
        deltas = {}
        if not self.group_center:
            return deltas
        axes_dict, action_type = self.handles_info[handle_name][:2]
        valid_objs = [bpy.data.objects.get(n) for n in self.object_names if bpy.data.objects.get(n)]
        if not valid_objs:
            return deltas
        inv_matrix = self.orientation_matrix.inverted()
        if action_type == 'ALIGN':
            for obj in valid_objs:
                corners = [inv_matrix @ (obj.matrix_world @ Vector(c)) for c in obj.bound_box]
                delta_local = Vector((0, 0, 0))
                for axis_idx, pos_type in axes_dict.items():
                    obj_min = min([v[axis_idx] for v in corners])
                    obj_max = max([v[axis_idx] for v in corners])
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
                return (min([v[axis_idx] for v in c]) + max([v[axis_idx] for v in c])) / 2.0

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

    def _handle_face_drag(self, mouse_pos, region, rv3d):
        axes_dict = self.handles_info[self.active_handle][0]
        face_axis = list(axes_dict.keys())[0]
        face_dir = axes_dict[face_axis]
        pos3d_start = self._get_handle_pos_world(self.active_handle)
        axis_vec_local = Vector((0, 0, 0))
        axis_vec_local[face_axis] = face_dir
        axis_vec_world = self.orientation_matrix @ axis_vec_local
        pos2d_start = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d_start)
        pos2d_end = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d_start + axis_vec_world)
        if not pos2d_start or not pos2d_end:
            return
        screen_axis_vec = pos2d_end - pos2d_start
        if screen_axis_vec.length < 0.001:
            return
        proj_dist = (mouse_pos - self.drag_start_mouse).dot(screen_axis_vec.normalized())
        delta_3d = proj_dist * 0.01
        start_size = self.drag_start_max[face_axis] - self.drag_start_min[face_axis]
        min_gap = max(0.0001, start_size * 0.0001)
        delta_3d = max(-(start_size - min_gap), delta_3d)
        self.group_min = self.drag_start_min.copy()
        self.group_max = self.drag_start_max.copy()
        if face_dir == 1:
            self.group_max[face_axis] = self.drag_start_max[face_axis] + delta_3d
        else:
            self.group_min[face_axis] = self.drag_start_min[face_axis] - delta_3d

    def _apply_face_drag(self):
        delta_min = self.group_min - self.drag_start_min
        delta_max = self.group_max - self.drag_start_max
        for item in self.objects_data:
            obj = bpy.data.objects.get(item['name'])
            if not obj:
                continue
            orig_mat = self.drag_start_matrices.get(obj.name)
            if not orig_mat:
                continue
            weight = item['rel']
            obj_delta_local = Vector((
                delta_min.x * (1 - weight.x) + delta_max.x * weight.x,
                delta_min.y * (1 - weight.y) + delta_max.y * weight.y,
                delta_min.z * (1 - weight.z) + delta_max.z * weight.z
            ))
            obj_delta_world = self.orientation_matrix @ obj_delta_local
            obj.matrix_world = Matrix.Translation(obj_delta_world) @ orig_mat

    def _compute_face_preview_deltas(self):
        deltas = {}
        delta_min = self.group_min - self.drag_start_min
        delta_max = self.group_max - self.drag_start_max
        for item in self.objects_data:
            weight = item['rel']
            delta_local = Vector((
                delta_min.x * (1 - weight.x) + delta_max.x * weight.x,
                delta_min.y * (1 - weight.y) + delta_max.y * weight.y,
                delta_min.z * (1 - weight.z) + delta_max.z * weight.z
            ))
            deltas[item['name']] = delta_local
        return deltas

    def _align_objects(self, handle_name):
        deltas = self._calculate_deltas(handle_name)
        for name, delta_local in deltas.items():
            obj = bpy.data.objects.get(name)
            if obj:
                delta_world = self.orientation_matrix.to_3x3() @ delta_local
                obj.matrix_world.translation += delta_world
        bpy.ops.ed.undo_push(message=f"可视化操作: {handle_name}")

    def modal(self, context, event):
        try:
            context.area.tag_redraw()
            if not self.active_handle:
                self._update_bbox_data(context)
            if event.type in {'RIGHTMOUSE', 'ESC'}:
                self._finish(context)
                return {'FINISHED'}
            if event.type == 'TAB' and event.value == 'PRESS':
                self.current_mode_idx = (self.current_mode_idx + 1) % len(self.modes)
                self._update_bbox_data(context)
                self._update_status(context)
                return {'RUNNING_MODAL'}
            if not self.group_center or len(self.object_names) < 2:
                return {'PASS_THROUGH'}
            mouse_pos = Vector((event.mouse_region_x, event.mouse_region_y))
            region, rv3d = context.region, context.region_data
            if event.type == 'MOUSEMOVE':
                if self.active_handle:
                    self._handle_face_drag(mouse_pos, region, rv3d)
                    return {'RUNNING_MODAL'}
                self.hovered_handle = None
                types = [('DIAMOND', 15.0), ('CIRCLE', 15.0), ('FACE', 25.0)]
                for check_type, threshold in types:
                    best_name = None
                    best_dist = threshold
                    for name, info in self.handles_info.items():
                        shape_type = info[3]
                        if check_type == 'DIAMOND' and info[1] != 'DISTRIBUTE':
                            continue
                        if check_type == 'CIRCLE' and (shape_type != 'CIRCLE' or info[1] != 'ALIGN'):
                            continue
                        if check_type not in ('DIAMOND', 'CIRCLE') and shape_type != check_type:
                            continue
                        if shape_type in ('LINE', 'EDGE_LINE'):
                            continue
                        pos3d = self._get_handle_pos_world(name)
                        pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d)
                        if pos2d:
                            dist = (pos2d - mouse_pos).length
                            if dist < best_dist:
                                best_dist = dist
                                best_name = name
                    if best_name:
                        self.hovered_handle = best_name
                        break
                if self.hovered_handle is None:
                    best_line = None
                    min_line_dist = 10.0
                    for name, info in self.handles_info.items():
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
                    if best_line is None:
                        for name, info in self.handles_info.items():
                            shape_type = info[3]
                            if shape_type != 'EDGE_LINE':
                                continue
                            pos3d = self._get_handle_pos_world(name)
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
                    self.hovered_handle = best_line
                return {'PASS_THROUGH'}
            elif event.type == 'LEFTMOUSE':
                if event.value == 'PRESS':
                    if self.hovered_handle and self.handles_info[self.hovered_handle][1] == 'FACE':
                        self.active_handle = self.hovered_handle
                        self.drag_start_mouse = mouse_pos
                        self.drag_start_min = self.group_min.copy()
                        self.drag_start_max = self.group_max.copy()
                        self.drag_start_matrices.clear()
                        for name in self.object_names:
                            obj = bpy.data.objects.get(name)
                            if obj:
                                self.drag_start_matrices[obj.name] = obj.matrix_world.copy()
                        return {'RUNNING_MODAL'}
                    elif self.hovered_handle:
                        self._align_objects(self.hovered_handle)
                        self._update_bbox_data(context)
                        return {'RUNNING_MODAL'}
                elif event.value == 'RELEASE':
                    if self.active_handle:
                        self._apply_face_drag()
                        self.active_handle = None
                        bpy.ops.ed.undo_push(message="可视化排布")
                        self._update_bbox_data(context)
                        return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}
        except Exception as e:
            self._finish(context)
            self.report({'ERROR'}, f"可视化对齐出错: {str(e)}")
            return {'CANCELLED'}

    def _draw_callback_view(self, op, context):
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

        for name, info in self.handles_info.items():
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

        for name, info in self.handles_info.items():
            if info[3] == 'LINE':
                continue
            axes_dict, action_type, base_color, shape_type = info[:4]
            pos3d = self._get_handle_pos_world(name)
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

            if shape_type == 'FACE':
                face_axis = info[4]
                ax1 = (face_axis + 1) % 3
                ax2 = (face_axis + 2) % 3
                cam_dist = (pos3d - cam_pos).length if rv3d.is_perspective else rv3d.view_distance
                half_size = cam_dist * 0.006
                if is_hovered:
                    half_size *= 1.4
                dir1 = self.orientation_matrix.col[ax1].to_3d().normalized()
                dir2 = self.orientation_matrix.col[ax2].to_3d().normalized()
                sq = [
                    pos3d - dir1 * half_size - dir2 * half_size,
                    pos3d + dir1 * half_size - dir2 * half_size,
                    pos3d + dir1 * half_size + dir2 * half_size,
                    pos3d - dir1 * half_size + dir2 * half_size,
                    pos3d - dir1 * half_size - dir2 * half_size,
                ]
                is_dragging = (name == self.active_handle)
                gpu.state.line_width_set(5.0 if is_dragging else 3.0)
                out_color = (1.0, 1.0, 1.0, 1.0) if is_dragging else (0.2, 0.5, 1.0, 1.0)
                shader.uniform_float("color", out_color)
                batch_sq_outline = batch_for_shader(shader, 'LINE_STRIP', {"pos": sq})
                batch_sq_outline.draw(shader)
                gpu.state.line_width_set(1.0)
                fill_color = (1.0, 1.0, 1.0, 0.6) if is_dragging else (0.2, 0.5, 1.0, 0.35) if is_hovered else (0.2, 0.5, 1.0, 0.18)
                shader.uniform_float("color", fill_color)
                batch_sq_fill = batch_for_shader(shader, 'TRI_FAN', {"pos": sq[:4]})
                batch_sq_fill.draw(shader)
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
        if self.active_handle and self.handles_info[self.active_handle][1] == 'FACE':
            preview_deltas = self._compute_face_preview_deltas()
        elif self.hovered_handle:
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

    def _finish(self, context):
        if self.draw_handle_view:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_view, 'WINDOW')
        if self.draw_handle_hud:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_hud, 'WINDOW')
        context.workspace.status_text_set(None)


classes = (RARA_OT_VisualAlignBox,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)