# 可视化测量
import bpy
import gpu
import bmesh
import numpy as np
from gpu_extras.batch import batch_for_shader
from bpy_extras import view3d_utils
from mathutils import Vector, geometry
import math

from ...utils.rara_snapper import RaraSnapper
from ...utils.math_utils import is_double_click
from ...utils.gpu_utils import draw_text_2d, draw_hud_text


class RARA_Model_MeasureSettings(bpy.types.PropertyGroup):
    show_length: bpy.props.BoolProperty(name="显示长度", default=True)
    show_angle: bpy.props.BoolProperty(name="显示角度", default=True)
    show_only_nearest: bpy.props.BoolProperty(name="只显示鼠标最近", default=False)

    unit: bpy.props.EnumProperty(
        name="单位",
        items=[
            ('M', "米 (m)", ""),
            ('CM', "厘米 (cm)", ""),
            ('MM', "毫米 (mm)", ""),
            ('IN', "英寸 (in)", ""),
            ('FT', "英尺 (ft)", "")
        ],
        default='M'
    )
    len_decimals: bpy.props.IntProperty(name="长度小数位", default=3, min=0, max=6)
    ang_decimals: bpy.props.IntProperty(name="角度小数位", default=1, min=0, max=6)
    clear_flag: bpy.props.BoolProperty(default=False)


def _format_length(val_m, settings):
    mults = {'M': 1.0, 'CM': 100.0, 'MM': 1000.0, 'IN': 39.3700787, 'FT': 3.2808399}
    units = {'M': 'm', 'CM': 'cm', 'MM': 'mm', 'IN': 'in', 'FT': 'ft'}
    v = val_m * mults[settings.unit]
    return f"{v:.{settings.len_decimals}f} {units[settings.unit]}"


def _draw_lines_batch(coords, color, width=3.0, context=None):
    if not coords:
        return
    try:
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        shader.bind()
        shader.uniform_float("color", color)
        shader.uniform_float("lineWidth", width)
        if context:
            shader.uniform_float("viewportSize", (context.region.width, context.region.height))
    except ValueError:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind()
        shader.uniform_float("color", color)

    batch = batch_for_shader(shader, 'LINES', {"pos": coords})
    batch.draw(shader)


def draw_callback_measure_3d(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('ALPHA')

    lines_x, lines_y, lines_z, lines_def = [], [], [], []
    ref_lines = []
    ref_lines_ext = []
    hovered_line = []

    all_points_coords = []
    white_points_coords = []
    arc_lines_coords = []

    self._frame_angle_texts.clear()

    segments_to_draw = [seg[:2] for seg in self._segments]
    if self._active_point and self._preview_point:
        if (self._active_point - self._preview_point).length > 1e-5:
            segments_to_draw.append((self._active_point, self._preview_point))

    hover_idx = self._get_hovered_segment_idx(context, self._mouse_pos, threshold=30.0)

    for i, seg in enumerate(self._segments):
        p1, p2, is_ref = seg
        vec = (p2 - p1).normalized() if (p2 - p1).length > 1e-5 else Vector((0, 0, 0))

        if is_ref:
            ref_lines.extend([p1, p2])
            ref_lines_ext.extend([p1 - vec * 1000, p2 + vec * 1000])
        else:
            if abs(vec.x) > 0.9999:
                lines_x.extend([p1, p2])
            elif abs(vec.y) > 0.9999:
                lines_y.extend([p1, p2])
            elif abs(vec.z) > 0.9999:
                lines_z.extend([p1, p2])
            else:
                lines_def.extend([p1, p2])

        if i == hover_idx:
            hovered_line.extend([p1, p2])

        all_points_coords.extend([p1, p2])

    if self._active_point and self._preview_point:
        p1, p2 = self._active_point, self._preview_point
        vec = (p2 - p1).normalized() if (p2 - p1).length > 1e-5 else Vector((0, 0, 0))
        if abs(vec.x) > 0.9999:
            lines_x.extend([p1, p2])
        elif abs(vec.y) > 0.9999:
            lines_y.extend([p1, p2])
        elif abs(vec.z) > 0.9999:
            lines_z.extend([p1, p2])
        else:
            lines_def.extend([p1, p2])

    if self._active_point and not self._preview_point:
        all_points_coords.append(self._active_point)
    elif not self._active_point and self._preview_point:
        all_points_coords.append(self._preview_point)

    if self._hovered_point:
        white_points_coords.append(self._hovered_point)
        all_points_coords = [p for p in all_points_coords if (p - self._hovered_point).length > 1e-5]

    # --- arc calculation ---
    vertices = []
    adj = []

    def get_vertex_idx(pt):
        for i, v in enumerate(vertices):
            if (v - pt).length < 1e-4:
                return i
        vertices.append(pt)
        adj.append([])
        return len(vertices) - 1

    for p1, p2 in segments_to_draw:
        i1 = get_vertex_idx(p1)
        i2 = get_vertex_idx(p2)
        adj[i1].append(p2)
        adj[i2].append(p1)

    for i, v in enumerate(vertices):
        neighbors = adj[i]
        if len(neighbors) >= 2:
            for j in range(len(neighbors)):
                for k in range(j + 1, len(neighbors)):
                    n1 = neighbors[j]
                    n2 = neighbors[k]
                    vec1 = (n1 - v).normalized()
                    vec2 = (n2 - v).normalized()
                    dot_val = max(-1.0, min(1.0, vec1.dot(vec2)))
                    angle_rad = math.acos(dot_val)
                    if angle_rad < 1e-4 or angle_rad > math.pi - 1e-4:
                        continue
                    l1 = (n1 - v).length
                    l2 = (n2 - v).length
                    r = min(0.3, min(l1, l2) * 0.9)
                    u = vec1
                    normal = vec1.cross(vec2).normalized()
                    if normal.length < 1e-5:
                        continue
                    v_vec = normal.cross(u).normalized()
                    steps = max(8, int(math.degrees(angle_rad) / 5))
                    prev_pt = v + r * u
                    for step in range(1, steps + 1):
                        t = step / steps * angle_rad
                        curr_pt = v + r * (math.cos(t) * u + math.sin(t) * v_vec)
                        arc_lines_coords.extend([prev_pt, curr_pt])
                        prev_pt = curr_pt
                    mid_t = angle_rad / 2.0
                    text_r = r * 1.2
                    text_pos3d = v + text_r * (math.cos(mid_t) * u + math.sin(mid_t) * v_vec)
                    settings = context.scene.rara_measure_settings
                    angle_deg = math.degrees(angle_rad)
                    self._frame_angle_texts.append((f"{angle_deg:.{settings.ang_decimals}f}°", text_pos3d))

    # Draw lines
    _draw_lines_batch(lines_x, (1.0, 0.2, 0.2, 1.0), 3.0, context)
    _draw_lines_batch(lines_y, (0.2, 1.0, 0.2, 1.0), 3.0, context)
    _draw_lines_batch(lines_z, (0.2, 0.5, 1.0, 1.0), 3.0, context)
    _draw_lines_batch(lines_def, (0.2, 0.8, 1.0, 1.0), 3.0, context)
    _draw_lines_batch(ref_lines, (0.9, 0.2, 1.0, 1.0), 4.0, context)
    _draw_lines_batch(ref_lines_ext, (0.9, 0.2, 1.0, 0.3), 1.5, context)
    _draw_lines_batch(hovered_line, (1.0, 1.0, 1.0, 0.8), 6.0, context)
    _draw_lines_batch(arc_lines_coords, (1.0, 0.8, 0.2, 1.0), 2.0, context)

    # Draw points
    if all_points_coords:
        try:
            shader_pt = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
            shader_pt.bind()
            shader_pt.uniform_float("color", (1.0, 0.2, 0.2, 1.0))
            shader_pt.uniform_float("size", 10.0)
        except ValueError:
            shader_pt = gpu.shader.from_builtin('UNIFORM_COLOR')
            shader_pt.bind()
            shader_pt.uniform_float("color", (1.0, 0.2, 0.2, 1.0))
        batch_points = batch_for_shader(shader_pt, 'POINTS', {"pos": all_points_coords})
        batch_points.draw(shader_pt)

    if white_points_coords:
        try:
            shader_w = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
            shader_w.bind()
            shader_w.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
            shader_w.uniform_float("size", 14.0)
        except ValueError:
            shader_w = gpu.shader.from_builtin('UNIFORM_COLOR')
            shader_w.bind()
            shader_w.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
        batch_w = batch_for_shader(shader_w, 'POINTS', {"pos": white_points_coords})
        batch_w.draw(shader_w)

    gpu.state.depth_test_set('LESS_EQUAL')


def draw_callback_measure_2d(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    region = context.region
    rv3d = context.space_data.region_3d
    settings = context.scene.rara_measure_settings

    texts_to_draw = []

    if settings.show_length:
        segments_to_draw = [seg[:2] for seg in self._segments]
        if self._active_point and self._preview_point:
            if (self._active_point - self._preview_point).length > 1e-5:
                segments_to_draw.append((self._active_point, self._preview_point))

        for p1, p2 in segments_to_draw:
            dist = (p1 - p2).length
            mid_pt = (p1 + p2) / 2.0
            pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, mid_pt)
            if pos2d:
                is_active = (self._active_point and self._preview_point and
                             (p1 == self._active_point and p2 == self._preview_point))
                color = (0.8, 0.8, 0.8, 1.0) if is_active else (0.2, 1.0, 0.2, 1.0)
                text_str = _format_length(dist, settings)
                texts_to_draw.append((text_str, pos2d[0] + 10, pos2d[1] + 10, 16, color))

    if settings.show_angle:
        for text, pos3d in self._frame_angle_texts:
            pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pos3d)
            if pos2d:
                texts_to_draw.append((text, pos2d[0] - 15, pos2d[1] - 5, 16, (1.0, 0.8, 0.2, 1.0)))

    if settings.show_only_nearest and texts_to_draw:
        mx, my = self._mouse_pos
        closest_text = min(texts_to_draw, key=lambda t: (t[1] - mx) ** 2 + (t[2] - my) ** 2)
        texts_to_draw = [closest_text]

    for t in texts_to_draw:
        draw_text_2d(t[0], (t[1], t[2]), t[4], t[3])

    if self._current_snap and self._current_snap_type:
        pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, self._current_snap)
        if pos2d:
            color = (0.2, 1.0, 1.0, 1.0) if self._current_snap_type == "垂足" else (1.0, 1.0, 0.2, 1.0)
            draw_text_2d(f"[{self._current_snap_type}]", (pos2d[0] + 15, pos2d[1] - 15), color, 14)

    if self._active_point and self._preview_point:
        cur_len = (self._preview_point - self._active_point).length
        if cur_len > 1e-5:
            chain_len = self._get_continuous_length() + cur_len
            mx, my = self._mouse_pos
            cur_str = _format_length(cur_len, settings)
            tot_str = _format_length(chain_len, settings)
            info_text = f"当前: {cur_str} | 总计: {tot_str}"
            if self._lock_axis:
                info_text += f" [锁定 {self._lock_axis} 轴]"
            draw_text_2d(info_text, (mx + 15, my - 35), (1.0, 1.0, 1.0, 1.0), 14)

    hud = getattr(self, '_hud_text', None)
    if hud:
        draw_hud_text(hud, context)


class RARA_OT_Model_Measure(bpy.types.Operator):
    bl_idname = "rara.model_measure"
    bl_label = "可视化测量"
    bl_description = "基于GPU的极速顶点吸附、线段与圆弧夹角测量工具\n左键连线/双击结束 | Ctrl+左键拖动点 | B吸附切换 | X/Y/Z锁定 | T标记参考 | ESC退出"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapper = RaraSnapper()
        self._segments = []
        self._active_point = None
        self._current_snap = None
        self._preview_point = None
        self._current_snap_type = ""
        self._hovered_point = None
        self._mouse_pos = (0, 0)
        self._lock_axis = None
        self._frame_angle_texts = []
        self._handle_3d = None
        self._handle_2d = None
        self._timer = None
        self._undo_state = None
        self._prev_active_point = None
        self._snap_enabled = True
        self._drag_seg_idx = -1
        self._drag_pt_idx = -1

    @staticmethod
    def _get_point_line_projection(pt, line_p1, line_p2):
        line_vec = (line_p2 - line_p1).normalized()
        if line_vec.length < 1e-5:
            return None
        pt_vec = pt - line_p1
        t = pt_vec.dot(line_vec)
        return line_p1 + line_vec * t

    @staticmethod
    def _get_lines_intersection(p1, d1, p2, d2, tolerance=0.05):
        w0 = p1 - p2
        a = d1.dot(d1)
        b = d1.dot(d2)
        c = d2.dot(d2)
        d = d1.dot(w0)
        e = d2.dot(w0)

        denominator = a * c - b * b
        if denominator < 1e-6:
            return None

        s = (b * e - c * d) / denominator
        t = (a * e - b * d) / denominator

        closest_pt1 = p1 + d1 * s
        closest_pt2 = p2 + d2 * t

        if (closest_pt1 - closest_pt2).length < tolerance:
            return (closest_pt1 + closest_pt2) / 2.0
        return None

    def _get_hovered_segment_idx(self, context, mouse_pos, threshold=20.0):
        region = context.region
        rv3d = context.space_data.region_3d

        best_dist = threshold
        best_idx = -1

        for i, seg in enumerate(self._segments):
            p1, p2, _ = seg
            pos1 = view3d_utils.location_3d_to_region_2d(region, rv3d, p1)
            pos2 = view3d_utils.location_3d_to_region_2d(region, rv3d, p2)
            if pos1 and pos2:
                v = pos2 - pos1
                w = Vector(mouse_pos) - pos1
                c1 = w.dot(v)
                if c1 <= 0:
                    dist = (Vector(mouse_pos) - pos1).length
                else:
                    c2 = v.dot(v)
                    if c2 <= c1:
                        dist = (Vector(mouse_pos) - pos2).length
                    else:
                        b = c1 / c2
                        pb = pos1 + b * v
                        dist = (Vector(mouse_pos) - pb).length

                if dist < best_dist:
                    best_dist = dist
                    best_idx = i

        return best_idx

    def _get_hovered_point(self, context, mouse_pos, threshold=20.0):
        region = context.region
        rv3d = context.space_data.region_3d

        best_dist = threshold ** 2
        best_pt = None

        pts = []
        for seg in self._segments:
            pts.extend([seg[0], seg[1]])
        if self._active_point:
            pts.append(self._active_point)

        for pt in pts:
            pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pt)
            if pos2d:
                dist_sq = (pos2d[0] - mouse_pos[0]) ** 2 + (pos2d[1] - mouse_pos[1]) ** 2
                if dist_sq < best_dist:
                    best_dist = dist_sq
                    best_pt = pt

        return best_pt

    def _find_near_segment_point(self, context, mouse_pos, threshold=20.0):
        """找到鼠标附近的线段端点，返回 (seg_idx, pt_idx) 或 (-1, -1)"""
        region = context.region
        rv3d = context.space_data.region_3d

        best_dist_sq = threshold ** 2
        best_seg, best_pt = -1, -1

        for i, seg in enumerate(self._segments):
            for j in (0, 1):
                pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, seg[j])
                if pos2d:
                    dist_sq = (pos2d[0] - mouse_pos[0]) ** 2 + (pos2d[1] - mouse_pos[1]) ** 2
                    if dist_sq < best_dist_sq:
                        best_dist_sq = dist_sq
                        best_seg, best_pt = i, j

        return best_seg, best_pt

    def _get_continuous_length(self):
        if not self._active_point:
            return 0.0
        total = 0.0
        curr = self._active_point
        visited = set()

        while True:
            found = False
            for i, seg in enumerate(self._segments):
                if i in visited:
                    continue
                if (seg[1] - curr).length < 1e-5:
                    total += (seg[0] - seg[1]).length
                    curr = seg[0]
                    visited.add(i)
                    found = True
                    break
                elif (seg[0] - curr).length < 1e-5:
                    total += (seg[1] - seg[0]).length
                    curr = seg[1]
                    visited.add(i)
                    found = True
                    break
            if not found:
                break
        return total

    def modal(self, context, event):
        try:
            settings = context.scene.rara_measure_settings
            region = context.region
            rv3d = context.space_data.region_3d

            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            if settings.clear_flag:
                self._segments.clear()
                self._active_point = None
                self._lock_axis = None
                settings.clear_flag = False

            nav_events = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}
            if event.type in nav_events:
                return {'PASS_THROUGH'}

            if event.type == 'ESC' and event.value == 'PRESS':
                self.finish(context)
                return {'CANCELLED'}

            if event.type == 'B' and event.value == 'PRESS':
                self._snap_enabled = not self._snap_enabled
                self.update_header(context)
                self.report({'INFO'}, f"吸附: {'全吸附' if self._snap_enabled else '仅顶点'}")
                return {'RUNNING_MODAL'}

            self._mouse_pos = (event.mouse_region_x, event.mouse_region_y)

            if event.value == 'PRESS' and not event.ctrl:
                if event.type == 'X':
                    self._lock_axis = 'X' if self._lock_axis != 'X' else None
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'Y':
                    self._lock_axis = 'Y' if self._lock_axis != 'Y' else None
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'Z':
                    self._lock_axis = 'Z' if self._lock_axis != 'Z' else None
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'T':
                    idx = self._get_hovered_segment_idx(context, self._mouse_pos, threshold=30.0)
                    if idx != -1:
                        self._segments[idx][2] = not self._segments[idx][2]
                        self.update_header(context)
                    return {'RUNNING_MODAL'}

            # ── Ctrl+左键拖动已有点 ──
            if event.type == 'LEFTMOUSE':
                if event.value == 'PRESS' and event.ctrl:
                    near_seg, near_pt = self._find_near_segment_point(context, self._mouse_pos, threshold=20.0)
                    if near_seg != -1:
                        self._drag_seg_idx = near_seg
                        self._drag_pt_idx = near_pt
                        self._active_point = None
                        return {'RUNNING_MODAL'}
                    return {'PASS_THROUGH'}
                elif event.value == 'RELEASE' and self._drag_seg_idx != -1:
                    self._drag_seg_idx = -1
                    self._drag_pt_idx = -1
                    return {'RUNNING_MODAL'}

            # ── MOUSEMOVE ──
            if event.type == 'MOUSEMOVE':
                # Ctrl+拖动：更新已有点的位置
                if self._drag_seg_idx != -1 and self._drag_seg_idx < len(self._segments):
                    # 只在有吸附点时更新；否则保持原位
                    if self._current_snap:
                        self._segments[self._drag_seg_idx][self._drag_pt_idx] = self._current_snap.copy()
                    return {'RUNNING_MODAL'}

                self._current_snap_type = ""
                self._hovered_point = self._get_hovered_point(context, self._mouse_pos)

                if self._hovered_point:
                    self._current_snap = self._hovered_point.copy()
                else:
                    virtual_points = []
                    ref_lines = [seg for seg in self._segments if seg[2]]

                    if self._active_point:
                        for seg in ref_lines:
                            proj = self._get_point_line_projection(self._active_point, seg[0], seg[1])
                            if proj:
                                virtual_points.append({'pos': proj, 'type': '垂足'})

                    for i in range(len(ref_lines)):
                        for j in range(i + 1, len(ref_lines)):
                            p1, d1 = ref_lines[i][0], (ref_lines[i][1] - ref_lines[i][0]).normalized()
                            p2, d2 = ref_lines[j][0], (ref_lines[j][1] - ref_lines[j][0]).normalized()
                            intersect = self._get_lines_intersection(p1, d1, p2, d2)
                            if intersect:
                                virtual_points.append({'pos': intersect, 'type': '交点'})

                    best_v_dist = 20.0 ** 2
                    best_v_pt = None
                    best_v_type = ""

                    for vp in virtual_points:
                        pos2d = view3d_utils.location_3d_to_region_2d(region, rv3d, vp['pos'])
                        if pos2d:
                            dist_sq = (pos2d[0] - self._mouse_pos[0]) ** 2 + (pos2d[1] - self._mouse_pos[1]) ** 2
                            if dist_sq < best_v_dist:
                                best_v_dist = dist_sq
                                best_v_pt = vp['pos']
                                best_v_type = vp['type']

                    if best_v_pt:
                        self._current_snap = best_v_pt.copy()
                        self._current_snap_type = best_v_type
                    else:
                        pt, snap_type = self.snapper.find_nearest(
                            context, event,
                            snap_vertex=True,
                            snap_half=self._snap_enabled,
                            snap_third=self._snap_enabled,
                            snap_edge=self._snap_enabled,
                            snap_face=self._snap_enabled
                        )
                        if pt:
                            self._current_snap = pt.copy()
                        else:
                            self._current_snap = None

                if self._current_snap:
                    self._preview_point = self._current_snap.copy()
                elif self._active_point:
                    view_vec = view3d_utils.region_2d_to_vector_3d(region, rv3d, self._mouse_pos)
                    ray_orig = view3d_utils.region_2d_to_origin_3d(region, rv3d, self._mouse_pos)
                    plane_normal = rv3d.view_rotation @ Vector((0, 0, 1))
                    intersect = geometry.intersect_line_plane(ray_orig, ray_orig + view_vec, self._active_point, plane_normal)
                    if intersect:
                        self._preview_point = intersect
                    else:
                        self._preview_point = self._active_point.copy()
                else:
                    self._preview_point = None

                if self._active_point and self._lock_axis:
                    for pt in [self._preview_point, self._current_snap]:
                        if pt:
                            if self._lock_axis == 'X':
                                pt.y = self._active_point.y
                                pt.z = self._active_point.z
                            elif self._lock_axis == 'Y':
                                pt.x = self._active_point.x
                                pt.z = self._active_point.z
                            elif self._lock_axis == 'Z':
                                pt.x = self._active_point.x
                                pt.y = self._active_point.y

                return {'RUNNING_MODAL'}

            # ── LEFTMOUSE (普通) ──
            elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                dbl = is_double_click(0.3)

                if dbl:
                    if self._undo_state == 'STARTED':
                        self._active_point = None
                    elif self._undo_state == 'ADDED':
                        if self._segments:
                            self._segments.pop()
                        self._active_point = self._prev_active_point
                    self._undo_state = None

                    idx = self._get_hovered_segment_idx(context, self._mouse_pos, threshold=30.0)
                    if idx != -1:
                        self._segments[idx][2] = not self._segments[idx][2]
                        self.update_header(context)
                        return {'RUNNING_MODAL'}

                    if self._active_point:
                        self._active_point = None
                        self._lock_axis = None
                        self.update_header(context)
                        return {'RUNNING_MODAL'}

                    return {'RUNNING_MODAL'}

                self._undo_state = None
                self._prev_active_point = self._active_point.copy() if self._active_point else None

                click_pt = self._preview_point
                if click_pt:
                    if self._active_point is None:
                        if self._current_snap:
                            self._active_point = click_pt.copy()
                            self._undo_state = 'STARTED'
                    else:
                        if (self._active_point - click_pt).length > 1e-5:
                            is_duplicate = False
                            for seg in self._segments:
                                d1 = (seg[0] - self._active_point).length + (seg[1] - click_pt).length
                                d2 = (seg[0] - click_pt).length + (seg[1] - self._active_point).length
                                if d1 < 1e-4 or d2 < 1e-4:
                                    is_duplicate = True
                                    break

                            if not is_duplicate:
                                self._segments.append([self._active_point, click_pt.copy(), False])
                                self._undo_state = 'ADDED'

                        self._active_point = click_pt.copy()
                        self._lock_axis = None

                    self.update_header(context)

            # ── RIGHTMOUSE ──
            elif event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                if self._active_point:
                    self._active_point = None
                    self._lock_axis = None
                else:
                    for seg in self._segments:
                        seg[2] = False
                self.update_header(context)

            # ── Ctrl+X 删除 ──
            elif event.type == 'X' and event.value == 'PRESS' and event.ctrl:
                if event.shift:
                    self._segments.clear()
                    self._active_point = None
                    self._lock_axis = None
                    self._hovered_point = None
                    self._current_snap = None
                    self._preview_point = None
                else:
                    if self._hovered_point:
                        self._segments = [
                            seg for seg in self._segments
                            if (seg[0] - self._hovered_point).length > 1e-4 and (seg[1] - self._hovered_point).length > 1e-4
                        ]
                        if self._active_point and (self._active_point - self._hovered_point).length < 1e-4:
                            self._active_point = None
                        self._hovered_point = None
                self.update_header(context)

            elif event.type == 'BACK_SPACE' and event.value == 'PRESS':
                if self._active_point is not None:
                    self._active_point = None
                    self._lock_axis = None
                elif self._segments:
                    self._segments.pop()
                self.update_header(context)

            return {'RUNNING_MODAL'}

        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"可视化测量出错: {str(e)}")
            return {'CANCELLED'}

    def invoke(self, context, event):
        if context.area.type != 'VIEW_3D':
            self.report({'WARNING'}, "View3D not found, cannot run operator")
            return {'CANCELLED'}
        if context.mode not in ('EDIT_MESH', 'OBJECT'):
            self.report({'WARNING'}, "请在编辑模式或物体模式下使用")
            return {'CANCELLED'}

        self.init_region = context.region
        self.snapper.build_cache(context, scope='ALL')
        args = (self, context)
        self._handle_3d = bpy.types.SpaceView3D.draw_handler_add(draw_callback_measure_3d, args, 'WINDOW', 'POST_VIEW')
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_callback_measure_2d, args, 'WINDOW', 'POST_PIXEL')
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)

        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        snap = "全吸附" if self._snap_enabled else "顶点"
        lock = self._lock_axis if self._lock_axis else "-"
        self._hud_text = f"【可视化测量】段数: {len(self._segments)} | B: 吸附({snap}) | 锁定: {lock} | Ctrl+左键: 拖动点 | T: 标记参考 | ESC: 退出"

    def finish(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._handle_3d is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle_3d, 'WINDOW')
            self._handle_3d = None
        if self._handle_2d is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, 'WINDOW')
            self._handle_2d = None
        context.area.tag_redraw()


classes = (RARA_Model_MeasureSettings, RARA_OT_Model_Measure,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.rara_measure_settings = bpy.props.PointerProperty(type=RARA_Model_MeasureSettings)


def unregister():
    del bpy.types.Scene.rara_measure_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)