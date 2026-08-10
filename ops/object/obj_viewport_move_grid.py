# 沿视图前后移动

import bpy
import mathutils
import blf  
import gpu  
from gpu_extras.batch import batch_for_shader
from bpy_extras import view3d_utils
from ...utils.gpu_utils import SHADER, draw_hud_text

BASE_STEP = 0.1

def get_current_step(event):
    if event.shift:
        return BASE_STEP * 0.1
    elif event.ctrl:
        return BASE_STEP * 10
    else:
        return BASE_STEP

def get_obj_center_and_max_dim(obj):
    mw = obj.matrix_world
    bbox = [mathutils.Vector(b) for b in obj.bound_box]
    center = sum((mw @ b for b in bbox), mathutils.Vector()) / 8
    
    dim_x = (bbox[4].x - bbox[0].x) * mw.to_scale().x
    dim_y = (bbox[2].y - bbox[0].y) * mw.to_scale().y
    dim_z = (bbox[1].z - bbox[0].z) * mw.to_scale().z
    max_dim = max(dim_x, dim_y, dim_z)
    
    if max_dim < 0.001:
        max_dim = 1.0
        center = mw.translation
        
    return center, max_dim

def move_object_along_view(context, obj, step, pure_translation=False):
    rv3d = context.region_data
    if not rv3d:
        return False
    
    cam_pos = rv3d.view_matrix.inverted().translation
    view_forward = -rv3d.view_matrix.inverted().col[2].xyz.normalized()
    
    if rv3d.is_perspective:
        center, _ = get_obj_center_and_max_dim(obj)
        ray_vec = center - cam_pos
        current_dist = ray_vec.length
        
        if current_dist <= 0.01:
            current_dist = 0.01
            ray_dir = view_forward
        else:
            ray_dir = ray_vec.normalized()
            
        target_dist = current_dist + step
        target_dist = max(0.01, target_dist)
        
        if pure_translation:
            translation_vector = ray_dir * step
            mat_trans = mathutils.Matrix.Translation(translation_vector)
            obj.matrix_world = mat_trans @ obj.matrix_world
        else:
            scale_factor = target_dist / current_dist
            mat_trans_to_cam = mathutils.Matrix.Translation(-cam_pos)
            mat_scale = mathutils.Matrix.Scale(scale_factor, 4)
            mat_trans_back = mathutils.Matrix.Translation(cam_pos)
            obj.matrix_world = mat_trans_back @ mat_scale @ mat_trans_to_cam @ obj.matrix_world
    else:
        translation_vector = view_forward * step
        mat_trans = mathutils.Matrix.Translation(translation_vector)
        obj.matrix_world = mat_trans @ obj.matrix_world
    
    return True

def get_object_to_viewpoint_distance(context, obj):
    if not obj or not context.region_data:
        return 0.0
    viewpoint = context.region_data.view_matrix.inverted().translation
    center, _ = get_obj_center_and_max_dim(obj)
    distance = (center - viewpoint).length
    return round(distance, 3)

def draw_distance_text(self, context):
    if context.region != self.active_region:
        return

    hud = getattr(self, '_hud_text', None)
    if hud:
        draw_hud_text(hud, context)

    obj = context.active_object
    if not obj or not context.region_data:
        return
    
    center, _ = get_obj_center_and_max_dim(obj)
    region = context.region
    rv3d = context.region_data
    center_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, center)
    
    if not center_2d:
        return
    
    distance = get_object_to_viewpoint_distance(context, obj)
    
    mode_str = "[纯平移(近大远小)]" if self.pure_translation_mode else "[平移+缩放(锁定视觉大小)]"
    text = f"{mode_str} 距离视点：{distance}m"
    
    font_id = 0
    blf.size(font_id, 16)
    if self.pure_translation_mode:
        blf.color(font_id, 1.0, 0.8, 0.2, 1.0) 
    else:
        blf.color(font_id, 0.2, 0.8, 1.0, 1.0) 
        
    text_width, text_height = blf.dimensions(font_id, text)
    x = center_2d.x - text_width / 2
    y = center_2d.y + text_height / 2 + 10
    blf.position(font_id, x, y - 4, 0)
    blf.draw(font_id, text)

def draw_virtual_plane(self, context):
    obj = context.active_object
    if not obj or not self.active_region_data:
        return

    center, max_dim = get_obj_center_and_max_dim(obj)
    hs = max( max_dim * 100 , 100)
    
    view_inv = self.active_region_data.view_matrix.inverted()
    cam_pos = view_inv.translation
    cam_right = view_inv.col[0].xyz.normalized()
    cam_up = view_inv.col[1].xyz.normalized()

    # --- 1. 绘制虚拟平面（所有视口可见） ---
    v0 = center - cam_right * hs - cam_up * hs
    v1 = center + cam_right * hs - cam_up * hs
    v2 = center + cam_right * hs + cam_up * hs
    v3 = center - cam_right * hs + cam_up * hs
    coords = [v0, v1, v2, v3]

    indices_tris = [(0, 1, 2), (0, 2, 3)]
    indices_lines = [(0, 1), (1, 2), (2, 3), (3, 0)]

    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')

    SHADER.bind()
    SHADER.uniform_float("color", (0.2, 0.8, 1.0, 0.15))
    batch_fill = batch_for_shader(SHADER, 'TRIS', {"pos": coords}, indices=indices_tris)
    batch_fill.draw(SHADER)

    SHADER.uniform_float("color", (0.2, 0.8, 1.0, 0.8))
    batch_lines = batch_for_shader(SHADER, 'LINES', {"pos": coords}, indices=indices_lines)
    batch_lines.draw(SHADER)

    if context.region != self.active_region:
        line_coords = [cam_pos, center]
        SHADER.uniform_float("color", (1.0, 0.6, 0.1, 1.0))
        batch_ray = batch_for_shader(SHADER, 'LINES', {"pos": line_coords})
        batch_ray.draw(SHADER)

        gpu.state.point_size_set(6.0)
        SHADER.uniform_float("color", (1.0, 0.2, 0.2, 1.0))
        batch_cam_pt = batch_for_shader(SHADER, 'POINTS', {"pos": [cam_pos]})
        batch_cam_pt.draw(SHADER)

    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')


class RARA_OT_Model_ViewportMoveGrid(bpy.types.Operator):
    bl_idname = "rara.model_viewport_move_grid"
    bl_label = "沿视图前后移动网格"
    bl_description = "沿视口的深度方向移动物体，实时显示移动距离和方向提示\n支持移动距离数字显示，方便精确定位\n\n【鼠标滚轮】沿视口方向移动\n【Shift】慢速移动\n【Ctrl】快速移动\n【TAB】切换模式\n【ESC】退出工具"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.active_object and context.active_object.mode == 'OBJECT' and context.area.type == 'VIEW_3D')

    def invoke(self, context, event):
        if not context.active_object or len(context.selected_objects) < 1:
            self.report({'WARNING'}, "操作失败：请先选中至少一个物体！")
            return {'CANCELLED'}
             
        self.pure_translation_mode = False
        
        self.active_region = context.region
        self.active_region_data = context.region_data
        
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            draw_distance_text, (self, context), 'WINDOW', 'POST_PIXEL'
        )
        self._handle_3d = bpy.types.SpaceView3D.draw_handler_add(
            draw_virtual_plane, (self, context), 'WINDOW', 'POST_VIEW'
        )
        
        context.window_manager.modal_handler_add(self)

        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        self._hud_text = "【沿视口移动】滚轮: 前后移动 | Shift: 慢速 | Ctrl: 快速 | TAB: 切换模式 | ESC/右键: 退出"
        
        self.redraw_all_views(context)
        return {'RUNNING_MODAL'}

    def redraw_all_views(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                self.redraw_all_views(context)
                return {'PASS_THROUGH'}
            if event.type in {'ESC', 'RIGHTMOUSE'}:
                self.cleanup(context)
                return {'FINISHED'}
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                self.cleanup(context)
                return {'FINISHED'}

            if event.type == 'TAB' and event.value == 'PRESS':
                self.pure_translation_mode = not self.pure_translation_mode
                self.redraw_all_views(context)
                return {'RUNNING_MODAL'}

            if event.type in {'WHEELDOWNMOUSE', 'WHEELUPMOUSE'}:
                mouse_x = event.mouse_x
                mouse_y = event.mouse_y
                r = self.active_region
                
                is_inside_active_region = (r.x <= mouse_x <= r.x + r.width) and (r.y <= mouse_y <= r.y + r.height)
                
                if not is_inside_active_region:
                    return {'PASS_THROUGH'}
                
                step = get_current_step(event) if event.type == 'WHEELUPMOUSE' else -get_current_step(event)
                
                for obj in context.selected_objects:
                    move_object_along_view(context, obj, step, self.pure_translation_mode)
                
                self.redraw_all_views(context)
                return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.cleanup(context)
            self.report({'ERROR'}, f"视口移动出错: {str(e)}")
            return {'CANCELLED'}
    
    def cleanup(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if hasattr(self, '_handle_2d') and self._handle_2d is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, 'WINDOW')
            except Exception:
                pass
            self._handle_2d = None
        if hasattr(self, '_handle_3d') and self._handle_3d is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle_3d, 'WINDOW')
            except Exception:
                pass
            self._handle_3d = None
        self.redraw_all_views(context)

classes = (
    RARA_OT_Model_ViewportMoveGrid,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

