# 选择闭合区域

import bpy
import bmesh
import gpu
from gpu_extras.batch import batch_for_shader
import mathutils
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils
import random
from ...utils.gpu_utils import SHADER, draw_hud_text

def get_random_color():
    # 生成明亮且饱和的随机颜色
    h = random.random()
    s = 0.8 + random.random() * 0.2
    v = 0.8 + random.random() * 0.2
    c = mathutils.Color()
    c.hsv = h, s, v
    return (c.r, c.g, c.b, 0.4) # 带有透明度

class RARA_OT_Model_SelectRegion(bpy.types.Operator):
    bl_idname = "rara.model_select_region"
    bl_label = "选择闭合区域"
    bl_description = "以选中的循环边为边界，快速选中该边界内部的所有元素\n支持在多个独立循环区域中同时选择\n采用 BVH 树算法保证选择精确度\n\n【左键】单选 | 【Shift+左键】加选 | 【Ctrl+左键】减选 | 【ESC/回车】确认退出"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.active_object and context.mode == 'EDIT_MESH'

    def invoke(self, context, event):
        self.obj = context.active_object
        self.bm = bmesh.from_edit_mesh(self.obj.data)
        
        self.bm.verts.ensure_lookup_table()
        self.bm.edges.ensure_lookup_table()
        self.bm.faces.ensure_lookup_table()

        # 1. 记录并定义边界（隔离墙）
        self.barrier_edges = set(e for e in self.bm.edges if e.select)
        self.barrier_faces = set(f for f in self.bm.faces if f.select)
        
        # 如果选中了面，面的边界边也作为隔离墙
        for f in self.barrier_faces:
            for e in f.edges:
                self.barrier_edges.add(e)

        # 取消所有选中状态
        for v in self.bm.verts: v.select = False
        for e in self.bm.edges: e.select = False
        for f in self.bm.faces: f.select = False

        # 2. 泛洪算法 (Flood Fill) 划分面域
        self.regions = [] # 存储每个面域包含的 face 列表
        self.region_colors = []
        self.face_to_region = {} # 映射：face -> region_index
        
        visited = set()
        
        for f in self.bm.faces:
            if f in self.barrier_faces or f in visited:
                continue
                
            # 发现新面域
            current_region = []
            queue = [f]
            visited.add(f)
            
            while queue:
                curr_f = queue.pop(0)
                current_region.append(curr_f)
                
                for loop in curr_f.loops:
                    edge = loop.edge
                    if edge in self.barrier_edges:
                        continue # 遇到隔离墙，停止蔓延
                        
                    # 获取相邻面
                    adj_face = loop.link_loop_radial_next.face if loop.link_loop_radial_next else None
                    if adj_face and adj_face != curr_f and adj_face not in visited and adj_face not in self.barrier_faces:
                        visited.add(adj_face)
                        queue.append(adj_face)
                        
            if current_region:
                region_idx = len(self.regions)
                self.regions.append(current_region)
                self.region_colors.append(get_random_color())
                for rf in current_region:
                    self.face_to_region[rf] = region_idx

        if not self.regions:
            self.report({'WARNING'}, "没有找到可划分的面域")
            return {'CANCELLED'}

        # 3. 构建 GPU 绘制批次
        self.batches = []
        for region in self.regions:
            coords = []
            indices = []
            idx_offset = 0
            
            for f in region:
                f_verts = f.verts[:]
                f_coords = [v.co for v in f_verts]
                
                if len(f_verts) == 3:
                    tri_indices = [(0, 1, 2)]
                else:
                    normal = f.normal
                    if abs(normal.x) < 0.9:
                        tangent = mathutils.Vector((1, 0, 0)).cross(normal).normalized()
                    else:
                        tangent = mathutils.Vector((0, 1, 0)).cross(normal).normalized()
                    bitangent = normal.cross(tangent).normalized()
                    center = f.calc_center_median()
                    coords_2d = []
                    for v_co in f_coords:
                        rel = v_co - center
                        coords_2d.append((rel.dot(tangent), rel.dot(bitangent)))
                    tri_indices = mathutils.geometry.tessellate_polygon([coords_2d])
                
                for tri in tri_indices:
                    coords.extend([f_coords[i].copy() for i in tri])
                    indices.append((idx_offset, idx_offset+1, idx_offset+2))
                    idx_offset += 3
                    
            if coords:
                batch = batch_for_shader(SHADER, 'TRIS', {"pos": coords}, indices=indices)
                self.batches.append(batch)
            else:
                self.batches.append(None)

        # 记录当前选中的面域索引
        self.selected_region_indices = set()

        # 【核心修复】：基于当前 BMesh 构建 BVH 树，确保射线检测的面索引绝对准确
        self.bvh = BVHTree.FromBMesh(self.bm)

        bmesh.update_edit_mesh(self.obj.data)

        # 4. 注册绘制和事件回调
        self._handle_3d = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_3d, (context,), 'WINDOW', 'POST_VIEW')
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_2d, (context,), 'WINDOW', 'POST_PIXEL')
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)

        context.window_manager.modal_handler_add(self)
        self._hud_text = "【选择闭合区域】左键: 单选 | Shift+左键: 加选 | Ctrl+左键: 减选 | ESC/回车: 确认退出"
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                region = context.region
                rv3d = context.region_data
                coord = event.mouse_region_x, event.mouse_region_y
                view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
                matrix_inv = self.obj.matrix_world.inverted()
                ray_origin_obj = matrix_inv @ ray_origin
                ray_dir_obj = (matrix_inv @ (ray_origin + view_vector)) - ray_origin_obj
                ray_dir_obj.normalize()
                location, normal, index, distance = self.bvh.ray_cast(ray_origin_obj, ray_dir_obj)

                if location is not None and index is not None:
                    clicked_face = self.bm.faces[index]
                    if clicked_face in self.face_to_region:
                        reg_idx = self.face_to_region[clicked_face]
                        if event.shift:
                            self.selected_region_indices.add(reg_idx)
                        elif event.ctrl or event.oskey:
                            self.selected_region_indices.discard(reg_idx)
                        else:
                            self.selected_region_indices.clear()
                            self.selected_region_indices.add(reg_idx)
                        self.update_mesh_selection()
                else:
                    if not event.shift and not event.ctrl:
                        self.selected_region_indices.clear()
                        self.update_mesh_selection()
                return {'RUNNING_MODAL'}

            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self.remove_handlers()
                return {'FINISHED'}
            if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                self.remove_handlers()
                return {'FINISHED'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.remove_handlers()
            self.report({'ERROR'}, f"选择闭合区域出错: {str(e)}")
            return {'CANCELLED'}

    def update_mesh_selection(self):
        for f in self.bm.faces:
            f.select = False
            
        for reg_idx in self.selected_region_indices:
            for f in self.regions[reg_idx]:
                f.select = True
                
        bmesh.update_edit_mesh(self.obj.data)

    def draw_callback_3d(self, context):
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        
        obj_mat = self.obj.matrix_world
        SHADER.bind()
        
        gpu.matrix.push()
        gpu.matrix.multiply_matrix(obj_mat)
        
        for i, batch in enumerate(self.batches):
            if not batch: continue
            
            color = list(self.region_colors[i])
            # 如果该区域被选中，高亮显示（增加不透明度和亮度）
            if i in self.selected_region_indices:
                color[3] = 0.8 
            else:
                color[3] = 0.2
                
            SHADER.uniform_float("color", color)
            batch.draw(SHADER)
            
        gpu.matrix.pop()
        gpu.state.blend_set('NONE')

    def draw_callback_2d(self, context):
        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)

    def remove_handlers(self):
        if hasattr(self, '_timer') and self._timer:
            try:
                bpy.context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None
        for attr in ('_handle_3d', '_handle_2d'):
            h = getattr(self, attr, None)
            if h:
                try:
                    bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
                except Exception:
                    pass


classes = (
    RARA_OT_Model_SelectRegion,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

