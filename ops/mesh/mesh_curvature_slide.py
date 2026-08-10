# 保持曲率滑移
# Uses shared edge_topology for robust edge detection

import bpy
import bmesh
import mathutils
from mathutils import Vector
import math
import gpu
from gpu_extras.batch import batch_for_shader
from ...utils.gpu_utils import draw_hud_text
from ...utils.edge_topology import div_set, get_endpoints, find_rail_verts


def _get_circle_from_3_points(p1, p2, p3):
    a = p1 - p3
    b = p2 - p3
    cross_ab = a.cross(b)
    if cross_ab.length_squared < 1e-6:
        return None, None, None
    a_sq = a.length_squared
    b_sq = b.length_squared
    cross_sq = cross_ab.length_squared
    term1 = a_sq * b - b_sq * a
    center = p3 + term1.cross(cross_ab) / (2.0 * cross_sq)
    radius = (p1 - center).length
    normal = cross_ab.normalized()
    return center, radius, normal


def _get_next_vert(curr_v, prev_v):
    best_v = None
    best_dot = -1.0
    vec_in = (curr_v.co - prev_v.co).normalized()
    for e in curr_v.link_edges:
        other_v = e.other_vert(curr_v)
        if other_v == prev_v:
            continue
        vec_out = (other_v.co - curr_v.co).normalized()
        dot = vec_in.dot(vec_out)
        if dot > best_dot:
            best_dot = dot
            best_v = other_v
    return best_v


def _catmull_rom(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2.0 * p1) +
        (-p0 + p2) * t +
        (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2 +
        (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
    )


class RARA_OT_MeshCurvatureSlide(bpy.types.Operator):
    bl_idname = "rara.model_mesh_curvature_slide"
    bl_label = "保持曲率滑动"
    bl_description = "圆弧/样条/线性三模式曲率滑动（复用滑移边线拓扑引擎，支持任意拓扑结构）"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def invoke(self, context, event):
        obj = context.edit_object
        self.bm = bmesh.from_edit_mesh(obj.data)
        self.matrix_world = obj.matrix_world.copy()
        self.mode = 'CIRCLE'

        selected_edges = [e for e in self.bm.edges if e.select and len(e.link_faces) > 0]
        if not selected_edges:
            self.report({'WARNING'}, "请先选中边线（非边界边）")
            return {'CANCELLED'}

        # ---- NEW: use shared topology engine (same as slide edge) ----
        components = div_set(selected_edges)
        self.slide_data = []

        for comp in components:
            rail_data = find_rail_verts(self.bm, comp)
            for e in comp:
                for v in e.verts:
                    if v in rail_data:
                        rd = rail_data[v]
                        rail_edges = rd['rail_edges']
                        rail_verts = rd['rail_verts']

                        linked_selected = [e2 for e2 in v.link_edges if e2 in comp]
                        if not linked_selected:
                            continue
                        ref_edge = linked_selected[0]

                        # Build p0-p1-p2-p3-p4: p2 is current vert, p1/p3 are rail neighbors
                        p2 = v.co.copy()
                        if len(rail_verts) >= 2:
                            p1, p3 = rail_verts[0].co.copy(), rail_verts[1].co.copy()
                        elif len(rail_verts) == 1:
                            p1 = rail_verts[0].co.copy()
                            p3 = p2 + (p2 - p1)
                        else:
                            ref_vec = (ref_edge.other_vert(v).co - v.co).normalized()
                            # Cross with view direction for a perpendicular
                            rv3d = context.space_data.region_3d
                            view_dir = rv3d.view_matrix.inverted().to_3x3() @ Vector((0, 0, -1))
                            perp = ref_vec.cross(view_dir)
                            if perp.length < 1e-6:
                                perp = ref_vec.cross(Vector((0, 1, 0)))
                            perp.normalize()
                            p1 = p2 + perp
                            p3 = p2 - perp

                        v1 = _get_next_vert(rail_verts[0], v) if len(rail_verts) >= 1 else None
                        v2 = _get_next_vert(rail_verts[1], v) if len(rail_verts) >= 2 else None
                        p0 = v1.co.copy() if v1 else p1 + (p1 - p2)
                        p4 = v2.co.copy() if v2 else p3 + (p3 - p2)

                        center, radius, normal = _get_circle_from_3_points(p1, p2, p3)
                        if center is not None:
                            init_vec = p2 - center
                            vec_p1 = p1 - center
                            vec_p3 = p3 - center
                            angle_p1 = math.atan2(init_vec.cross(vec_p1).dot(normal), init_vec.dot(vec_p1))
                            angle_p3 = math.atan2(init_vec.cross(vec_p3).dot(normal), init_vec.dot(vec_p3))
                            limit_min = min(angle_p1, angle_p3)
                            limit_max = max(angle_p1, angle_p3)
                            self.slide_data.append({
                                'vert': v, 'init_co': p2.copy(),
                                'center': center, 'radius': radius, 'normal': normal, 'init_vec': init_vec,
                                'p0': p0, 'p1': p1, 'p2': p2, 'p3': p3, 'p4': p4,
                                'limit_min': limit_min, 'limit_max': limit_max
                            })

        if not self.slide_data:
            self.report({'WARNING'}, "无法找到合适的腰线结构——请确保每条选中边两侧都有相邻面")
            return {'CANCELLED'}

        # ---- Direction consistency: ensure all vertices slide in the same direction ----
        vert_to_idx = {d['vert']: i for i, d in enumerate(self.slide_data)}
        comp_edge_set = set(selected_edges)
        processed = set()
        for data in self.slide_data:
            v = data['vert']
            if v in processed:
                continue
            processed.add(v)
            queue = [v]
            while queue:
                cur_v = queue.pop(0)
                cur_idx = vert_to_idx[cur_v]
                cur_dir = (self.slide_data[cur_idx]['p1'] - self.slide_data[cur_idx]['p3']).normalized()
                for e in cur_v.link_edges:
                    if e not in comp_edge_set:
                        continue
                    nb = e.other_vert(cur_v)
                    if nb not in vert_to_idx or nb in processed:
                        continue
                    nb_idx = vert_to_idx[nb]
                    nb_dir = (self.slide_data[nb_idx]['p1'] - self.slide_data[nb_idx]['p3']).normalized()
                    if cur_dir.dot(nb_dir) < 0:
                        nd = self.slide_data[nb_idx]
                        nd['p0'], nd['p4'] = nd['p4'], nd['p0']
                        nd['p1'], nd['p3'] = nd['p3'], nd['p1']
                        new_center, new_radius, new_normal = _get_circle_from_3_points(nd['p1'], nd['p2'], nd['p3'])
                        if new_center is not None:
                            nd['center'] = new_center
                            nd['radius'] = new_radius
                            nd['normal'] = new_normal
                        nd['init_vec'] = nd['p2'] - nd['center']
                        vec_p1 = nd['p1'] - nd['center']
                        vec_p3 = nd['p3'] - nd['center']
                        angle_p1 = math.atan2(nd['init_vec'].cross(vec_p1).dot(nd['normal']), nd['init_vec'].dot(vec_p1))
                        angle_p3 = math.atan2(nd['init_vec'].cross(vec_p3).dot(nd['normal']), nd['init_vec'].dot(vec_p3))
                        nd['limit_min'] = min(angle_p1, angle_p3)
                        nd['limit_max'] = max(angle_p1, angle_p3)
                    processed.add(nb)
                    queue.append(nb)

        # ---- GPU batches ----
        self.batches_circle = []
        self.batches_spline = []
        self.batches_linear = []
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        for data in self.slide_data:
            center = data['center']
            radius = data['radius']
            normal = data['normal']
            tangent = data['init_vec'].normalized()
            bitangent = normal.cross(tangent).normalized()
            limit_min = data['limit_min']
            limit_max = data['limit_max']
            points_circle = []
            for i in range(33):
                angle = limit_min + (limit_max - limit_min) * (i / 32)
                p_local = center + (tangent * math.cos(angle) + bitangent * math.sin(angle)) * radius
                points_circle.append(self.matrix_world @ p_local)
            self.batches_circle.append(batch_for_shader(shader, 'LINE_STRIP', {"pos": points_circle}))
            points_spline = []
            for i in range(21):
                t = 1.0 - (i / 20)
                p_local = _catmull_rom(data['p3'], data['p2'], data['p1'], data['p0'], t)
                points_spline.append(self.matrix_world @ p_local)
            for i in range(1, 21):
                t = i / 20
                p_local = _catmull_rom(data['p1'], data['p2'], data['p3'], data['p4'], t)
                points_spline.append(self.matrix_world @ p_local)
            self.batches_spline.append(batch_for_shader(shader, 'LINE_STRIP', {"pos": points_spline}))
            # Linear batch: straight line p1 → p2 → p3
            points_linear = [
                self.matrix_world @ data['p1'],
                self.matrix_world @ data['p2'],
                self.matrix_world @ data['p3'],
            ]
            self.batches_linear.append(batch_for_shader(shader, 'LINE_STRIP', {"pos": points_linear}))

        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback, (context,), 'WINDOW', 'POST_VIEW'
        )
        self._hud_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_hud_callback, (context,), 'WINDOW', 'POST_PIXEL'
        )
        self.mouse_start_x = event.mouse_x
        self.current_mouse_x = event.mouse_x
        context.window_manager.modal_handler_add(self)
        self._update_header(context)
        return {'RUNNING_MODAL'}

    def _update_header(self, context):
        mode_names = {'CIRCLE': "[C4D]圆弧", 'SPLINE': "[真曲率]样条", 'LINEAR': "[直线]线性"}
        mode_str = mode_names[self.mode]
        msg = "滑动鼠标 | TAB: 切模式(%s) | 左键: 确认 | 右键: 取消" % mode_str
        context.workspace.status_text_set(msg)
        self._hud_text = msg

    def _draw_hud_callback(self, context):
        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)

    def _draw_callback(self, context):
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(2.0)
        gpu.state.depth_test_set('LESS_EQUAL')
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        if self.mode == 'CIRCLE':
            shader.uniform_float("color", (0.0, 1.0, 1.0, 0.5))
            for batch in self.batches_circle:
                batch.draw(shader)
        elif self.mode == 'SPLINE':
            shader.uniform_float("color", (1.0, 0.6, 0.0, 0.8))
            for batch in self.batches_spline:
                batch.draw(shader)
        else:
            shader.uniform_float("color", (0.6, 1.0, 0.2, 0.8))
            for batch in self.batches_linear:
                batch.draw(shader)
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')
        gpu.state.line_width_set(1.0)

    def _remove_draw_handler(self, context):
        if hasattr(self, "_handle"):
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            del self._handle
        if hasattr(self, "_hud_handle"):
            bpy.types.SpaceView3D.draw_handler_remove(self._hud_handle, 'WINDOW')
            del self._hud_handle
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    def _update_positions(self, context):
        delta_x = self.current_mouse_x - self.mouse_start_x
        t = delta_x * 0.005
        t = max(-1.0, min(1.0, t))
        for data in self.slide_data:
            v = data['vert']
            if self.mode == 'CIRCLE':
                if t >= 0:
                    angle = t * data['limit_max']
                else:
                    angle = -t * data['limit_min']
                rot_quat = mathutils.Quaternion(data['normal'], angle)
                new_vec = data['init_vec'].copy()
                new_vec.rotate(rot_quat)
                v.co = data['center'] + new_vec
            elif self.mode == 'SPLINE':
                if t >= 0:
                    v.co = _catmull_rom(data['p3'], data['p2'], data['p1'], data['p0'], t)
                else:
                    v.co = _catmull_rom(data['p1'], data['p2'], data['p3'], data['p4'], -t)
            elif self.mode == 'LINEAR':
                if t >= 0:
                    direction = data['p3'] - data['p2']
                else:
                    direction = data['p1'] - data['p2']
                v.co = data['p2'] + direction * abs(t)
        bmesh.update_edit_mesh(context.edit_object.data)

    def modal(self, context, event):
        try:
            if event.type == 'TAB' and event.value == 'PRESS':
                if self.mode == 'CIRCLE':
                    self.mode = 'SPLINE'
                elif self.mode == 'SPLINE':
                    self.mode = 'LINEAR'
                else:
                    self.mode = 'CIRCLE'
                self._update_header(context)
                self._update_positions(context)
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type == 'MOUSEMOVE':
                self.current_mouse_x = event.mouse_x
                self._update_positions(context)
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            elif event.type == 'LEFTMOUSE':
                self._remove_draw_handler(context)
                return {'FINISHED'}

            elif event.type in {'RIGHTMOUSE', 'ESC'}:
                for data in self.slide_data:
                    data['vert'].co = data['init_co']
                bmesh.update_edit_mesh(context.edit_object.data)
                self._remove_draw_handler(context)
                return {'CANCELLED'}
            return {'RUNNING_MODAL'}
        except Exception as e:
            self._remove_draw_handler(context)
            print("Error:", e)
            return {'CANCELLED'}


classes = (RARA_OT_MeshCurvatureSlide,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
