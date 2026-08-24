# 智能复制阵列

import bpy
import bmesh
import gpu
import blf
from gpu_extras.batch import batch_for_shader
import mathutils
import numpy as np
from ...utils.gpu_utils import SHADER


from ...constants import BBOX_EDGES


def draw_bounding_box(matrix, color, local_bbox):
    # 根据给定的矩阵和局部边界框顶点绘制3D线框
    world_verts = [matrix @ v for v in local_bbox]
    batch = batch_for_shader(SHADER, 'LINES', {"pos": world_verts}, indices=BBOX_EDGES)
    SHADER.bind()
    SHADER.uniform_float("color", color)
    batch.draw(SHADER)

def get_interpolated_matrix(mat1, mat2, factor):
    #对两个矩阵进行TRS分解并进行平滑插值
    loc1, rot1, scale1 = mat1.decompose()
    loc2, rot2, scale2 = mat2.decompose()
    
    # 位移和缩放使用线性插值(Lerp)
    loc_interp = loc1.lerp(loc2, factor)
    scale_interp = scale1.lerp(scale2, factor)
    # 旋转使用四元数球面线性插值(Slerp)
    rot_interp = rot1.slerp(rot2, factor)
    
    return mathutils.Matrix.LocRotScale(loc_interp, rot_interp, scale_interp)
    
    
def calculate_transform_matrix_np(orig_dict, curr_dict):
    # Umeyama 相似变换拟合：旋转 + 均匀缩放 + 平移
    # 对任意点数（1/2/3/多点）都返回无剪切、无非均匀缩放的干净变换
    keys = list(orig_dict.keys())
    n = len(keys)
    if n == 0:
        return mathutils.Matrix.Identity(4)

    po = np.array([orig_dict[k].to_tuple() for k in keys], dtype=np.float64)
    pc = np.array([curr_dict[k].to_tuple() for k in keys], dtype=np.float64)

    co = po.mean(axis=0)
    cc = pc.mean(axis=0)
    po -= co
    pc -= cc

    var_orig = float(np.sum(po * po))
    if var_orig < 1e-12:
        # 原始点全部重合，只可能发生纯平移
        return mathutils.Matrix.Translation(mathutils.Vector(cc) - mathutils.Vector(co))

    H = po.T @ pc
    U, S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    d = 1.0
    if np.linalg.det(R) < 0:
        # 消除反射，保证是纯旋转
        Vt[-1, :] *= -1
        R = Vt.T @ U.T
        d = -1.0

    scale = (S[0] + S[1] + S[2] * d) / var_orig
    if not np.isfinite(scale) or scale < 1e-6:
        scale = 1.0

    trans = cc - scale * (R @ co)
    mat = mathutils.Matrix.Identity(4)
    for r in range(3):
        mat[r][0] = scale * R[r][0]
        mat[r][1] = scale * R[r][1]
        mat[r][2] = scale * R[r][2]
        mat[r][3] = trans[r]
    return mat


def draw_marker(matrix, color):
    """绘制小十字标记，用于无边界框的对象（空物体、灯光等）"""
    o = matrix.translation
    s = 0.2
    lines = [
        (o + mathutils.Vector((-s, 0, 0)), o + mathutils.Vector((s, 0, 0))),
        (o + mathutils.Vector((0, -s, 0)), o + mathutils.Vector((0, s, 0))),
        (o + mathutils.Vector((0, 0, -s)), o + mathutils.Vector((0, 0, s))),
    ]
    pos = [p for pair in lines for p in pair]
    batch = batch_for_shader(SHADER, 'LINES', {"pos": pos})
    SHADER.bind()
    SHADER.uniform_float("color", color)
    batch.draw(SHADER)


def has_meaningful_bbox(obj):
    """只有 MESH/CURVE/SURFACE/META/FONT 有有意义的边界框"""
    return obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}


def draw_object_callback_3d(self, context):
    if not self.active_obj:
        return

    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.line_width_set(2.0)

    use_marker = not has_meaningful_bbox(self.active_obj)

    # 1. 绘制原始位置(红色)
    if use_marker:
        draw_marker(self.orig_matrix, (1.0, 0.0, 0.0, 0.8))
    else:
        draw_bounding_box(self.orig_matrix, (1.0, 0.0, 0.0, 0.8), self.local_bbox)

    # 2. 绘制当前活动物体(蓝色)
    curr_matrix = self.active_obj.matrix_world
    if use_marker:
        draw_marker(curr_matrix, (0.0, 0.5, 1.0, 0.8))
    else:
        draw_bounding_box(curr_matrix, (0.0, 0.5, 1.0, 0.8), self.local_bbox)

    # 3. 绘制智能阵列(紫色)
    if self.array_count > 0:
        if self.is_interpolate:
            for i in range(1, self.array_count + 1):
                factor = i / (self.array_count + 1)
                interp_mat = get_interpolated_matrix(self.orig_matrix, curr_matrix, factor)
                if use_marker:
                    draw_marker(interp_mat, (0.8, 0.2, 1.0, 0.8))
                else:
                    draw_bounding_box(interp_mat, (0.8, 0.2, 1.0, 0.8), self.local_bbox)
        else:
            try:
                orig_inv = self.orig_matrix.inverted()
            except ValueError:
                gpu.state.line_width_set(1.0)
                gpu.state.blend_set('NONE')
                return
            delta_mat = orig_inv @ curr_matrix
            prev_mat = curr_matrix.copy()
            for i in range(self.array_count):
                next_mat = prev_mat @ delta_mat
                if use_marker:
                    draw_marker(next_mat, (0.8, 0.2, 1.0, 0.8))
                else:
                    draw_bounding_box(next_mat, (0.8, 0.2, 1.0, 0.8), self.local_bbox)
                prev_mat = next_mat

    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')

def draw_object_callback_2d(self, context):
    """在屏幕底部居中绘制HUD文本状态"""
    mode_text = "插值(Interpolate)" if self.is_interpolate else "外推 (Extrapolate)"
    text = f"智能阵列 | 模式: {mode_text} [Tab切换] | 数量: {self.array_count} [Shift+滚轮]"
    
    font_id = 0
    blf.size(font_id, 20)
    tw, th = blf.dimensions(font_id, text)
    blf.position(font_id, (context.region.width - tw) / 2, 30, 0)
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
    blf.draw(font_id, text)
    
    
def draw_mesh_callback_3d(self, context):
    if not self.active_obj or context.mode != 'EDIT_MESH':
        return

    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.line_width_set(2.0)

    # 直接使用对象引用获取当前坐标
    curr_coords = {self.vert_map[v]: v.co.copy() for v in self.active_verts if v.is_valid}
    local_transform = calculate_transform_matrix_np(self.orig_coords, curr_coords)
    
    obj_mat = self.active_obj.matrix_world
    ident = mathutils.Matrix.Identity(4)

    draw_list = [
        (ident, (1.0, 0.0, 0.0, 0.8)),           
        (local_transform, (0.0, 0.5, 1.0, 0.8))  
    ]

    if self.array_count > 0:
        if self.is_interpolate:
            for i in range(1, self.array_count + 1):
                factor = i / (self.array_count + 1)
                interp_mat = get_interpolated_matrix(ident, local_transform, factor)
                draw_list.append((interp_mat, (0.8, 0.2, 1.0, 0.8)))
        else:
            prev_mat = local_transform.copy()
            for i in range(self.array_count):
                next_mat = prev_mat @ local_transform
                draw_list.append((next_mat, (0.8, 0.2, 1.0, 0.8)))
                prev_mat = next_mat

    SHADER.bind()
    batch_to_draw = self.wire_batch if self.draw_wireframe else self.bbox_batch

    for local_mat, color in draw_list:
        gpu.matrix.push()
        gpu.matrix.multiply_matrix(obj_mat @ local_mat)
        SHADER.uniform_float("color", color)
        batch_to_draw.draw(SHADER)
        gpu.matrix.pop()

    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')

def draw_mesh_callback_2d(self, context):
    mode_text = "插值(Interpolate)" if self.is_interpolate else "外推 (Extrapolate)"
    preview_mode = "完整线框" if self.draw_wireframe else "边界框"
    text = f"网格智能阵列 | 模式: {mode_text} [Tab] | 数量: {self.array_count} [Shift+滚轮] | 预览: {preview_mode} | Enter确认"
    
    font_id = 0
    blf.size(font_id, 20)
    tw, th = blf.dimensions(font_id, text)
    blf.position(font_id, (context.region.width - tw) / 2, 30, 0)
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
    blf.draw(font_id, text)






class RARA_OT_Model_InteractiveArrayObject(bpy.types.Operator):
    """智能UI复制阵列模态操作"""
    bl_idname = "rara.model_interactive_array_object"
    bl_label = "智能复制阵列"
    bl_description = "通过交互方式快速复制并阵列选中物体，支持拖拽预览和实时更新\n\n【左键拖拽】设置阵列偏移\n【Ctrl+左键】精确调整偏移距离\n【回车】确认生成阵列\n【ESC】取消"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object

    def invoke(self, context, event):
        if not context.active_object:
            self.report({'WARNING'}, "没有活动物体！")
            return {'CANCELLED'}

        self.active_obj = context.active_object
        self.orig_matrix = self.active_obj.matrix_world.copy()
        self.local_bbox = [mathutils.Vector(v) for v in self.active_obj.bound_box]
        
        self.array_count = 2
        self.is_interpolate = True # 默认内插模式

        # 注册 3D 和2D 绘制回调
        self._handle_3d = bpy.types.SpaceView3D.draw_handler_add(draw_object_callback_3d, (self, context), 'WINDOW', 'POST_VIEW')
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_object_callback_2d, (self, context), 'WINDOW', 'POST_PIXEL')
        
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        mode = "内插" if self.is_interpolate else "外推"
        self._hud_text = "【物体智能阵列】数量 {} | 模式(Tab): {} | 左键拖拽: 设置偏移 | Shift+滚轮: 调整数量 | 回车: 确认 | ESC: 取消".format(self.array_count, mode)

    def modal(self, context, event):
        try:
            context.area.tag_redraw()

            if event.type == 'TAB' and event.value == 'PRESS':
                self.is_interpolate = not self.is_interpolate
                self.update_header(context)
                return {'RUNNING_MODAL'}

            elif event.shift and event.type == 'WHEELUPMOUSE':
                self.array_count += 1
                self.update_header(context)
                return {'RUNNING_MODAL'}
            
            elif event.shift and event.type == 'WHEELDOWNMOUSE':
                self.array_count = max(0, self.array_count - 1)
                self.update_header(context)
                return {'RUNNING_MODAL'}

            elif event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                self.execute_array(context)
                self.remove_handlers(context)
                return {'FINISHED'}

            elif event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self.active_obj.matrix_world = self.orig_matrix
                self.remove_handlers(context)
                return {'CANCELLED'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.remove_handlers(context)
            self.report({'ERROR'}, f"智能阵列出错: {str(e)}")
            return {'CANCELLED'}

    def execute_array(self, context):
        try:
            curr_matrix = self.active_obj.matrix_world
            new_objs = []
            new_objs.append(self._create_instance(context, self.orig_matrix))

            if self.array_count > 0:
                if self.is_interpolate:
                    for i in range(1, self.array_count + 1):
                        factor = i / (self.array_count + 1)
                        mat = get_interpolated_matrix(self.orig_matrix, curr_matrix, factor)
                        new_objs.append(self._create_instance(context, mat))
                else:
                    orig_inv = self.orig_matrix.inverted()
                    delta_mat = orig_inv @ curr_matrix
                    prev_mat = curr_matrix.copy()
                    for i in range(self.array_count):
                        next_mat = prev_mat @ delta_mat
                        new_objs.append(self._create_instance(context, next_mat))
                        prev_mat = next_mat

            for obj in new_objs:
                obj.select_set(True)
        except Exception as e:
            self.report({'ERROR'}, f"执行物体阵列出错: {str(e)}")

    def _create_instance(self, context, matrix):
        new_obj = self.active_obj.copy()
        if self.active_obj.data:
            new_obj.data = self.active_obj.data.copy()
        new_obj.matrix_world = matrix
        context.collection.objects.link(new_obj)
        return new_obj

    def remove_handlers(self, context=None):
        for attr in ('_handle_3d', '_handle_2d'):
            h = getattr(self, attr, None)
            if h:
                try:
                    bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
                except Exception:
                    pass


class RARA_OT_Model_InteractiveArrayMesh(bpy.types.Operator):
    bl_idname = "rara.model_interactive_array_mesh"
    bl_label = "网格智能复制阵列"
    bl_description = "在编辑模式下通过交互方式复制并阵列选中网格元素，支持拖拽预览和实时更新\n\n【左键拖拽】设置阵列偏移\n【Ctrl+左键】精确调整偏移距离\n【回车】确认生成阵列\n【ESC】取消"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    is_interpolate: bpy.props.BoolProperty(name="插值模式", default=True)
    array_count: bpy.props.IntProperty(name="数量", default=2, min=0)

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
            self.report({'WARNING'}, "请选择一个编辑模式下的网格对象")
            return {'CANCELLED'}
        self.active_obj = obj
        self.bm = bmesh.from_edit_mesh(obj.data)

        # 强制刷新内部索引表
        self.bm.verts.ensure_lookup_table()
        self.bm.edges.ensure_lookup_table()
        self.bm.faces.ensure_lookup_table()
        self.bm.verts.index_update()

        self.orig_verts = [v for v in self.bm.verts if v.select]
        self.orig_edges = [e for e in self.bm.edges if e.select]
        self.orig_faces = [f for f in self.bm.faces if f.select]
        sel_geom = self.orig_verts + self.orig_edges + self.orig_faces
        if not sel_geom:
            self.report({'WARNING'}, "请选中至少一个网格元素")
            return {'CANCELLED'}

        # 原始坐标用BMVert 对象做键（避免索引漂移）
        self.orig_coords = {v: v.co.copy() for v in self.orig_verts}

        # 构建边界框，用于 GPU 预览
        coords = list(self.orig_coords.values())
        min_v = mathutils.Vector((min(c.x for c in coords), min(c.y for c in coords), min(c.z for c in coords)))
        max_v = mathutils.Vector((max(c.x for c in coords), max(c.y for c in coords), max(c.z for c in coords)))
        bbox_verts = [
            (min_v.x, min_v.y, min_v.z), (max_v.x, min_v.y, min_v.z),
            (max_v.x, max_v.y, min_v.z), (min_v.x, max_v.y, min_v.z),
            (min_v.x, min_v.y, max_v.z), (max_v.x, min_v.y, max_v.z),
            (max_v.x, max_v.y, max_v.z), (min_v.x, max_v.y, max_v.z),
        ]
        self.bbox_batch = batch_for_shader(SHADER, 'LINES', {"pos": bbox_verts}, indices=BBOX_EDGES)

        # 顶点数< 1000 时绘制完整线框预览
        self.draw_wireframe = len(self.orig_verts) < 1000
        if self.draw_wireframe:
            wire_vert_set = set()
            for e in self.orig_edges:
                wire_vert_set.add(e.verts[0])
                wire_vert_set.add(e.verts[1])
            wire_vert_list = list(wire_vert_set)
            vert_idx_map = {v: i for i, v in enumerate(wire_vert_list)}
            wire_verts_co = [v.co for v in wire_vert_list]
            wire_indices = [(vert_idx_map[e.verts[0]], vert_idx_map[e.verts[1]]) for e in self.orig_edges]
            if wire_indices:
                self.wire_batch = batch_for_shader(SHADER, 'LINES', {"pos": wire_verts_co}, indices=wire_indices)
            else:
                self.draw_wireframe = False

        # 复制一份临时副本供用户交互拖拽
        geom_to_duplicate = self.orig_verts + self.orig_edges + self.orig_faces
        ret = bmesh.ops.duplicate(self.bm, geom=geom_to_duplicate)
        self.active_verts = [ele for ele in ret['geom'] if isinstance(ele, bmesh.types.BMVert)]
        self.vert_map = {new_v: orig_v for new_v, orig_v in zip(self.active_verts, self.orig_verts)}

        for v in self.bm.verts: v.select = False
        for e in self.bm.edges: e.select = False
        for f in self.bm.faces: f.select = False
        for ele in ret['geom']:
            if hasattr(ele, 'select'):
                ele.select = True
        bmesh.update_edit_mesh(self.active_obj.data)

        args = (self, context)
        self._handle_3d = bpy.types.SpaceView3D.draw_handler_add(draw_mesh_callback_3d, args, 'WINDOW', 'POST_VIEW')
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_mesh_callback_2d, args, 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        self._hud_text = (f"【网格智能阵列】数量(Shift+滚轮): {self.array_count} | "
               f"模式(Tab): {'插值' if self.is_interpolate else '外推'} | "
               f"确认: 回车 | 取消: ESC")

    def draw_3d(self, op, context):
        pass

    def draw_2d(self, op, context):
        pass

    def modal(self, context, event):
        try:
            context.area.tag_redraw()

            if event.type == 'TAB' and event.value == 'PRESS':
                self.is_interpolate = not self.is_interpolate
                self.update_header(context)
                return {'RUNNING_MODAL'}
            elif event.shift and event.type == 'WHEELUPMOUSE':
                self.array_count += 1
                self.update_header(context)
                return {'RUNNING_MODAL'}
            elif event.shift and event.type == 'WHEELDOWNMOUSE':
                self.array_count = max(0, self.array_count - 1)
                self.update_header(context)
                return {'RUNNING_MODAL'}
            elif event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                self.execute_array(context)
                self.remove_handlers(context)
                return {'FINISHED'}
            elif event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                geom_to_delete = [v for v in self.active_verts if v.is_valid]
                try:
                    bmesh.ops.delete(self.bm, geom=geom_to_delete, context='VERTS')
                except Exception:
                    pass
                for v in self.bm.verts: v.select = False
                for e in self.bm.edges: e.select = False
                for f in self.bm.faces: f.select = False
                for v in self.orig_verts:
                    if v.is_valid: v.select = True
                for e in self.orig_edges:
                    if e.is_valid: e.select = True
                for f in self.orig_faces:
                    if f.is_valid: f.select = True
                try:
                    bmesh.update_edit_mesh(self.active_obj.data)
                except Exception:
                    pass
                self.remove_handlers(context)
                return {'CANCELLED'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.remove_handlers(context)
            self.report({'ERROR'}, f"网格智能阵列出错: {str(e)}")
            return {'CANCELLED'}

    def execute_array(self, context):
        try:
            curr_coords = {self.vert_map[v]: v.co.copy() for v in self.active_verts if v.is_valid}
            local_transform = calculate_transform_matrix_np(self.orig_coords, curr_coords)

            for v in self.bm.verts: v.select = False
            for e in self.bm.edges: e.select = False
            for f in self.bm.faces: f.select = False
            for v in self.active_verts:
                if v.is_valid: v.select = True

            matrices_to_generate = []
            if self.array_count > 0:
                if self.is_interpolate:
                    ident = mathutils.Matrix.Identity(4)
                    for i in range(1, self.array_count + 1):
                        factor = i / (self.array_count + 1)
                        mat = get_interpolated_matrix(ident, local_transform, factor)
                        matrices_to_generate.append(mat)
                else:
                    prev_mat = local_transform.copy()
                    for i in range(self.array_count):
                        next_mat = prev_mat @ local_transform
                        matrices_to_generate.append(next_mat)
                        prev_mat = next_mat

            for mat in matrices_to_generate:
                geom_to_duplicate = self.orig_verts + self.orig_edges + self.orig_faces
                ret = bmesh.ops.duplicate(self.bm, geom=geom_to_duplicate)
                new_verts = [ele for ele in ret['geom'] if isinstance(ele, bmesh.types.BMVert)]
                for v in new_verts:
                    v.co = mat @ v.co
                    v.select = True

            bmesh.update_edit_mesh(self.active_obj.data)
        except Exception as e:
            self.report({'ERROR'}, f"执行网格阵列出错: {str(e)}")

    def remove_handlers(self, context=None):
        for attr in ('_handle_3d', '_handle_2d'):
            h = getattr(self, attr, None)
            if h:
                try:
                    bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
                except Exception:
                    pass


classes = (
    RARA_OT_Model_InteractiveArrayObject,
    RARA_OT_Model_InteractiveArrayMesh,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

