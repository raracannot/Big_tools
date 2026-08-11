# 保持曲率滑移
# 基于链遍历的轨道检测，复用 slide_edge 的拓扑引擎

import bpy
import bmesh
import mathutils
from mathutils import Vector
import math
import gpu
from gpu_extras.batch import batch_for_shader
from ...utils.gpu_utils import draw_hud_text
from ...utils.edge_topology import div_set, get_endpoints, build_edge_chain


def _get_circle_from_3_points(p1, p2, p3):
    a = p1 - p3
    b = p2 - p3
    cross_ab = a.cross(b)
    scale2 = max(a.length_squared, b.length_squared, 1e-10)
    if cross_ab.length_squared < scale2 * 1e-12:
        return None, None, None
    a_sq = a.length_squared
    b_sq = b.length_squared
    cross_sq = cross_ab.length_squared
    term1 = a_sq * b - b_sq * a
    center = p3 + term1.cross(cross_ab) / (2.0 * cross_sq)
    radius = (p1 - center).length
    normal = cross_ab.normalized()
    return center, radius, normal


def _catmull_rom(p0, p1, p2, p3, t):
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2.0 * p1) +
        (-p0 + p2) * t +
        (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2 +
        (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
    )


def _perp_rail_verts(chain_verts, idx, edge_set):
    """Fallback: per-vertex perp scoring for non-manifold topology"""
    v = chain_verts[idx]
    unselected = [e for e in v.link_edges if e not in edge_set]
    if not unselected:
        return None, None
    if idx > 0 and idx < len(chain_verts) - 1:
        ref_dir = (chain_verts[idx + 1].co - chain_verts[idx - 1].co).normalized()
    elif idx > 0:
        ref_dir = (v.co - chain_verts[idx - 1].co).normalized()
    else:
        ref_dir = (chain_verts[idx + 1].co - v.co).normalized()
    scored = sorted(
        ((abs((e.other_vert(v).co - v.co).normalized().dot(ref_dir)), e.other_vert(v))
         for e in unselected), key=lambda x: x[0])
    if len(scored) >= 2:
        return scored[0][1], scored[1][1]
    elif len(scored) == 1:
        return scored[0][1], None
    return None, None


def _face_rail_verts(bm, chain_verts, all_selected_edges):
    """Face-shared adjacency matching: 通过共面关系确保全链 p1/p3 同侧。
    返回 [(BMVert, BMVert), ...] 每个顶点对应的 (p1_vert, p3_vert) 或 (None, None)。"""
    n = len(chain_verts)
    results = [(None, None)] * n
    edge_set = set(all_selected_edges)

    # ── 第一步: 找链上相邻顶点间的选中边 ──
    chain_edges = []
    for i in range(n - 1):
        e = bm.edges.get((chain_verts[i], chain_verts[i + 1]))
        if e and e in edge_set:
            chain_edges.append(e)
        else:
            chain_edges.append(None)

    # ── 第二步: 为第一个顶点初始化两侧面 ──
    v0 = chain_verts[0]
    unselected = [e for e in v0.link_edges if e not in edge_set]
    if len(unselected) < 2:
        # 退化为 perp 排序
        return [_perp_rail_verts(chain_verts, i, edge_set) for i in range(n)]

    # 用第一个 chain edge 的两个面定义两侧
    e0 = chain_edges[0]
    if not e0 or len(e0.link_faces) < 2:
        return [_perp_rail_verts(chain_verts, i, edge_set) for i in range(n)]

    face_side_0 = e0.link_faces[0]
    face_side_1 = e0.link_faces[1]

    # 映射：面 → (上一顶点在此面上的 p 侧号)
    side_of_face = {face_side_0: 0, face_side_1: 1}
    side_name = ['p1', 'p3']

    for i in range(n):
        v = chain_verts[i]
        edges_by_face = {0: None, 1: None}

        for e in v.link_edges:
            if e in edge_set:
                continue
            for f in e.link_faces:
                if f in side_of_face:
                    s = side_of_face[f]
                    if edges_by_face[s] is None:
                        edges_by_face[s] = e

        p1_edge = edges_by_face[0]
        p3_edge = edges_by_face[1]

        if p1_edge is None and p3_edge is None:
            results[i] = (None, None)
            continue

        p1_vert = p1_edge.other_vert(v) if p1_edge else None
        p3_vert = p3_edge.other_vert(v) if p3_edge else None

        if p1_vert is None and p3_edge is not None:
            # 只有一侧有 rail，镜像补另一侧
            p3_vert = p3_edge.other_vert(v)
            p1_vert = None
            results[i] = (p3_vert, None)
        elif p3_vert is None and p1_edge is not None:
            results[i] = (p1_vert, None)
        else:
            results[i] = (p1_vert, p3_vert)

        # 更新下一顶点的面映射（通过选中边传递两侧面）
        if i < n - 1:
            ce = chain_edges[i]
            if ce and len(ce.link_faces) == 2:
                f0, f1 = ce.link_faces[0], ce.link_faces[1]
                # 确定当前侧对应哪个面
                if f0 in side_of_face:
                    # f0 保持原侧, f1 对应另一侧
                    new_map = {f0: side_of_face[f0]}
                    new_map[f1] = 1 - side_of_face[f0]
                elif f1 in side_of_face:
                    new_map = {f1: side_of_face[f1]}
                    new_map[f0] = 1 - side_of_face[f1]
                else:
                    new_map = {f0: 0, f1: 1}
                side_of_face = new_map
            else:
                # 边界边：回退到 perp
                remaining = list(range(i + 1, n))
                perp_results = [_perp_rail_verts(chain_verts, j, edge_set) for j in remaining]
                for k, j in enumerate(remaining):
                    results[j] = perp_results[k]
                break

    return results


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


def _build_face_normal_map(bm, component_edges):
    """Build dict: (v1_idx, v2_idx) → [face_normals] for component edges."""
    result = {}
    for e in component_edges:
        v1, v2 = e.verts
        key = (min(v1.index, v2.index), max(v1.index, v2.index))
        norms = [f.normal.copy() for f in e.link_faces]
        if norms:
            result[key] = norms
    return result


class RARA_OT_MeshCurvatureSlide(bpy.types.Operator):
    bl_idname = "rara.model_mesh_curvature_slide"
    bl_label = "保持曲率滑动"
    bl_description = "圆弧/样条/线性三模式曲率滑动（链遍历拓扑引擎，稳定识别任意尺度）"
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

        components = div_set(selected_edges)
        self.slide_data = []
        all_selected = set(selected_edges)

        for comp in components:
            endpoints = get_endpoints(comp)
            if len(endpoints) == 0:
                # Closed loop: start from an arbitrary edge
                chain_data = build_edge_chain(comp, comp[0].verts[0])
                # Remove the trailing (vert, None) entry for closed loops
                chain_verts = [item[0] for item in chain_data if item[1] is not None]
                if len(chain_verts) > 2 and chain_verts[0] == chain_verts[-1]:
                    chain_verts.pop()
            elif len(endpoints) == 1:
                chain_data = build_edge_chain(comp, endpoints[0])
                chain_verts = [item[0] for item in chain_data]
            else:
                # Build from both ends and merge
                chain1 = build_edge_chain(comp, endpoints[0])
                chain2 = build_edge_chain(comp, endpoints[1])
                verts1 = [item[0] for item in chain1]
                verts2 = [item[0] for item in chain2]
                # Merge: verts1 goes forward, verts2 is reversed and appended
                verts2_no_last = verts2[:-1] if verts2 else []
                verts2_no_last.reverse()
                chain_verts = verts1 + verts2_no_last

            if len(chain_verts) < 3:
                continue

            rail_map = _face_rail_verts(self.bm, chain_verts, all_selected)
            for i, (v, (r1, r3)) in enumerate(zip(chain_verts, rail_map)):
                if r1 is None:
                    continue

                p2 = v.co.copy()
                p1 = r1.co.copy()
                if r3 is not None:
                    p3 = r3.co.copy()
                else:
                    p3 = p2 + (p2 - p1)
                v_next1 = _get_next_vert(r1, v)
                v_next2 = _get_next_vert(r3, v) if r3 else None
                p0 = v_next1.co.copy() if v_next1 else p1 + (p1 - p2)
                p4 = v_next2.co.copy() if v_next2 else p3 + (p3 - p2)

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

        # GPU batches + modal (unchanged from current)
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
            points_circle = []
            for i in range(33):
                angle = data['limit_min'] + (data['limit_max'] - data['limit_min']) * (i / 32)
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
            points_linear = [
                self.matrix_world @ data['p1'],
                self.matrix_world @ data['p2'],
                self.matrix_world @ data['p3'],
            ]
            self.batches_linear.append(batch_for_shader(shader, 'LINE_STRIP', {"pos": points_linear}))

        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback, (context,), 'WINDOW', 'POST_VIEW')
        self._hud_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_hud_callback, (context,), 'WINDOW', 'POST_PIXEL')
        self.mouse_start_x = event.mouse_x
        self.current_mouse_x = event.mouse_x
        context.window_manager.modal_handler_add(self)
        self._update_header(context)
        return {'RUNNING_MODAL'}

    def _update_header(self, context):
        mode_names = {'CIRCLE': "[C4D]圆弧", 'SPLINE': "[真曲率]样条", 'LINEAR': "[直线]线性"}
        msg = "滑动鼠标 | TAB: 切模式(%s) | 左键: 确认 | 右键: 取消" % mode_names[self.mode]
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
        except Exception:
            self._remove_draw_handler(context)
            return {'CANCELLED'}


classes = (RARA_OT_MeshCurvatureSlide,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
