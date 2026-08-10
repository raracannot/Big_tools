# 焊接顶点到边 — 采用 KDTree 空间加速 + 交互预览

import math
import bpy
import bmesh
import mathutils
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from ...utils.gpu_utils import SHADER
from ...utils.draw_callbacks import draw_weld


def closest_point_on_segment(p, a, b):
    ap = p - a
    ab = b - a
    ab_dot_ab = ab.dot(ab)
    if ab_dot_ab == 0:
        return a
    t = ap.dot(ab) / ab_dot_ab
    t = max(0.0, min(1.0, t))
    return a + t * ab


class WeldPreview:

    def __init__(self):
        self.weld_pairs = []

    def update_data(self, pairs):
        self.weld_pairs = pairs

    def draw(self, context):
        if not self.weld_pairs:
            return

        import gpu

        region = context.region
        rv3d = context.space_data.region_3d

        line_color = (0.2, 0.8, 1.0, 0.8)
        point_color = (1.0, 0.8, 0.2, 1.0)

        lines_3d_coords = []
        tris_coords = []
        tris_indices = []

        radius = 4.0
        seg = 12
        vert_idx = 0

        for orig_pt, target_pt in self.weld_pairs:
            lines_3d_coords.extend([orig_pt, target_pt])

            co2d = view3d_utils.location_3d_to_region_2d(region, rv3d, target_pt)
            if not co2d:
                continue

            center_idx = vert_idx
            tris_coords.append(co2d)
            vert_idx += 1

            for i in range(seg):
                ang = 2 * math.pi * i / seg
                x = co2d[0] + math.cos(ang) * radius
                y = co2d[1] + math.sin(ang) * radius
                tris_coords.append((x, y))

                next_i = (i + 1) % seg
                tris_indices.append((center_idx, center_idx + 1 + i, center_idx + 1 + next_i))

            vert_idx += seg

        if lines_3d_coords:
            SHADER.bind()
            SHADER.uniform_float("color", line_color)
            batch_lines = batch_for_shader(SHADER, 'LINES', {"pos": lines_3d_coords})
            gpu.state.blend_set('ALPHA')
            gpu.state.line_width_set(2.0)
            gpu.state.depth_test_set('LESS_EQUAL')
            batch_lines.draw(SHADER)
            gpu.state.depth_test_set('NONE')
            gpu.state.line_width_set(1.0)

        if tris_coords:
            SHADER.bind()
            SHADER.uniform_float("color", point_color)
            batch_tris = batch_for_shader(SHADER, 'TRIS', {"pos": tris_coords}, indices=tris_indices)
            batch_tris.draw(SHADER)

        gpu.state.blend_set('NONE')


class RARA_OT_Model_WeldToEdges(bpy.types.Operator):

    bl_idname = "rara.model_weld_verts_to_edges"
    bl_label = "焊接顶点到边 (交互预览)"
    bl_description = "KDTree空间加速检测选中顶点到最近边的投影点，精确焊接到边线上\n【Ctrl+滚轮】粗调阈值 | 【Shift+滚轮】细调 | 【回车/左键】确认 | 【ESC/右键】取消"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    threshold: bpy.props.FloatProperty(default=0.001, min=0.0, precision=5)

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
            self.report({'WARNING'}, "请选择一个编辑模式下的网格对象")
            return {'CANCELLED'}

        self.obj = obj
        self.bm = bmesh.from_edit_mesh(obj.data)
        self.bm.verts.ensure_lookup_table()
        self.bm.edges.ensure_lookup_table()

        self.selected_verts = [v for v in self.bm.verts if v.select and not v.hide]
        if not self.selected_verts:
            self.report({'WARNING'}, "请至少选一个可见顶点")
            return {'CANCELLED'}

        # Build per-vertex edge candidates (exclude self-connected / hidden)
        self.vert_edge_data = {}
        all_edge_set = set()
        for v in self.selected_verts:
            valid = [e for e in self.bm.edges if v not in e.verts and not e.hide]
            if not valid:
                continue
            self.vert_edge_data[v] = valid
            all_edge_set.update(valid)

        self.all_candidate_edges = list(all_edge_set)
        if not self.all_candidate_edges:
            self.report({'WARNING'}, "没有可用的目标边")
            return {'CANCELLED'}

        # Build KDTree from edge midpoints
        size = len(self.all_candidate_edges)
        self.kd = mathutils.kdtree.KDTree(size)
        self.edge_to_kd_idx = {}
        for i, e in enumerate(self.all_candidate_edges):
            mid = (e.verts[0].co + e.verts[1].co) / 2.0
            self.kd.insert(mid, i)
            self.edge_to_kd_idx[e] = i
        self.kd.balance()

        # Compute max edge length for per-vertex search radius
        self.max_edge_len = max((e.calc_length() for e in self.all_candidate_edges)) if self.all_candidate_edges else 1.0

        self.preview_drawer = WeldPreview()
        self.weld_data = []
        self.update_preview()

        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(draw_weld, args, 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update_preview(self):
        self.weld_data.clear()
        draw_pairs = []
        matrix_world = self.obj.matrix_world
        search_radius = self.threshold + self.max_edge_len

        for v in self.selected_verts:
            if v not in self.vert_edge_data:
                continue
            candidates = self.kd.find_range(v.co, search_radius)

            best_edge = None
            min_dist = self.threshold + 1.0
            best_proj = None

            for (co, kd_idx, dist) in candidates:
                e = self.all_candidate_edges[kd_idx]
                # Must be in this vertex's valid edge set
                if e not in self.vert_edge_data[v]:
                    continue
                v1_co = e.verts[0].co
                v2_co = e.verts[1].co
                proj = closest_point_on_segment(v.co, v1_co, v2_co)
                d = (v.co - proj).length
                if d < min_dist:
                    min_dist = d
                    best_edge = e
                    best_proj = proj

            if best_edge and min_dist <= self.threshold:
                t_val = (best_proj - best_edge.verts[0].co).length
                total = best_edge.calc_length()
                t_norm = t_val / total if total > 0 else 0.0
                t_norm = max(0.0, min(1.0, t_norm))
                self.weld_data.append((v, best_edge, best_proj, t_norm))
                draw_pairs.append((matrix_world @ v.co, matrix_world @ best_proj))

        self.preview_drawer.update_data(draw_pairs)

    def update_header(self, context):
        msg = (f"【焊接到边】待焊接: {len(self.weld_data)} | "
               f"阈值: {self.threshold:.5f} | "
               f"Ctrl+滚轮: 粗调 | Shift+滚轮: 细调 | 回车/左键: 确认 | ESC: 取消")
        self._hud_text = msg

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}
            context.area.tag_redraw()

            if event.type in {'ESC', 'RIGHTMOUSE'}:
                self.finish(context)
                return {'CANCELLED'}

            if event.type in {'RET', 'NUMPAD_ENTER', 'LEFTMOUSE'} and event.value == 'PRESS':
                self.execute_op(context)
                self.finish(context)
                return {'FINISHED'}

            if (event.ctrl or event.shift) and event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                step = 0.02 if event.type == 'WHEELUPMOUSE' else -0.02
                if event.shift:
                    step *= 0.01
                self.threshold = max(0.0, self.threshold + step)
                self.update_preview()
                self.update_header(context)
                return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"焊接到边出错: {str(e)}")
            return {'CANCELLED'}

    def execute_op(self, context):
        try:
            if not self.weld_data:
                self.report({'INFO'}, "当前阈值下没有可焊接的顶点")
                return

            bm = self.bm
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()

            welds_per_edge = {}
            for v, edge, target_pt, t_val in self.weld_data:
                if not edge.is_valid:
                    continue
                if edge not in welds_per_edge:
                    welds_per_edge[edge] = []
                welds_per_edge[edge].append((v, target_pt, t_val))

            welded_count = 0

            for edge, welds in welds_per_edge.items():
                welds.sort(key=lambda x: x[2], reverse=True)
                for v, target_pt, t_val in welds:
                    if not v.is_valid or not edge.is_valid:
                        continue
                    if edge.calc_length() == 0:
                        continue
                    split_result = bmesh.utils.edge_split(edge, edge.verts[0], t_val)
                    new_vert = split_result[1]
                    new_vert.co = target_pt
                    if v.is_valid and new_vert.is_valid:
                        bmesh.ops.pointmerge(bm, verts=[v, new_vert], merge_co=target_pt)
                        welded_count += 1
                        bm.verts.ensure_lookup_table()
                        bm.edges.ensure_lookup_table()

            bmesh.update_edit_mesh(self.obj.data)
            self.report({'INFO'}, f"成功焊接 {welded_count} 个顶点")
        except Exception as e:
            self.report({'ERROR'}, f"执行焊接时出错: {str(e)}")

    def finish(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if hasattr(self, '_handle') and self._handle:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            except Exception:
                pass
            self._handle = None
        try:
            context.area.tag_redraw()
        except Exception:
            pass


classes = (RARA_OT_Model_WeldToEdges,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
