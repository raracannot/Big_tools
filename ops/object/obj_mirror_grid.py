# 绘制镜像

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
import mathutils
from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
from ...utils.gpu_utils import SHADER, draw_hud_text
from ...constants import BBOX_EDGES

# --- 绘制相关数据 ---
plane_vertices = [
    (-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)
]
plane_indices = [(0, 1, 2), (2, 3, 0)]


def draw_callback_px(self, context):
    if not self.active_obj:
        return

    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')

    matrix_world = self.active_obj.matrix_world.copy()
    loc = matrix_world.translation

    if self.is_global:
        rot_matrix = mathutils.Matrix.Identity(4)
        rot_matrix.translation = loc
    else:
        rot_matrix = matrix_world.copy()
        # 移除缩放，只保留旋转和位置
        rot_matrix = mathutils.Matrix.LocRotScale(loc, rot_matrix.to_quaternion(), None)

    scale_mat = mathutils.Matrix.Scale(self.plane_size, 4)

    # 定义三个平面的基础颜色和旋转 (X: 红色, Y: 绿色, Z: 蓝色)
    planes = [
        # YZ Plane (X轴法线) - 红色
        (mathutils.Euler((0, 1.5708, 0), 'XYZ').to_matrix().to_4x4(), (1.0, 0.0, 0.0)),
        # XZ Plane (Y轴法线) - 绿色
        (mathutils.Euler((1.5708, 0, 0), 'XYZ').to_matrix().to_4x4(), (0.0, 1.0, 0.0)),
        # XY Plane (Z轴法线) - 蓝色
        (mathutils.Euler((0, 0, 0), 'XYZ').to_matrix().to_4x4(), (0.0, 0.0, 1.0))
    ]

    for i, (rot, base_color) in enumerate(planes):
        final_matrix = rot_matrix @ rot @ scale_mat
        
        # 转换顶点到世界坐标
        world_verts = [final_matrix @ mathutils.Vector(v) for v in plane_vertices]
        
        # 判断当前平面是否被鼠标悬停，悬停时透明度为0.9（几乎不透明），否则为0.3
        alpha = 0.9 if i == self.hovered_axis else 0.3
        color_with_alpha = (*base_color, alpha)
        
        batch = batch_for_shader(SHADER, 'TRIS', {"pos": world_verts}, indices=plane_indices)
        SHADER.bind()
        SHADER.uniform_float("color", color_with_alpha)
        batch.draw(SHADER)

    # ==========================================
    # 绘制悬停时的镜像边界框预览
    if self.hovered_axis is not None:
        gpu.state.line_width_set(2.0)
        
        # 提取当前悬停轴的镜像缩放向量
        axis_index = self.hovered_axis
        mirror_scale_vec = mathutils.Vector(([1,0,0], [0,1,0], [0,0,1])[axis_index])
        mirror_mat = mathutils.Matrix.Scale(-1, 4, mirror_scale_vec)
        
        # 提前计算好镜像的变换基底矩阵
        if self.is_global:
            pivot = self.active_obj.matrix_world.translation
            offset_mat = mathutils.Matrix.Translation(pivot)
            inv_offset_mat = mathutils.Matrix.Translation(-pivot)
            transform_base = offset_mat @ mirror_mat @ inv_offset_mat
        else:
            ref_mat = self.active_obj.matrix_world.copy()
            ref_inv = ref_mat.inverted()
            transform_base = ref_mat @ mirror_mat @ ref_inv

        # 遍历所有目标物体，绘制其镜像后的边界框
        for obj in self.target_objs:
            # 计算该物体镜像后的世界矩阵
            mirrored_matrix = transform_base @ obj.matrix_world
            
            # 获取局部边界框并转换到镜像后的世界坐标
            local_bbox = [mathutils.Vector(v) for v in obj.bound_box]
            world_verts = [mirrored_matrix @ v for v in local_bbox]
            
            # 绘制黄色线框预览
            batch = batch_for_shader(SHADER, 'LINES', {"pos": world_verts}, indices=BBOX_EDGES)
            SHADER.bind()
            SHADER.uniform_float("color", (1.0, 0.8, 0.0, 1.0)) # 橙黄色
            batch.draw(SHADER)
            
        gpu.state.line_width_set(1.0)
    # ==========================================

    gpu.state.blend_set('NONE')


def draw_hud_callback_2d(self, context):
    hud = getattr(self, '_hud_text', None)
    if hud:
        draw_hud_text(hud, context)


class RARA_OT_Model_MirrorGrid(bpy.types.Operator):
    """绘制镜像模态操作"""
    bl_idname = "rara.model_mirror_grid"
    bl_label = "绘制镜像"
    bl_description = "根据活动物体的坐标轴为参考，对选中物体执行对称镜像\n支持全局坐标系和本地坐标系两种模式\n\n【G】切换全局/本地坐标系\n【回车/左键】确认执行镜像\n【ESC/右键】取消"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.area.type == 'VIEW_3D' 
                and context.selected_objects)

    def invoke(self, context, event):
        if not context.active_object:
            self.report({'WARNING'}, "没有活动物体")
            return {'CANCELLED'}

        self.active_obj = context.active_object
        self.target_objs = [obj for obj in context.selected_objects if obj != self.active_obj]
        
        if not self.target_objs:
            self.report({'WARNING'}, "请选中至少一个待镜像物体（除活动物体外）")
            return {'CANCELLED'}

        self.is_global = False
        self.hovered_axis = None  # 记录当前鼠标悬停的轴(0:X, 1:Y, 2:Z)
        
        # 计算合适的平面大小 (活动对象边界框最长边 * 1.5)
        max_edge = max(self.active_obj.dimensions)
        if max_edge < 0.001:  # 防止物体没有体积（如Empty或单顶点）
            max_edge = 1.0
            
        # 因为基础平面顶点是从 -1 到 1，边长为 2，
        # 目标边长 = max_edge * 1.5，所以 scale (=plane_size) = 目标边长 / 2
        self.plane_size = (max_edge * 1.5) / 2.0

        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_px, args, 'WINDOW', 'POST_VIEW')
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_hud_callback_2d, args, 'WINDOW', 'POST_PIXEL')
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        mode = "全局" if self.is_global else "本地"
        self._hud_text = f"【绘制镜像】坐标系: {mode} | G: 切换 | 左键: 执行 | ESC/右键: 取消"

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            if event.type == 'TAB' and event.value == 'PRESS':
                self.is_global = not self.is_global
                self.update_header(context)
                return {'RUNNING_MODAL'}

            elif event.type == 'MOUSEMOVE':
                region = context.region
                rv3d = context.region_data
                coord = event.mouse_region_x, event.mouse_region_y
                ray_origin = region_2d_to_origin_3d(region, rv3d, coord)
                ray_dir = region_2d_to_vector_3d(region, rv3d, coord)
                self.hovered_axis = self.get_clicked_plane(ray_origin, ray_dir)

            elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                if self.hovered_axis is not None:
                    if event.ctrl and event.alt:
                        mode = 'LINKED_COPY'
                    elif event.ctrl:
                        mode = 'COPY'
                    else:
                        mode = 'MIRROR'
                    self.execute_mirror(context, self.hovered_axis, mode)
                    self.cleanup(context)
                    return {'FINISHED'}

            elif event.type in {'RIGHTMOUSE', 'ESC'}:
                self.cleanup(context)
                return {'CANCELLED'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.cleanup(context)
            self.report({'ERROR'}, f"绘制镜像出错: {str(e)}")
            return {'CANCELLED'}

    def cleanup(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if hasattr(self, '_handle') and self._handle:
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

    def get_clicked_plane(self, ray_origin, ray_dir):
        loc = self.active_obj.matrix_world.translation
        if self.is_global:
            rot_matrix = mathutils.Matrix.Identity(3)
        else:
            rot_matrix = self.active_obj.matrix_world.to_3x3().normalized()

        # 获取当前坐标系下的 X, Y, Z 轴方向向量
        axis_x = rot_matrix @ mathutils.Vector((1, 0, 0))
        axis_y = rot_matrix @ mathutils.Vector((0, 1, 0))
        axis_z = rot_matrix @ mathutils.Vector((0, 0, 1))

        # 定义三个平面的信息：(轴索引, 法线向量, 平面上的基向量U, 平面上的基向量V)
        planes_info = [
            (0, axis_x, axis_y, axis_z), # YZ Plane -> X axis normal
            (1, axis_y, axis_x, axis_z), # XZ Plane -> Y axis normal
            (2, axis_z, axis_x, axis_y)  # XY Plane -> Z axis normal
        ]

        closest_axis = None
        min_dist = float('inf')

        for axis, normal, u, v in planes_info:
            # 射线与平面相交
            intersect = mathutils.geometry.intersect_line_plane(ray_origin, ray_origin + ray_dir * 10000, loc, normal)
            if intersect:
                # 计算交点相对于中心的向量
                vec_to_center = intersect - loc
                
                # 将向量投影到平面的两个基向量上，获取局部坐标
                local_u = vec_to_center.dot(u)
                local_v = vec_to_center.dot(v)
                
                # 精确判断是否在正方形范围内
                if abs(local_u) <= self.plane_size and abs(local_v) <= self.plane_size:
                    dist_to_cam = (intersect - ray_origin).length
                    if dist_to_cam < min_dist:
                        min_dist = dist_to_cam
                        closest_axis = axis

        return closest_axis

    def execute_mirror(self, context, axis_index, mode):
        # 0: X, 1: Y, 2: Z
        scale = [1, 1, 1]
        scale[axis_index] = -1
        
        objs_to_process = self.target_objs
        
        # 如果是复制模式，则生成新物体
        if mode in {'COPY', 'LINKED_COPY'}:
            new_objs = []
            for obj in self.target_objs:
                new_obj = obj.copy()
                
                # 普通复制时，连同网格数据一起复制；关联复制则跳过此步，共享原数据
                if mode == 'COPY' and obj.data:
                    new_obj.data = obj.data.copy()
                    
                context.collection.objects.link(new_obj)
                new_objs.append(new_obj)
            
            # 取消选中所有，重新选中新复制的物体和活动物体
            bpy.ops.object.select_all(action='DESELECT')
            for obj in new_objs:
                obj.select_set(True)
            self.active_obj.select_set(True)
            context.view_layer.objects.active = self.active_obj
            
            objs_to_process = new_objs

        if self.is_global:
            mirror_mat = mathutils.Matrix.Scale(-1, 4, mathutils.Vector(([1,0,0], [0,1,0], [0,0,1])[axis_index]))
            pivot = self.active_obj.matrix_world.translation
            offset_mat = mathutils.Matrix.Translation(pivot)
            inv_offset_mat = mathutils.Matrix.Translation(-pivot)
            
            for obj in objs_to_process:
                obj.matrix_world = offset_mat @ mirror_mat @ inv_offset_mat @ obj.matrix_world
        else:
            # 局部镜像
            ref_mat = self.active_obj.matrix_world.copy()
            ref_inv = ref_mat.inverted()
            mirror_mat = mathutils.Matrix.Scale(-1, 4, mathutils.Vector(([1,0,0], [0,1,0], [0,0,1])[axis_index]))
            
            for obj in objs_to_process:
                local_mat = ref_inv @ obj.matrix_world
                obj.matrix_world = ref_mat @ mirror_mat @ local_mat


classes = (
    RARA_OT_Model_MirrorGrid,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

