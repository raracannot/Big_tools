# 边界框体排布

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector, Quaternion
from bpy_extras import view3d_utils
import math
from ...utils.gpu_utils import draw_hud_text, draw_lines_3d, make_bbox_wireframe, CIRCLE_VERTS_FAN
from ...utils.math_utils import compute_obb_orientation, collect_world_vertices, get_group_obb_orientation
from ...constants import CIRCLE_HANDLE_RADIUS


ROT_RING_SEGMENTS = 32
ROT_RING_VERTS = [
    (math.cos(2 * math.pi * i / ROT_RING_SEGMENTS), math.sin(2 * math.pi * i / ROT_RING_SEGMENTS), 0.0)
    for i in range(ROT_RING_SEGMENTS + 1)
]


class RARA_OT_VisualLayoutModal(bpy.types.Operator):
    bl_idname = "rara.model_visual_layout_modal"
    bl_label = "开启可视化排布"
    bl_description = "支持多轴向切换、左键穿透与基于边界框的比例旋转"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.area.type == 'VIEW_3D' 
                and context.mode == 'OBJECT'
                and any(obj.type == 'MESH' for obj in context.selected_objects))

    def invoke(self, context, event):
        self.draw_handle_view = None
        self.draw_handle_px = None
        self.objects_data = []
        self.min_v = Vector()
        self.max_v = Vector()
        self.orientation_matrix = Matrix.Identity(4)
        self.center_world = Vector()

        self.modes = ['GLOBAL', 'LOCAL', 'CURSOR', 'MIN_BBOX']
        self.mode_names = ['全局轴', '活动对象轴', '游标轴', '最小包围框轴']
        self.current_mode_idx = 0

        self.rot_pivot_modes = ['BBOX_CENTER', 'INDIVIDUAL_ORIGINS']
        self.rot_pivot_names = ['边界框总轴心', '各对象轴心']
        self.current_rot_pivot_idx = 1

        self.trans_interp_modes = ['BBOX', 'END_OBJECTS']
        self.trans_interp_names = ['边界框比例', '端头对象比例']
        self.current_trans_interp_idx = 1

        self.hovered_handle = None
        self.hovered_type = None
        self.active_handle = None
        self.active_type = None

        self.drag_start_mouse = Vector((0, 0))
        self.drag_start_min = Vector()
        self.drag_start_max = Vector()
        self.drag_start_matrices = {}
        self.last_selected_names = set()

        self.handles_info = {
            'X_MAX': (0, 1, (1.0, 0.2, 0.2, 0.8)), 'X_MIN': (0, -1, (1.0, 0.2, 0.2, 0.8)),
            'Y_MAX': (1, 1, (0.2, 1.0, 0.2, 0.8)), 'Y_MIN': (1, -1, (0.2, 1.0, 0.2, 0.8)),
            'Z_MAX': (2, 1, (0.2, 0.2, 1.0, 0.8)), 'Z_MIN': (2, -1, (0.2, 0.2, 1.0, 0.8)),
        }

        if not self._update_layout_data(context):
            return {'CANCELLED'}

        args = (self, context)
        self.draw_handle_view = bpy.types.SpaceView3D.draw_handler_add(self._draw_callback_view, args, 'WINDOW', 'POST_VIEW')
        self.draw_handle_px = bpy.types.SpaceView3D.draw_handler_add(self._draw_callback_px, args, 'WINDOW', 'POST_PIXEL')

        context.window_manager.modal_handler_add(self)
        self._update_status(context)
        return {'RUNNING_MODAL'}

    def _update_status(self, context):
        mode_str = self.mode_names[self.current_mode_idx]
        rot_pivot_str = self.rot_pivot_names[self.current_rot_pivot_idx]
        trans_interp_str = self.trans_interp_names[self.current_trans_interp_idx]
        msg = f"左键拖动 | TAB: 轴向 [{mode_str}] | C: 旋转轴心 [{rot_pivot_str}] | V: 平移比例 [{trans_interp_str}] | 右键/ESC 退出"
        context.workspace.status_text_set(msg)
        self._hud_text = msg

    def modal(self, context, event):
        try:
            context.area.tag_redraw()
            current_sel = set(obj.name for obj in context.selected_objects if obj.type == 'MESH')
            if current_sel != self.last_selected_names:
                self._update_layout_data(context)
            if event.type in {'RIGHTMOUSE', 'ESC', 'RET'}:
                self._finish(context)
                return {'FINISHED'}
            if event.type == 'TAB' and event.value == 'PRESS':
                self.current_mode_idx = (self.current_mode_idx + 1) % len(self.modes)
                self._update_layout_data(context)
                self._update_status(context)
                return {'RUNNING_MODAL'}
            if event.type == 'C' and event.value == 'PRESS':
                self.current_rot_pivot_idx = (self.current_rot_pivot_idx + 1) % len(self.rot_pivot_modes)
                self._update_status(context)
                return {'RUNNING_MODAL'}
            if event.type == 'V' and event.value == 'PRESS':
                self.current_trans_interp_idx = (self.current_trans_interp_idx + 1) % len(self.trans_interp_modes)
                self._update_status(context)
                return {'RUNNING_MODAL'}
            mouse_pos = Vector((event.mouse_region_x, event.mouse_region_y))
            region, rv3d = context.region, context.region_data
            if event.type == 'MOUSEMOVE':
                if self.active_handle:
                    self._handle_drag(mouse_pos, region, rv3d)
                    return {'RUNNING_MODAL'}
                else:
                    self.hovered_handle, self.hovered_type = self._get_hovered_handle(mouse_pos, region, rv3d)
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
                elif event.value == 'RELEASE':
                    if self.active_handle:
                        self.active_handle = None
                        self.active_type = None
                        self._update_layout_data(context)
                        return {'RUNNING_MODAL'}
                return {'PASS_THROUGH'}
            if event.type not in {'MOUSEMOVE', 'LEFTMOUSE', 'RIGHTMOUSE', 'ESC', 'RET', 'TAB', 'C', 'V'}:
                return {'PASS_THROUGH'}
            return {'RUNNING_MODAL'}
        except Exception as e:
            self._finish(context)
            self.report({'ERROR'}, f"可视化排布出错: {str(e)}")
            return {'CANCELLED'}

    def _handle_drag(self, mouse_pos, region, rv3d):
        axis_idx, direction, _ = self.handles_info[self.active_handle]
        pos3d_start = self._get_handle_pos_world(self.active_handle)
        axis_vec_local = Vector((0, 0, 0))
        axis_vec_local[axis_idx] = direction
        axis_vec_world = self.orientation_matrix @ axis_vec_local
        if self.active_type == 'TRANS':
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
                self.max_v[axis_idx] = max(self.min_v[axis_idx] + 0.01, self.drag_start_max[axis_idx] + delta_3d)
            else:
                self.min_v[axis_idx] = min(self.max_v[axis_idx] - 0.01, self.drag_start_min[axis_idx] - delta_3d)
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
                    delta_min.z * (1 - weight.z) + delta_max.z * weight.z
                ))
                obj_delta_world = self.orientation_matrix @ obj_delta_local
                obj.matrix_world = Matrix.Translation(obj_delta_world) @ orig_mat
        elif self.active_type == 'ROT':
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

    def _update_layout_data(self, context):
        objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
        self.last_selected_names = set(obj.name for obj in objects)
        if len(objects) < 2:
            self.objects_data.clear()
            return False
        mode = self.modes[self.current_mode_idx]
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
                min_v.x = min(min_v.x, v.x); min_v.y = min(min_v.y, v.y); min_v.z = min(min_v.z, v.z)
                max_v.x = max(max_v.x, v.x); max_v.y = max(max_v.y, v.y); max_v.z = max(max_v.z, v.z)
            center_local = sum(corners, Vector()) / 8.0
            obj_centers.append((obj.name, center_local))
        self.min_v = min_v
        self.max_v = max_v
        self.center_world = self.orientation_matrix @ ((min_v + max_v) / 2.0)
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
                'end_rel': Vector((e_rel_x, e_rel_y, e_rel_z))
            })
        return True

    def _get_handle_pos_world(self, handle_name):
        c = (self.min_v + self.max_v) / 2.0
        local_pos = c.copy()
        if handle_name == 'X_MAX': local_pos.x = self.max_v.x
        elif handle_name == 'X_MIN': local_pos.x = self.min_v.x
        elif handle_name == 'Y_MAX': local_pos.y = self.max_v.y
        elif handle_name == 'Y_MIN': local_pos.y = self.min_v.y
        elif handle_name == 'Z_MAX': local_pos.z = self.max_v.z
        elif handle_name == 'Z_MIN': local_pos.z = self.min_v.z
        return self.orientation_matrix @ local_pos

    def _get_hovered_handle(self, mouse_pos, region, rv3d):
        if not self.objects_data:
            return None, None
        closest_handle = None
        closest_type = None
        min_dist = 40.0
        for name in self.handles_info.keys():
            pos3d = self._get_handle_pos_world(name)
            pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d)
            if pos2d:
                dist = (pos2d - mouse_pos).length
                if dist < min_dist:
                    min_dist = dist
                    closest_handle = name
                    closest_type = 'TRANS' if dist < CIRCLE_HANDLE_RADIUS * 1.5 else 'ROT'
        return closest_handle, closest_type

    def _draw_callback_view(self, op, context):
        if not self.objects_data:
            return

        lines = make_bbox_wireframe(self.min_v, self.max_v, self.orientation_matrix)
        draw_lines_3d(lines, (1.0, 0.8, 0.2, 0.5), 2.0, 'LESS_EQUAL')

        rv3d = context.region_data
        cam_pos = rv3d.view_matrix.inverted().translation
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader.bind()
        for name, info in self.handles_info.items():
            axis_idx, _, color = info
            pos3d = self._get_handle_pos_world(name)
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
            is_active_rot = (name == self.hovered_handle and self.hovered_type == 'ROT') or \
                            (name == self.active_handle and self.active_type == 'ROT')
            draw_color = (1.0, 1.0, 1.0, 1.0) if is_active_rot else color
            gpu.state.line_width_set(8.0 if is_active_rot else 6.0)
            batch_ring = batch_for_shader(shader, 'LINES', {"pos": ring_lines})
            shader.uniform_float("color", draw_color)
            batch_ring.draw(shader)
        gpu.state.blend_set('NONE')
        gpu.state.line_width_set(1.0)
        gpu.state.depth_test_set('NONE')

    def _draw_callback_px(self, op, context):
        if not self.objects_data:
            return
        region, rv3d = context.region, context.region_data
        gpu.state.blend_set('ALPHA')
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        shader.bind()
        for name, info in self.handles_info.items():
            pos3d = self._get_handle_pos_world(name)
            pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d)
            if not pos2d:
                continue
            is_active_trans = (name == self.hovered_handle and self.hovered_type == 'TRANS') or \
                              (name == self.active_handle and self.active_type == 'TRANS')
            color = (1.0, 1.0, 1.0, 1.0) if is_active_trans else info[2]
            scale = 1.5 if is_active_trans else 1.0
            s = CIRCLE_HANDLE_RADIUS * scale
            verts = [(pos2d.x + v[0] * s, pos2d.y + v[1] * s) for v in CIRCLE_VERTS_FAN]
            batch = batch_for_shader(shader, 'TRI_FAN', {"pos": verts})
            shader.uniform_float("color", color)
            batch.draw(shader)
        gpu.state.blend_set('NONE')

        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)

    def _finish(self, context):
        if self.draw_handle_view:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_view, 'WINDOW')
        if self.draw_handle_px:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_px, 'WINDOW')
        context.workspace.status_text_set(None)


classes = (RARA_OT_VisualLayoutModal,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)