# 可视化原点选择器

import bpy
from mathutils import Vector
import gpu
from gpu_extras.batch import batch_for_shader
import blf
import bpy_extras.view3d_utils
import math
from ...constants import BBOX_EDGES
from ...utils.gpu_utils import SHADER, draw_hud_text

# --- 预计算圆的基础顶点（半径为5，16段），提升GPU绘制效率 ---
CIRCLE_SEGMENTS = 16
CIRCLE_RADIUS = 5
BASE_CIRCLE_VERTS = [(0.0, 0.0)] + [
    (CIRCLE_RADIUS * math.cos(2 * math.pi * i / CIRCLE_SEGMENTS), 
     CIRCLE_RADIUS * math.sin(2 * math.pi * i / CIRCLE_SEGMENTS))
    for i in range(CIRCLE_SEGMENTS + 1)
]

# --- Helper Functions ---
def get_bounds_points(min_v, max_v):
    c = (min_v + max_v) / 2
    return {
        "CENTER": c,
        "TOP_CENTER": Vector((c.x, c.y, max_v.z)),
        "BOTTOM_CENTER": Vector((c.x, c.y, min_v.z)),
        "LEFT_CENTER": Vector((min_v.x, c.y, c.z)),
        "RIGHT_CENTER": Vector((max_v.x, c.y, c.z)),
        "FRONT_CENTER": Vector((c.x, min_v.y, c.z)),
        "BACK_CENTER": Vector((c.x, max_v.y, c.z)),
        "CORNER_BLF": Vector((min_v.x, min_v.y, min_v.z)),
        "CORNER_BRF": Vector((max_v.x, min_v.y, min_v.z)),
        "CORNER_BLB": Vector((min_v.x, max_v.y, min_v.z)),
        "CORNER_BRB": Vector((max_v.x, max_v.y, min_v.z)),
        "CORNER_TLF": Vector((min_v.x, min_v.y, max_v.z)),
        "CORNER_TRF": Vector((max_v.x, min_v.y, max_v.z)),
        "CORNER_TLB": Vector((min_v.x, max_v.y, max_v.z)),
        "CORNER_TRB": Vector((max_v.x, max_v.y, max_v.z)),
        "EDGE_B_F": Vector((c.x, min_v.y, min_v.z)),
        "EDGE_B_B": Vector((c.x, max_v.y, min_v.z)),
        "EDGE_B_L": Vector((min_v.x, c.y, min_v.z)),
        "EDGE_B_R": Vector((max_v.x, c.y, min_v.z)),
        "EDGE_T_F": Vector((c.x, min_v.y, max_v.z)),
        "EDGE_T_B": Vector((c.x, max_v.y, max_v.z)),
        "EDGE_T_L": Vector((min_v.x, c.y, max_v.z)),
        "EDGE_T_R": Vector((max_v.x, c.y, max_v.z)),
        "EDGE_V_LF": Vector((min_v.x, min_v.y, c.z)),
        "EDGE_V_RF": Vector((max_v.x, min_v.y, c.z)),
        "EDGE_V_LB": Vector((min_v.x, max_v.y, c.z)),
        "EDGE_V_RB": Vector((max_v.x, max_v.y, c.z)),
    }

def get_bbox_lines_from_pts(pts):
    corners = [
        pts["CORNER_BLF"], pts["CORNER_BRF"], pts["CORNER_BRB"], pts["CORNER_BLB"],
        pts["CORNER_TLF"], pts["CORNER_TRF"], pts["CORNER_TRB"], pts["CORNER_TLB"],
    ]
    lines = []
    for a, b in BBOX_EDGES:
        lines.extend([corners[a], corners[b]])
    return lines

def get_world_aligned_bbox_points(obj):
    if not obj.data or not hasattr(obj.data, 'vertices') or not obj.data.vertices: return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh()
    if not mesh.vertices:
        evaluated_obj.to_mesh_clear()
        return None

    world_matrix = obj.matrix_world
    verts_world = [world_matrix @ v.co for v in mesh.vertices]
    evaluated_obj.to_mesh_clear()

    if not verts_world: return None
    min_v = Vector((min(v.x for v in verts_world), min(v.y for v in verts_world), min(v.z for v in verts_world)))
    max_v = Vector((max(v.x for v in verts_world), max(v.y for v in verts_world), max(v.z for v in verts_world)))
    return get_bounds_points(min_v, max_v)

def get_local_specific_points(obj):
    if not obj.data or not hasattr(obj.data, 'vertices') or not obj.data.vertices: return None
    corners = [Vector(corner) for corner in obj.bound_box]
    min_v = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    max_v = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
    
    local_pts = get_bounds_points(min_v, max_v)
    return {k: obj.matrix_world @ v for k, v in local_pts.items()}

def get_all_extreme_vertices_world(obj):
    if not obj.data or not hasattr(obj.data, 'vertices') or not obj.data.vertices: return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh()
    if not mesh.vertices:
        evaluated_obj.to_mesh_clear()
        return None

    world_matrix = obj.matrix_world
    verts_world = [world_matrix @ v.co for v in mesh.vertices]
    evaluated_obj.to_mesh_clear()

    extremes = {
        "min_x": min(verts_world, key=lambda v: v.x),
        "max_x": max(verts_world, key=lambda v: v.x),
        "min_y": min(verts_world, key=lambda v: v.y),
        "max_y": max(verts_world, key=lambda v: v.y),
        "min_z": min(verts_world, key=lambda v: v.z),
        "max_z": max(verts_world, key=lambda v: v.z),
    }
    return extremes

def get_all_extreme_vertices_local(obj):
    """获取局部坐标系下 X, Y, Z 三个轴向的绝对最高和最低顶点，并转换为世界坐标"""
    if not obj.data or not hasattr(obj.data, 'vertices') or not obj.data.vertices: return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh()
    if not mesh.vertices:
        evaluated_obj.to_mesh_clear()
        return None

    # 获取局部坐标系的顶点
    verts_local = [v.co for v in mesh.vertices]
    
    # 在局部坐标系下寻找极值点
    extremes_local = {
        "min_x": min(verts_local, key=lambda v: v.x),
        "max_x": max(verts_local, key=lambda v: v.x),
        "min_y": min(verts_local, key=lambda v: v.y),
        "max_y": max(verts_local, key=lambda v: v.y),
        "min_z": min(verts_local, key=lambda v: v.z),
        "max_z": max(verts_local, key=lambda v: v.z),
    }
    
    # 将找到的局部极值点转换为世界坐标以便绘制和点击
    world_matrix = obj.matrix_world
    extremes_world = {k: world_matrix @ v for k, v in extremes_local.items()}
    
    evaluated_obj.to_mesh_clear()
    return extremes_world

def calculate_centroid_world(obj):
    if not obj.data or not hasattr(obj.data, 'vertices') or not obj.data.vertices: return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh()
    if not mesh.vertices:
        evaluated_obj.to_mesh_clear()
        return None

    world_matrix = obj.matrix_world
    total_co = sum((world_matrix @ v.co for v in mesh.vertices), Vector((0, 0, 0)))
    count = len(mesh.vertices)
    evaluated_obj.to_mesh_clear()
    return total_co / count if count > 0 else None

# --- Modal Operator ---
class RARA_OT_Model_OriginPicker(bpy.types.Operator):
    bl_idname = "rara.model_origin_picker"
    bl_label = "可视化原点选择器"
    bl_description = "在物体上可视化显示多个关键位置（中心、顶部、底部、左侧、右侧、前端、后端等），点击即可将原点设置到该位置\n支持世界坐标系和本地坐标系两种模式\n\n【左键点击】设置原点到点击位置\n【TAB】切换世界/本地坐标系\n【ESC】退出工具"
    bl_options = {'REGISTER', 'UNDO'}
    
    _handle_view = None
    _points_data = []
    _lines_world = []
    _lines_local = []
    _active_objects = []
    _font_id = 0
    _mouse_loc = (0, 0)
    _display_mode = 'LOCAL'
    
    @classmethod
    def poll(cls, context):
        return context.selected_objects is not None

    def invoke(self, context, event):
        if not context.selected_objects:
            self.report({'WARNING'}, "请选择至少一个对象")
            return {'CANCELLED'}

        self._active_objects = context.selected_objects
        self._points_data = []
        self._lines_world = []
        self._lines_local = []
        self._font_id = 0
        self._mouse_loc = (event.mouse_region_x, event.mouse_region_y)
        self._display_mode = 'LOCAL'

        self.calculate_all_points(context)

        if not self._points_data:
            self.report({'WARNING'}, "未找到可用的原点位置")
            return {'CANCELLED'}

        args = (self, context)
        self._handle_view = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_view, args, 'WINDOW', 'POST_PIXEL')
        
        context.window_manager.modal_handler_add(self)
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        self._hud_text = "【原点选择器】左键: 设置原点 | TAB: 切换 局部/世界 | ESC/右键: 取消"
        self.report({'INFO'}, "当前为【局部模式】，按 TAB 键切换为世界模式")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            if event.type == 'MOUSEMOVE':
                self._mouse_loc = (event.mouse_region_x, event.mouse_region_y)

            if event.type == 'TAB' and event.value == 'PRESS':
                self._display_mode = 'WORLD' if self._display_mode == 'LOCAL' else 'LOCAL'
                self._points_data.clear()
                self._lines_world.clear()
                self._lines_local.clear()
                self.calculate_all_points(context)
                
                mode_name = "世界" if self._display_mode == 'WORLD' else "局部"
                self.report({'INFO'}, f"已切换到【{mode_name}模式】")
                return {'RUNNING_MODAL'}

            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                clicked_point = self.get_hovered_point(click_radius=15)
                if clicked_point:
                    self.apply_origin_based_on_point(context, clicked_point) 
                    self.report({'INFO'}, f"已将原点设置到 {clicked_point['label']}")
                    self.cancel(context)
                    return {'FINISHED'}
                else:
                    self.report({'INFO'}, "未点击到有效点，请重试或按ESC取消")
                    return {'RUNNING_MODAL'} 

            if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
                self.cancel(context)
                self.report({'INFO'}, "操作已取消")
                return {'CANCELLED'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.cancel(context)
            self.report({'ERROR'}, f"原点选择器出错: {str(e)}")
            return {'CANCELLED'}

    def cancel(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._handle_view:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle_view, 'WINDOW')
            except Exception:
                pass
            self._handle_view = None
        self._points_data.clear()
        self._lines_world.clear()
        self._lines_local.clear()
        self._active_objects.clear()

    def update_screen_locations(self, context):
        region, rv3d = context.region, context.space_data.region_3d
        for p in self._points_data:
            p['screen_loc'] = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, p['location'])

    def get_hovered_point(self, click_radius=15):
        closest_point = None
        min_dist = click_radius ** 2
        mx, my = self._mouse_loc
        
        for p in self._points_data:
            sl = p.get('screen_loc')
            if sl:
                dist_sq = (sl[0] - mx)**2 + (sl[1] - my)**2
                if dist_sq < min_dist:
                    min_dist = dist_sq
                    closest_point = p
        return closest_point

    def apply_origin_based_on_point(self, context, clicked_point):
        original_mode = context.mode
        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        orig_loc = context.scene.cursor.location.copy()
        orig_rot = context.scene.cursor.rotation_euler.copy()

        p_type = clicked_point['type']
        p_key = clicked_point['key']
        target_loc = clicked_point['location']

        if p_type == 'GLOBAL':
            context.scene.cursor.location = target_loc
            bpy.ops.object.select_all(action='DESELECT')
            for obj in self._active_objects: obj.select_set(True)
            if self._active_objects: context.view_layer.objects.active = self._active_objects[0]
            bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
        else:
            for obj in self._active_objects:
                if obj.type != 'MESH': continue
                
                obj_loc = None
                if p_type == 'CENTROID':
                    obj_loc = calculate_centroid_world(obj)
                elif p_type == 'EXTREME':
                    extremes = get_all_extreme_vertices_world(obj)
                    if extremes: obj_loc = extremes.get(p_key)
                elif p_type == 'LOCAL_EXTREME':
                    extremes = get_all_extreme_vertices_local(obj)
                    if extremes: obj_loc = extremes.get(p_key)
                elif p_type == 'WORLD_BBOX':
                    pts = get_world_aligned_bbox_points(obj)
                    if pts: obj_loc = pts.get(p_key)
                elif p_type == 'LOCAL_BBOX':
                    pts = get_local_specific_points(obj)
                    if pts: obj_loc = pts.get(p_key)
                elif p_type == 'PARENT':
                    if obj.parent: obj_loc = obj.parent.matrix_world.translation.copy()

                if obj_loc:
                    bpy.ops.object.select_all(action='DESELECT')
                    obj.select_set(True)
                    context.view_layer.objects.active = obj
                    context.scene.cursor.location = obj_loc
                    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')

        context.scene.cursor.location = orig_loc
        context.scene.cursor.rotation_euler = orig_rot
        
        bpy.ops.object.select_all(action='DESELECT')
        for obj in self._active_objects: obj.select_set(True)
        if self._active_objects: context.view_layer.objects.active = self._active_objects[0]

        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=original_mode)

    def calculate_all_points(self, context):
        def add_pt(loc, lbl, col, p_type='GLOBAL', p_key=None):
            for existing in self._points_data:
                if (existing['location'] - loc).length < 0.001:
                    if lbl not in existing['label']:
                        existing['label'] += f" / {lbl}"
                    return
            self._points_data.append({'location': loc, 'label': lbl, 'color': col, 'type': p_type, 'key': p_key})
        
        add_pt(context.scene.cursor.location.copy(), "光标位置", (1.0, 0.0, 0.0, 1.0), 'GLOBAL')
        add_pt(Vector((0, 0, 0)), "世界原点", (0.0, 1.0, 0.0, 1.0), 'GLOBAL')

        if len(self._active_objects) > 1:
            global_min = Vector((float('inf'), float('inf'), float('inf')))
            global_max = Vector((float('-inf'), float('-inf'), float('-inf')))
            valid_global = False
            for obj in self._active_objects:
                if obj.type != 'MESH': continue
                corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
                for v in corners:
                    global_min.x = min(global_min.x, v.x); global_min.y = min(global_min.y, v.y); global_min.z = min(global_min.z, v.z)
                    global_max.x = max(global_max.x, v.x); global_max.y = max(global_max.y, v.y); global_max.z = max(global_max.z, v.z)
                valid_global = True
            if valid_global:
                global_center = (global_min + global_max) / 2
                add_pt(global_center, "整体选择中心", (1.0, 1.0, 1.0, 1.0), 'GLOBAL')

        pt_names = {
            "CENTER": "中心", "TOP_CENTER": "顶部中心", "BOTTOM_CENTER": "底部中心",
            "LEFT_CENTER": "左侧中心", "RIGHT_CENTER": "右侧中心",
            "FRONT_CENTER": "前面中心", "BACK_CENTER": "后面中心",
            "CORNER_BLF": "底左前", "CORNER_BRF": "底右前", "CORNER_BLB": "底左后", "CORNER_BRB": "底右后",
            "CORNER_TLF": "顶左前", "CORNER_TRF": "顶右前", "CORNER_TLB": "顶左后", "CORNER_TRB": "顶右后",
            "EDGE_B_F": "底前", "EDGE_B_B": "底后", "EDGE_B_L": "底左", "EDGE_B_R": "底右",
            "EDGE_T_F": "顶前", "EDGE_T_B": "顶后", "EDGE_T_L": "顶左", "EDGE_T_R": "顶右",
            "EDGE_V_LF": "左前", "EDGE_V_RF": "右前", "EDGE_V_LB": "左后", "EDGE_V_RB": "右后",
        }

        for obj in self._active_objects:
            if obj.type != 'MESH': continue

            if obj.parent:
                add_pt(obj.parent.matrix_world.translation.copy(), f"父级原点", (0.0, 0.0, 1.0, 1.0), 'PARENT')

            centroid = calculate_centroid_world(obj)
            if centroid:
                add_pt(centroid, f"质心", (1.0, 1.0, 0.0, 1.0), 'CENTROID')

            if self._display_mode == 'WORLD':
                extremes = get_all_extreme_vertices_world(obj)
                if extremes:
                    add_pt(extremes["min_z"], f"绝对最低点(Z)", (0.8, 0.2, 0.8, 1.0), 'EXTREME', 'min_z')
                    add_pt(extremes["max_z"], f"绝对最高点(Z)", (0.8, 0.2, 0.8, 1.0), 'EXTREME', 'max_z')
                    add_pt(extremes["min_x"], f"绝对最左点(X)", (0.8, 0.2, 0.8, 1.0), 'EXTREME', 'min_x')
                    add_pt(extremes["max_x"], f"绝对最右点(X)", (0.8, 0.2, 0.8, 1.0), 'EXTREME', 'max_x')
                    add_pt(extremes["min_y"], f"绝对最前点(Y)", (0.8, 0.2, 0.8, 1.0), 'EXTREME', 'min_y')
                    add_pt(extremes["max_y"], f"绝对最后点(Y)", (0.8, 0.2, 0.8, 1.0), 'EXTREME', 'max_y')

                world_pts = get_world_aligned_bbox_points(obj)
                if world_pts:
                    for key, loc in world_pts.items():
                        add_pt(loc, f"{pt_names[key]} (世界)", (1.0, 0.5, 0.0, 1.0), 'WORLD_BBOX', key)
                    self._lines_world.extend(get_bbox_lines_from_pts(world_pts))

            elif self._display_mode == 'LOCAL':
                local_extremes = get_all_extreme_vertices_local(obj)
                if local_extremes:
                    add_pt(local_extremes["min_z"], f"局部最低点(Z)", (0.8, 0.2, 0.8, 1.0), 'LOCAL_EXTREME', 'min_z')
                    add_pt(local_extremes["max_z"], f"局部最高点(Z)", (0.8, 0.2, 0.8, 1.0), 'LOCAL_EXTREME', 'max_z')
                    add_pt(local_extremes["min_x"], f"局部最左点(X)", (0.8, 0.2, 0.8, 1.0), 'LOCAL_EXTREME', 'min_x')
                    add_pt(local_extremes["max_x"], f"局部最右点(X)", (0.8, 0.2, 0.8, 1.0), 'LOCAL_EXTREME', 'max_x')
                    add_pt(local_extremes["min_y"], f"局部最前点(Y)", (0.8, 0.2, 0.8, 1.0), 'LOCAL_EXTREME', 'min_y')
                    add_pt(local_extremes["max_y"], f"局部最后点(Y)", (0.8, 0.2, 0.8, 1.0), 'LOCAL_EXTREME', 'max_y')

                local_pts = get_local_specific_points(obj)
                if local_pts:
                    for key, loc in local_pts.items():
                        add_pt(loc, f"{pt_names[key]} (局部)", (0.0, 1.0, 1.0, 1.0), 'LOCAL_BBOX', key)
                    self._lines_local.extend(get_bbox_lines_from_pts(local_pts))

    def draw_callback_view(self, op, context):
        self.update_screen_locations(context)
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(2.0)
        SHADER.bind()
        gpu.state.blend_set('ALPHA')

        region = context.region
        rv3d = context.space_data.region_3d

        active_lines = self._lines_world if self._display_mode == 'WORLD' else self._lines_local
        if active_lines:
            line_verts = []
            for i in range(0, len(active_lines), 2):
                if i + 1 >= len(active_lines): break
                p1 = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, active_lines[i])
                p2 = bpy_extras.view3d_utils.location_3d_to_region_2d(region, rv3d, active_lines[i + 1])
                if p1 and p2:
                    line_verts.extend([(p1[0], p1[1]), (p2[0], p2[1])])
            if line_verts:
                SHADER.uniform_float("color", (1.0, 0.8, 0.0, 0.5) if self._display_mode == 'WORLD' else (0.0, 1.0, 1.0, 0.5))
                batch = batch_for_shader(SHADER, 'LINES', {"pos": line_verts})
                batch.draw(SHADER)

        gpu.state.line_width_set(5.0)

        for p in self._points_data:
            sl = p.get('screen_loc')
            if not sl: continue

            verts = [(sl[0] + v[0], sl[1] + v[1]) for v in BASE_CIRCLE_VERTS]
            SHADER.uniform_float("color", p['color'])
            batch = batch_for_shader(SHADER, 'TRI_FAN', {"pos": verts})
            batch.draw(SHADER)

        hovered = self.get_hovered_point(click_radius=15)
        if hovered and hovered.get('screen_loc'):
            sl = hovered['screen_loc']

            verts_hover = [(sl[0] + v[0]*1.5, sl[1] + v[1]*1.5) for v in BASE_CIRCLE_VERTS]
            SHADER.uniform_float("color", (1.0, 1.0, 1.0, 0.8))
            batch_hover = batch_for_shader(SHADER, 'TRI_FAN', {"pos": verts_hover})
            batch_hover.draw(SHADER)

            blf.position(self._font_id, sl[0] + 12, sl[1] + 5, 0)
            blf.size(self._font_id, 16)
            blf.enable(self._font_id, blf.SHADOW)
            blf.shadow(self._font_id, 3, 0.0, 0.0, 0.0, 0.8)
            blf.color(self._font_id, *hovered['color'])
            blf.draw(self._font_id, hovered['label'])
            blf.disable(self._font_id, blf.SHADOW)

        gpu.state.blend_set('NONE')

        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)


classes = (
    RARA_OT_Model_OriginPicker,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

