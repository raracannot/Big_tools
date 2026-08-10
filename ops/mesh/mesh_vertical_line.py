# 绘制垂直线
import math
import bpy
import bmesh
import mathutils
import gpu
from gpu_extras.batch import batch_for_shader
from bpy_extras import view3d_utils
from ...utils.gpu_utils import SHADER, draw_lines_2d, draw_circle_2d, draw_text_2d, draw_hud_text


# ==========================================
# Vertical Line Preview
# ==========================================

class VerticalLinePreview:
    def draw(self, op, context):
        if not op._drawing:
            return
        try:
            # Selected vertex (red)
            if op._selected_vertex_co is not None:
                batch = batch_for_shader(SHADER, 'POINTS', {"pos": [op._selected_vertex_co]})
                SHADER.bind()
                SHADER.uniform_float("color", (1, 0, 0, 1))
                gpu.state.point_size_set(6)
                batch.draw(SHADER)

            # Nearest edge (yellow)
            if op._nearest_edge is not None:
                batch = batch_for_shader(SHADER, 'LINES', {"pos": [op._nearest_edge[0], op._nearest_edge[1]]})
                SHADER.bind()
                SHADER.uniform_float("color", (1, 1, 0, 1))
                batch.draw(SHADER)

            # Vertical line (blue)
            if op._vertical_point is not None and op._selected_vertex_co is not None:
                batch = batch_for_shader(SHADER, 'LINES', {"pos": [op._selected_vertex_co, op._vertical_point]})
                SHADER.bind()
                SHADER.uniform_float("color", (0, 0.5, 1, 1))
                batch.draw(SHADER)
        except ReferenceError:
            return


def draw_hud_callback(op, context):
    hud = getattr(op, '_hud_text', None)
    if hud:
        draw_hud_text(hud, context)


# ==========================================
# Operator
# ==========================================

class RARA_OT_Model_VerticalLine(bpy.types.Operator):
    bl_idname = "rara.model_vertical_line"
    bl_label = "绘制垂直线"
    bl_description = "选中一个顶点，移动鼠标实时显示到最近边的垂足，LMB确认创建垂直线\n【Shift+滚轮】切换垂足等分中点模式"
    bl_options = {'REGISTER', 'UNDO'}

    _mode_names = ["垂足点", "等分中心线"]

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            if event.shift and event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and event.value == 'PRESS':
                self._mode = (self._mode + 1) % 2 if event.type == 'WHEELUPMOUSE' else (self._mode - 1) % 2
                self.update_nearest(context, event)
                self.update_header(context)

            if event.type == 'ESC':
                self.finish(context)
                return {'CANCELLED'}

            if event.type in {'RET', 'LEFTMOUSE'} and event.value == 'PRESS':
                self.apply_line(context)
                self.finish(context)
                return {'FINISHED'}

            if event.type == 'MOUSEMOVE':
                self.update_nearest(context, event)
                return {'RUNNING_MODAL'}

            if event.type in {'MIDDLEMOUSE', 'RIGHTMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} or \
               (event.type == 'MOUSEMOVE' and (event.ctrl or event.shift or event.alt)):
                return {'PASS_THROUGH'}

            return {'RUNNING_MODAL'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"绘制垂直线出错: {str(e)}")
            return {'CANCELLED'}

    def update_header(self, context):
        self._hud_text = f"【绘制垂直线】模式:{self._mode_names[self._mode]} | Shift+滚轮: 切换 | 左键: 确认 | ESC: 退出"

    def invoke(self, context, event):
        self._handle = None
        self._selected_vertex_co = None
        self._nearest_edge = None
        self._vertical_point = None
        self._drawing = False
        self._nearest_edge_indices = None
        self._mode = 0
        obj = context.object
        bpy.ops.mesh.select_mode(type='VERT')
        bm = bmesh.from_edit_mesh(obj.data)
        selected = [v for v in bm.verts if v.select]
        if len(selected) != 1:
            self.report({'WARNING'}, "请只选择一个顶点")
            return {'CANCELLED'}
        self._selected_vertex_co = obj.matrix_world @ selected[0].co
        self._drawing = True
        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(VerticalLinePreview().draw, args, 'WINDOW', 'POST_VIEW')
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_hud_callback, args, 'WINDOW', 'POST_PIXEL')
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        self.update_nearest(context, event)
        return {'RUNNING_MODAL'}

    def finish(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._handle:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            except Exception:
                pass
            self._handle = None
        if hasattr(self, '_handle_2d') and self._handle_2d:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, 'WINDOW')
            except Exception:
                pass
            self._handle_2d = None
        self._drawing = False
        try:
            bpy.ops.mesh.select_mode(type='VERT')
        except Exception:
            pass
        try:
            context.area.tag_redraw()
        except Exception:
            pass

    def update_nearest(self, context, event):
        obj = context.object
        bm = bmesh.from_edit_mesh(obj.data)
        edges = list(bm.edges)
        coord = (event.mouse_region_x, event.mouse_region_y)
        region = context.region
        rv3d = context.region_data

        min_d = float('inf')
        nearest = None
        nearest_e = None

        for e in edges:
            v1 = obj.matrix_world @ e.verts[0].co
            v2 = obj.matrix_world @ e.verts[1].co
            c1 = view3d_utils.location_3d_to_region_2d(region, rv3d, v1)
            c2 = view3d_utils.location_3d_to_region_2d(region, rv3d, v2)
            if c1 and c2:
                pt = self.closest_point_2d(coord, c1, c2)
                d = (mathutils.Vector(coord) - pt).length
                if d < min_d:
                    min_d = d
                    nearest = (v1, v2)
                    nearest_e = e
        self._nearest_edge = nearest
        if nearest_e:
            self._nearest_edge_indices = (nearest_e.verts[0].index, nearest_e.verts[1].index)
        else:
            self._nearest_edge_indices = None

        if nearest:
            p = self._selected_vertex_co
            a, b = nearest
            if self._mode == 0:
                ab = b - a
                ap = p - a
                ab2 = ab.length_squared
                if ab2 > 0:
                    t = max(0, min(1, ap.dot(ab) / ab2))
                    self._vertical_point = a + ab * t
                else:
                    self._vertical_point = a
            else:
                self._vertical_point = (a + b) * 0.5
        else:
            self._vertical_point = None

    def apply_line(self, context):
        obj = context.object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        if not hasattr(self, '_nearest_edge_indices') or self._nearest_edge_indices is None or self._vertical_point is None:
            return

        sel = None
        for v in bm.verts:
            if v.select:
                sel = v
                break
        if sel is None:
            return

        ia, ib = self._nearest_edge_indices
        a = bm.verts[ia]
        b = bm.verts[ib]

        e = None
        for edge in bm.edges:
            if (edge.verts[0] == a and edge.verts[1] == b) or (edge.verts[0] == b and edge.verts[1] == a):
                e = edge
                break
        if e is None:
            return

        a_w = obj.matrix_world @ a.co
        b_w = obj.matrix_world @ b.co
        ab = b_w - a_w
        ap = self._vertical_point - a_w
        ab2 = ab.length_squared
        t = max(0, min(1, ap.dot(ab) / ab2)) if ab2 > 0 else 0

        new_v = bmesh.utils.edge_split(e, a, t)[1]
        new_v.co = obj.matrix_world.inverted() @ self._vertical_point

        target = new_v
        MERGE_THRESHOLD = 0.001
        for v in bm.verts:
            if v is not new_v and (obj.matrix_world @ v.co - self._vertical_point).length < MERGE_THRESHOLD:
                new_v.co = v.co
                bmesh.ops.remove_doubles(bm, verts=[new_v, v], dist=MERGE_THRESHOLD)
                bm.verts.ensure_lookup_table()
                for cv in bm.verts:
                    if (obj.matrix_world @ cv.co - self._vertical_point).length < MERGE_THRESHOLD:
                        target = cv
                        break
                break

        result = bmesh.ops.connect_verts(bm, verts=[sel, target])
        if not result.get("edges"):
            connected = False
            for edge in sel.link_edges:
                if target in edge.verts:
                    connected = True
                    break
            if not connected:
                bm.edges.new([sel, target])
            else:
                self.report({'INFO'}, "连接边已存在")

        bmesh.update_edit_mesh(obj.data)

    @staticmethod
    def closest_point_2d(p, a, b):
        ab = mathutils.Vector(b) - mathutils.Vector(a)
        ab2 = ab.length_squared
        if ab2 == 0:
            return mathutils.Vector(a)
        t = max(0, min(1, (mathutils.Vector(p) - mathutils.Vector(a)).dot(ab) / ab2))
        return mathutils.Vector(a) + ab * t


classes = (RARA_OT_Model_VerticalLine,)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)