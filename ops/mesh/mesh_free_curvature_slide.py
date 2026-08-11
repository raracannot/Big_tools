# 自由曲率滑移
# Improved: robust rail walking, boundary support, shared topology engine

import bpy
import bmesh
import gpu
from gpu_extras.batch import batch_for_shader
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_origin_3d, region_2d_to_vector_3d
from mathutils import Vector
from ...utils.gpu_utils import draw_hud_text
from ...utils.edge_topology import div_set, get_endpoints, walk_rail_chain, build_edge_chain


def _get_t(t, p0, p1, alpha=0.5):
    d = p1 - p0
    a = d.dot(d)
    if a == 0:
        return t
    return t + a ** (alpha * 0.5)


def _catmull_rom(p0, p1, p2, p3, t):
    t0 = 0.0
    t1 = _get_t(t0, p0, p1)
    t2 = _get_t(t1, p1, p2)
    t3 = _get_t(t2, p2, p3)
    if t1 == t2:
        return p1
    t_mapped = t1 + t * (t2 - t1)
    A1 = (t1 - t_mapped) / (t1 - t0) * p0 + (t_mapped - t0) / (t1 - t0) * p1 if t1 != t0 else p0
    A2 = (t2 - t_mapped) / (t2 - t1) * p1 + (t_mapped - t1) / (t2 - t1) * p2 if t2 != t1 else p1
    A3 = (t3 - t_mapped) / (t3 - t2) * p2 + (t_mapped - t2) / (t3 - t2) * p3 if t3 != t2 else p2
    B1 = (t2 - t_mapped) / (t2 - t0) * A1 + (t_mapped - t0) / (t2 - t0) * A2 if t2 != t0 else A1
    B2 = (t3 - t_mapped) / (t3 - t1) * A2 + (t_mapped - t1) / (t3 - t1) * A3 if t3 != t1 else A2
    C = (t2 - t_mapped) / (t2 - t1) * B1 + (t_mapped - t1) / (t2 - t1) * B2 if t2 != t1 else B1
    return C


class _GlobalRail:
    def __init__(self, verts):
        self.verts = verts
        self.coords = [v.co.copy() for v in verts]
        self.lengths = [0.0]
        for i in range(1, len(self.coords)):
            dist = (self.coords[i] - self.coords[i - 1]).length
            self.lengths.append(self.lengths[-1] + dist)
        self.total_length = self.lengths[-1]

    def evaluate(self, u):
        u = max(0.0, min(self.total_length, u))
        if u == 0.0:
            return self.coords[0].copy()
        if u == self.total_length:
            return self.coords[-1].copy()
        idx = 0
        for i in range(len(self.lengths) - 1):
            if self.lengths[i] <= u <= self.lengths[i + 1]:
                idx = i
                break
        segment_len = self.lengths[idx + 1] - self.lengths[idx]
        t = (u - self.lengths[idx]) / segment_len if segment_len > 0 else 0.0
        p1 = self.coords[idx]
        p2 = self.coords[idx + 1]
        p0 = self.coords[idx - 1] if idx > 0 else p1 + (p1 - p2)
        p3 = self.coords[idx + 2] if idx < len(self.coords) - 2 else p2 + (p2 - p1)
        return _catmull_rom(p0, p1, p2, p3, t)


class RARA_OT_MeshFreeCurvatureSlide(bpy.types.Operator):
    bl_idname = "rara.model_mesh_free_curvature_slide"
    bl_label = "自由曲率滑移"
    bl_description = "全局连续虚拟轨道：支持跨面滑动、碰撞排序、加线融并、等距与自适应分布，兼容边界/极点拓扑"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def invoke(self, context, event):
        obj = context.edit_object
        self.bm = bmesh.from_edit_mesh(obj.data)
        self.matrix_world = obj.matrix_world.copy()
        if not self._init_data():
            return {'CANCELLED'}
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_callback, (context,), 'WINDOW', 'POST_VIEW'
        )
        self._hud_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_hud_callback, (context,), 'WINDOW', 'POST_PIXEL'
        )
        self._update_header(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _update_header(self, context):
        msg = "左键拖动 | A: 加环切 | X: 融并 | M: 等距分布 | C: 自适应分布 | 回车: 确认 | 右键/ESC: 取消"
        context.workspace.status_text_set(msg)
        self._hud_text = msg

    def _draw_hud_callback(self, context):
        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)

    def _init_data(self):
        self.bm.verts.ensure_lookup_table()
        self.bm.edges.ensure_lookup_table()
        self.bm.faces.ensure_lookup_table()

        # ---- NEW: use shared topology to partition selected edges ----
        selected_edges = [e for e in self.bm.edges if e.select]
        if not selected_edges:
            return False

        components = div_set(selected_edges)
        all_selected = set(selected_edges)
        self.rails = []
        self.slide_data = {}

        for comp in components:
            # Build ordered vertex chain from selected edges
            endpoints = get_endpoints(comp)
            if len(endpoints) == 2:
                chain1 = build_edge_chain(comp, endpoints[0])
                chain2 = build_edge_chain(comp, endpoints[1])
                verts1 = [item[0] for item in chain1]
                verts2 = [item[0] for item in chain2]
                verts2_no_last = verts2[:-1] if verts2 else []
                verts2_no_last.reverse()
                chain_verts = verts1 + verts2_no_last
            elif len(endpoints) == 1:
                chain_data = build_edge_chain(comp, endpoints[0])
                chain_verts = [item[0] for item in chain_data]
            else:
                chain_data = build_edge_chain(comp, comp[0].verts[0])
                chain_verts = [item[0] for item in chain_data if item[1] is not None]
                if len(chain_verts) > 2 and chain_verts[0] == chain_verts[-1]:
                    chain_verts.pop()

            if len(chain_verts) < 2:
                continue

            for v in chain_verts:
                if v in self.slide_data:
                    continue
                unselected = [e for e in v.link_edges if e not in all_selected]
                rail_chain = [v]
                for dir_e in unselected[:2]:
                    temp = walk_rail_chain(self.bm, v, dir_e, all_selected, max_steps=150)
                    if dir_e == unselected[0]:
                        rail_chain.extend(temp)
                    else:
                        temp.reverse()
                        rail_chain = temp + rail_chain

                sel_indices = [i for i, cv in enumerate(rail_chain) if cv.select]
                if sel_indices:
                    start_idx = max(0, sel_indices[0] - 1)
                    end_idx = min(len(rail_chain), sel_indices[-1] + 2)
                    rail_chain = rail_chain[start_idx:end_idx]

                if len(rail_chain) >= 2:
                    if self.rails:
                        curr_dir = (rail_chain[-1].co - rail_chain[0].co).normalized()
                        curr_mid = rail_chain[len(rail_chain) // 2].co
                        closest_rail = min(self.rails, key=lambda r: (r.coords[len(r.coords) // 2] - curr_mid).length_squared)
                        ref_dir = (closest_rail.coords[-1] - closest_rail.coords[0]).normalized()
                        if curr_dir.dot(ref_dir) < 0:
                            rail_chain.reverse()

                    rail_obj = _GlobalRail(rail_chain)
                    rail_id = len(self.rails)
                    self.rails.append(rail_obj)
                    for i, cv in enumerate(rail_chain):
                        if cv.select:
                            self.slide_data[cv] = {
                                'vert': cv, 'init_co': cv.co.copy(),
                                'rail_id': rail_id, 'u': rail_obj.lengths[i],
                                'drag_start_u': rail_obj.lengths[i], 'order_idx': i
                            }

        if not self.slide_data:
            return False

        self.islands = []
        self.island_edges = []
        visited = set()
        for v in self.slide_data.keys():
            if v not in visited:
                island = []
                queue = [v]
                visited.add(v)
                while queue:
                    curr = queue.pop(0)
                    island.append(curr)
                    for e in curr.link_edges:
                        if e.select:
                            neighbor = e.other_vert(curr)
                            if neighbor in self.slide_data and neighbor not in visited:
                                visited.add(neighbor)
                                queue.append(neighbor)
                self.islands.append(island)
                island_set = set(island)
                edges = [(e.verts[0], e.verts[1]) for e in self.bm.edges if e.select and e.verts[0] in island_set and e.verts[1] in island_set]
                self.island_edges.append(edges)

        self.active_island_idx = -1
        self.visual_active_idx = -1
        self.is_dragging = False
        self.mouse_start_x = 0
        self._update_batches()
        return True

    def _update_batches(self):
        self.batches_spline = []
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        for rail in self.rails:
            points_spline = []
            segments = 50
            for i in range(segments + 1):
                u = (i / segments) * rail.total_length
                points_spline.append(self.matrix_world @ rail.evaluate(u))
            if points_spline:
                self.batches_spline.append(batch_for_shader(shader, 'LINE_STRIP', {"pos": points_spline}))

    def _draw_callback(self, context):
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        gpu.state.line_width_set(2.0)
        shader.uniform_float("color", (1.0, 0.6, 0.0, 0.5))
        for batch in self.batches_spline:
            batch.draw(shader)
        gpu.state.line_width_set(4.0)
        highlight_idx = getattr(self, 'visual_active_idx', self.active_island_idx)
        for idx, edges in enumerate(self.island_edges):
            if idx == highlight_idx:
                color = (0.2, 0.6, 1.0, 1.0) if self.is_dragging else (1.0, 1.0, 1.0, 1.0)
            else:
                color = (0.0, 1.0, 0.0, 1.0)
            shader.uniform_float("color", color)
            lines = []
            for v1, v2 in edges:
                if v1.is_valid and v2.is_valid:
                    lines.extend([self.matrix_world @ v1.co, self.matrix_world @ v2.co])
            if lines:
                batch_for_shader(shader, 'LINES', {"pos": lines}).draw(shader)
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('NONE')
        gpu.state.line_width_set(1.0)

    def modal(self, context, event):
        try:
            context.area.tag_redraw()
            if event.type == 'MOUSEMOVE':
                if self.is_dragging:
                    mouse_delta = Vector((event.mouse_x - self.mouse_start_x, event.mouse_y - getattr(self, 'mouse_start_y', event.mouse_y)))
                    self._update_positions(context, mouse_delta)
                else:
                    self.active_island_idx = self._get_closest_island(context, event.mouse_region_x, event.mouse_region_y)
                    self.visual_active_idx = self.active_island_idx
                return {'RUNNING_MODAL'}
            elif event.type == 'A' and event.value == 'PRESS' and not self.is_dragging:
                rail_info = self._get_closest_rail_u(context, event.mouse_region_x, event.mouse_region_y)
                if rail_info:
                    rail_idx, target_u = rail_info
                    self._add_loop_cut_at_u(context, rail_idx, target_u)
                    self._rebuild_islands()
                return {'RUNNING_MODAL'}
            elif event.type == 'X' and event.value == 'PRESS' and not self.is_dragging:
                if self.active_island_idx != -1:
                    self._dissolve_active_island(context)
                    self._rebuild_islands()
                    self.active_island_idx = -1
                return {'RUNNING_MODAL'}
            elif event.type == 'M' and event.value == 'PRESS' and not self.is_dragging:
                self._distribute_evenly(context)
                return {'RUNNING_MODAL'}
            elif event.type == 'C' and event.value == 'PRESS' and not self.is_dragging:
                self._distribute_adaptively(context)
                return {'RUNNING_MODAL'}
            elif event.type == 'LEFTMOUSE':
                if event.value == 'PRESS' and self.active_island_idx != -1:
                    self.is_dragging = True
                    self.mouse_start_x = event.mouse_x
                    self.mouse_start_y = event.mouse_y
                    # Per-rail 2D direction: each rail gets its own screen-space vector
                    self.rail_vec_map = {}
                    active_island = self.islands[self.active_island_idx]
                    rail_ids_in_island = set()
                    for v in active_island:
                        if v in self.slide_data:
                            rail_ids_in_island.add(self.slide_data[v]['rail_id'])
                    for rail_id in rail_ids_in_island:
                        rail = self.rails[rail_id]
                        rail_v = None
                        for v in active_island:
                            if v in self.slide_data and self.slide_data[v]['rail_id'] == rail_id:
                                rail_v = v
                                break
                        if rail_v:
                            data = self.slide_data[rail_v]
                            loc_c = location_3d_to_region_2d(context.region, context.region_data, self.matrix_world @ rail_v.co)
                            epsilon = 0.01
                            test_u = data['u'] + epsilon if data['u'] + epsilon <= rail.total_length else data['u'] - epsilon
                            loc_t = location_3d_to_region_2d(context.region, context.region_data, self.matrix_world @ rail.evaluate(test_u))
                            if loc_c and loc_t:
                                rvec = (loc_t - loc_c).normalized()
                                if test_u < data['u']:
                                    rvec = -rvec
                                self.rail_vec_map[rail_id] = rvec
                        if rail_id not in self.rail_vec_map:
                            self.rail_vec_map[rail_id] = Vector((1.0, 0.0))
                    for v in self.slide_data.keys():
                        if v.is_valid:
                            self.slide_data[v]['drag_start_u'] = self.slide_data[v]['u']
                    return {'RUNNING_MODAL'}
                elif event.value == 'RELEASE' and self.is_dragging:
                    self.is_dragging = False
                return {'PASS_THROUGH'}
            elif event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'} and event.value == 'PRESS':
                self._remove_draw_handler(context)
                return {'FINISHED'}
            elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
                for data in self.slide_data.values():
                    if data['vert'].is_valid:
                        data['vert'].co = data['init_co']
                bmesh.update_edit_mesh(context.edit_object.data)
                self._remove_draw_handler(context)
                return {'CANCELLED'}
            return {'PASS_THROUGH'}
        except Exception as e:
            self._remove_draw_handler(context)
            print("Error:", e)
            return {'CANCELLED'}

    def _update_positions(self, context, mouse_delta):
        if self.active_island_idx == -1:
            return
        active_island = self.islands[self.active_island_idx]
        rail_updates = {}
        for v in active_island:
            if not v.is_valid:
                continue
            data = self.slide_data[v]
            rail_id = data['rail_id']
            rvec = self.rail_vec_map.get(rail_id, Vector((1.0, 0.0)))
            projected_delta = mouse_delta.dot(rvec)
            new_u = data['drag_start_u'] + projected_delta * 0.01
            if rail_id not in rail_updates:
                rail_updates[rail_id] = []
            rail_updates[rail_id].append((v, new_u))
        for rail_id, updates in rail_updates.items():
            rail = self.rails[rail_id]
            rail_verts = [v for v, d in self.slide_data.items() if d['rail_id'] == rail_id and v.is_valid]
            u_values = []
            for rv in rail_verts:
                matched = [nu for av, nu in updates if rv == av]
                u_values.append(matched[0] if matched else self.slide_data[rv]['drag_start_u'])
            u_values = [max(0.0, min(rail.total_length, u)) for u in u_values]
            u_values.sort()
            rail_verts.sort(key=lambda rv: self.slide_data[rv]['order_idx'])
            for i, rv in enumerate(rail_verts):
                self.slide_data[rv]['u'] = u_values[i]
                rv.co = rail.evaluate(u_values[i])
            ref_v = active_island[0]
            if ref_v.is_valid:
                ref_rail_id = self.slide_data[ref_v]['rail_id']
                ref_vec = self.rail_vec_map.get(ref_rail_id, Vector((1.0, 0.0)))
                ref_projected = mouse_delta.dot(ref_vec)
                target_u = self.slide_data[ref_v]['drag_start_u'] + ref_projected * 0.01
                rail = self.rails[ref_rail_id]
                target_u = max(0.0, min(rail.total_length, target_u))
                best_v = None
                min_diff = float('inf')
                for v, d in self.slide_data.items():
                    if d['rail_id'] == ref_rail_id and v.is_valid:
                        diff = abs(d['u'] - target_u)
                        if diff < min_diff:
                            min_diff = diff
                            best_v = v
                if best_v:
                    for idx, island in enumerate(self.islands):
                        if best_v in island:
                            self.visual_active_idx = idx
                            break
        bmesh.update_edit_mesh(context.edit_object.data)

    def _distribute_evenly(self, context):
        for rail_id, rail in enumerate(self.rails):
            rail_verts = [v for v, d in self.slide_data.items() if d['rail_id'] == rail_id and v.is_valid]
            if not rail_verts:
                continue
            rail_verts.sort(key=lambda rv: self.slide_data[rv]['order_idx'])
            N = len(rail_verts)
            step = rail.total_length / (N + 1)
            for i, v in enumerate(rail_verts):
                new_u = step * (i + 1)
                self.slide_data[v]['u'] = new_u
                self.slide_data[v]['drag_start_u'] = new_u
                v.co = rail.evaluate(new_u)
        bmesh.update_edit_mesh(context.edit_object.data)
        context.area.tag_redraw()

    def _distribute_adaptively(self, context):
        for rail_id, rail in enumerate(self.rails):
            rail_verts = [v for v, d in self.slide_data.items() if d['rail_id'] == rail_id and v.is_valid]
            if not rail_verts:
                continue
            rail_verts.sort(key=lambda rv: self.slide_data[rv]['order_idx'])
            N = len(rail_verts)
            samples = 200
            u_vals = [i * rail.total_length / (samples - 1) for i in range(samples)]
            pts = [rail.evaluate(u) for u in u_vals]
            curvatures = [0.0] * samples
            for i in range(1, samples - 1):
                v1 = (pts[i] - pts[i - 1]).normalized()
                v2 = (pts[i + 1] - pts[i]).normalized()
                curvatures[i] = max(0.0, 1.0 - v1.dot(v2))
            max_c = max(curvatures)
            densities = [1.0] * samples
            if max_c > 1e-6:
                for i in range(samples):
                    densities[i] = 0.05 + ((curvatures[i] / max_c) ** 2) * 1.0
            smoothed = [1.0] * samples
            for i in range(samples):
                prev_d = densities[max(0, i - 1)]
                curr_d = densities[i]
                next_d = densities[min(samples - 1, i + 1)]
                smoothed[i] = (prev_d + 2.0 * curr_d + next_d) / 4.0
            cdf = [0.0] * samples
            for i in range(1, samples):
                area = (smoothed[i - 1] + smoothed[i]) * 0.5 * (u_vals[i] - u_vals[i - 1])
                cdf[i] = cdf[i - 1] + area
            total_cdf = cdf[-1]
            if total_cdf > 0:
                step = total_cdf / (N + 1)
                for i, v in enumerate(rail_verts):
                    target_cdf = step * (i + 1)
                    new_u = 0.0
                    for j in range(samples - 1):
                        if cdf[j] <= target_cdf <= cdf[j + 1]:
                            segment_cdf = cdf[j + 1] - cdf[j]
                            t = (target_cdf - cdf[j]) / segment_cdf if segment_cdf > 0 else 0.0
                            new_u = u_vals[j] + t * (u_vals[j + 1] - u_vals[j])
                            break
                    self.slide_data[v]['u'] = new_u
                    self.slide_data[v]['drag_start_u'] = new_u
                    v.co = rail.evaluate(new_u)
        bmesh.update_edit_mesh(context.edit_object.data)
        context.area.tag_redraw()

    def _get_mouse_3d_hit(self, context, mouse_x, mouse_y):
        region = context.region
        rv3d = context.region_data
        coord = (mouse_x, mouse_y)
        view_vector = region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = region_2d_to_origin_3d(region, rv3d, coord)
        depsgraph = context.evaluated_depsgraph_get()
        result, location, normal, index, obj, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector)
        return location if result else None

    def _rebuild_islands(self):
        for rail_id in range(len(self.rails)):
            rail_verts = [v for v, d in self.slide_data.items() if d['rail_id'] == rail_id and v.is_valid]
            rail_verts.sort(key=lambda v: self.slide_data[v]['u'])
            for i, v in enumerate(rail_verts):
                self.slide_data[v]['order_idx'] = i
        self.islands = []
        self.island_edges = []
        visited = set()
        for v in self.slide_data.keys():
            if v.is_valid and v not in visited:
                island = []
                queue = [v]
                visited.add(v)
                while queue:
                    curr = queue.pop(0)
                    island.append(curr)
                    for e in curr.link_edges:
                        if e.select:
                            neighbor = e.other_vert(curr)
                            if neighbor in self.slide_data and neighbor not in visited:
                                visited.add(neighbor)
                                queue.append(neighbor)
                self.islands.append(island)
                island_set = set(island)
                edges = [(e.verts[0], e.verts[1]) for e in self.bm.edges if e.select and e.verts[0] in island_set and e.verts[1] in island_set]
                self.island_edges.append(edges)

    def _get_closest_island(self, context, mouse_x, mouse_y):
        mouse_pt = Vector((mouse_x, mouse_y))
        hit_loc = self._get_mouse_3d_hit(context, mouse_x, mouse_y)
        if hit_loc:
            closest_idx, min_dist_3d = -1, float('inf')
            best_3d_pt = None
            for idx, edges in enumerate(self.island_edges):
                for v1, v2 in edges:
                    if not v1.is_valid or not v2.is_valid:
                        continue
                    p1 = self.matrix_world @ v1.co
                    p2 = self.matrix_world @ v2.co
                    vec_line = p2 - p1
                    vec_hit = hit_loc - p1
                    line_len_sq = vec_line.length_squared
                    if line_len_sq > 0:
                        t = max(0.0, min(1.0, vec_hit.dot(vec_line) / line_len_sq))
                        closest_pt = p1 + t * vec_line
                        dist_sq = (hit_loc - closest_pt).length_squared
                        if dist_sq < min_dist_3d:
                            min_dist_3d = dist_sq
                            closest_idx = idx
                            best_3d_pt = closest_pt
            if best_3d_pt:
                pt_2d = location_3d_to_region_2d(context.region, context.region_data, best_3d_pt)
                if pt_2d and (mouse_pt - pt_2d).length_squared < 1600:
                    return closest_idx
        closest_idx, min_dist_2d = -1, float('inf')
        for idx, edges in enumerate(self.island_edges):
            for v1, v2 in edges:
                if not v1.is_valid or not v2.is_valid:
                    continue
                p1_2d = location_3d_to_region_2d(context.region, context.region_data, self.matrix_world @ v1.co)
                p2_2d = location_3d_to_region_2d(context.region, context.region_data, self.matrix_world @ v2.co)
                if p1_2d and p2_2d:
                    vec_line = p2_2d - p1_2d
                    vec_mouse = mouse_pt - p1_2d
                    line_len_sq = vec_line.length_squared
                    if line_len_sq > 0:
                        t = max(0.0, min(1.0, vec_mouse.dot(vec_line) / line_len_sq))
                        dist_sq = (mouse_pt - (p1_2d + t * vec_line)).length_squared
                        if dist_sq < min_dist_2d:
                            min_dist_2d = dist_sq
                            closest_idx = idx
        return closest_idx if min_dist_2d < 1600 else -1

    def _get_closest_rail_u(self, context, mouse_x, mouse_y):
        mouse_pt = Vector((mouse_x, mouse_y))
        hit_loc = self._get_mouse_3d_hit(context, mouse_x, mouse_y)
        if hit_loc:
            closest_rail_idx, min_dist_3d = -1, float('inf')
            best_u, best_3d_pt = 0.0, None
            for idx, rail in enumerate(self.rails):
                segments = 50
                for i in range(segments):
                    u1 = (i / segments) * rail.total_length
                    u2 = ((i + 1) / segments) * rail.total_length
                    p1 = self.matrix_world @ rail.evaluate(u1)
                    p2 = self.matrix_world @ rail.evaluate(u2)
                    vec_line = p2 - p1
                    vec_hit = hit_loc - p1
                    line_len_sq = vec_line.length_squared
                    if line_len_sq > 0:
                        t = max(0.0, min(1.0, vec_hit.dot(vec_line) / line_len_sq))
                        closest_pt = p1 + t * vec_line
                        dist_sq = (hit_loc - closest_pt).length_squared
                        if dist_sq < min_dist_3d:
                            min_dist_3d = dist_sq
                            closest_rail_idx = idx
                            best_u = u1 + t * (u2 - u1)
                            best_3d_pt = closest_pt
            if best_3d_pt:
                pt_2d = location_3d_to_region_2d(context.region, context.region_data, best_3d_pt)
                if pt_2d and (mouse_pt - pt_2d).length_squared < 1600:
                    return (closest_rail_idx, best_u)
        closest_rail_idx, min_dist_2d = -1, float('inf')
        best_u = 0.0
        for idx, rail in enumerate(self.rails):
            segments = 50
            for i in range(segments):
                u1 = (i / segments) * rail.total_length
                u2 = ((i + 1) / segments) * rail.total_length
                p1_2d = location_3d_to_region_2d(context.region, context.region_data, self.matrix_world @ rail.evaluate(u1))
                p2_2d = location_3d_to_region_2d(context.region, context.region_data, self.matrix_world @ rail.evaluate(u2))
                if p1_2d and p2_2d:
                    vec_line = p2_2d - p1_2d
                    vec_mouse = mouse_pt - p1_2d
                    line_len_sq = vec_line.length_squared
                    if line_len_sq > 0:
                        t = max(0.0, min(1.0, vec_mouse.dot(vec_line) / line_len_sq))
                        dist_sq = (mouse_pt - (p1_2d + t * vec_line)).length_squared
                        if dist_sq < min_dist_2d:
                            min_dist_2d = dist_sq
                            closest_rail_idx = idx
                            best_u = u1 + t * (u2 - u1)
        return (closest_rail_idx, best_u) if min_dist_2d < 1600 else None

    def _add_loop_cut_at_u(self, context, ref_rail_idx, target_u):
        ref_rail = self.rails[ref_rail_idx]
        idx = 0
        for i in range(len(ref_rail.lengths) - 1):
            if ref_rail.lengths[i] <= target_u <= ref_rail.lengths[i + 1]:
                idx = i
                break
        segment_len = ref_rail.lengths[idx + 1] - ref_rail.lengths[idx]
        t = (target_u - ref_rail.lengths[idx]) / segment_len if segment_len > 0 else 0.0
        t = max(0.01, min(0.99, t))
        rail_connections = set()
        for edges in self.island_edges:
            for vA, vB in edges:
                if vA in self.slide_data and vB in self.slide_data:
                    rA = self.slide_data[vA]['rail_id']
                    rB = self.slide_data[vB]['rail_id']
                    if rA != rB:
                        rail_connections.add((min(rA, rB), max(rA, rB)))
        new_verts_by_rail = {}
        for rail_id, rail in enumerate(self.rails):
            if idx + 1 >= len(rail.lengths):
                continue
            local_u = rail.lengths[idx] + t * (rail.lengths[idx + 1] - rail.lengths[idx])
            rail_verts = [v for v, d in self.slide_data.items() if d['rail_id'] == rail_id and v.is_valid]
            rail_verts.sort(key=lambda v: self.slide_data[v]['u'])
            v1, v2 = None, None
            for i in range(len(rail_verts) - 1):
                if self.slide_data[rail_verts[i]]['u'] <= local_u <= self.slide_data[rail_verts[i + 1]]['u']:
                    v1 = rail_verts[i]
                    v2 = rail_verts[i + 1]
                    break
            if v1 and v2:
                edge = self.bm.edges.get((v1, v2))
                if edge:
                    u1 = self.slide_data[v1]['u']
                    u2 = self.slide_data[v2]['u']
                    edge_t = (local_u - u1) / (u2 - u1) if u2 > u1 else 0.5
                    new_edge, new_vert = bmesh.utils.edge_split(edge, v1, edge_t)
                    new_vert.co = rail.evaluate(local_u)
                    new_vert.select = True
                    edge.select = False
                    new_edge.select = False
                    new_verts_by_rail[rail_id] = new_vert
                    self.slide_data[new_vert] = {
                        'vert': new_vert,
                        'init_co': new_vert.co.copy(),
                        'rail_id': rail_id,
                        'u': local_u,
                        'drag_start_u': local_u,
                        'order_idx': 0
                    }
        for rA, rB in rail_connections:
            if rA in new_verts_by_rail and rB in new_verts_by_rail:
                nV_A = new_verts_by_rail[rA]
                nV_B = new_verts_by_rail[rB]
                shared_faces = [f for f in nV_A.link_faces if f in nV_B.link_faces]
                if shared_faces:
                    try:
                        res = bmesh.ops.connect_vert_pair(self.bm, verts=[nV_A, nV_B])
                        for e in res.get('edges', []):
                            e.select = True
                    except Exception:
                        pass
                else:
                    try:
                        e = self.bm.edges.get((nV_A, nV_B))
                        if not e:
                            e = self.bm.edges.new((nV_A, nV_B))
                        e.select = True
                    except Exception:
                        pass
        self.bm.verts.ensure_lookup_table()
        self.bm.edges.ensure_lookup_table()
        self.bm.faces.ensure_lookup_table()
        bmesh.update_edit_mesh(context.edit_object.data)

    def _dissolve_active_island(self, context):
        active_verts = self.islands[self.active_island_idx]
        edges_to_dissolve = []
        for v1, v2 in self.island_edges[self.active_island_idx]:
            if v1.is_valid and v2.is_valid:
                e = self.bm.edges.get((v1, v2))
                if e:
                    edges_to_dissolve.append(e)
        if not edges_to_dissolve:
            return
        for v in active_verts:
            if v in self.slide_data:
                del self.slide_data[v]
        try:
            bmesh.ops.dissolve_edges(self.bm, edges=edges_to_dissolve, use_verts=True)
        except Exception as e:
            print("融并失败:", e)
        self.bm.verts.ensure_lookup_table()
        self.bm.edges.ensure_lookup_table()
        self.bm.faces.ensure_lookup_table()
        bmesh.update_edit_mesh(context.edit_object.data)

    def _remove_draw_handler(self, context):
        if hasattr(self, "_handle"):
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            del self._handle
        if hasattr(self, "_hud_handle"):
            bpy.types.SpaceView3D.draw_handler_remove(self._hud_handle, 'WINDOW')
            del self._hud_handle
        context.workspace.status_text_set(None)
        context.area.tag_redraw()


classes = (RARA_OT_MeshFreeCurvatureSlide,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
