# 修复应用后的旋转 — SVD + 法线 OBB 双算法

import time
import bpy
import gpu
import numpy as np
import bmesh
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector
from ...utils.math_utils import compute_obb_orientation

# ── 轴动画系统 ──────────────────────────────────────────
_AXIS_SCALE = 1.2
_AXIS_TIMEOUT = 1.0
_axis_anim_data = []
_axis_anim_handler = None
_axis_anim_timer_active = False


def _convex_hull_2d(points):
    points = np.unique(points, axis=0)
    if len(points) < 3:
        return points
    idx = np.lexsort((points[:, 0], points[:, 1]))
    pts = points[idx]
    lower = []
    for p in pts:
        while len(lower) >= 2:
            a, b = lower[-2], lower[-1]
            if (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) > 0:
                break
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2:
            a, b = upper[-2], upper[-1]
            if (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) > 0:
                break
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def _min_area_rect_2d(points_2d):
    hull = _convex_hull_2d(points_2d)
    if len(hull) < 3:
        if len(hull) == 2:
            d = hull[1] - hull[0]
            d = d / np.linalg.norm(d)
            return np.array([d[0], d[1]])
        return np.array([1.0, 0.0])
    best_area = float('inf')
    best_long_edge = np.array([1.0, 0.0])
    n = len(hull)
    for i in range(n):
        edge = hull[(i + 1) % n] - hull[i]
        length = np.linalg.norm(edge)
        if length < 1e-10:
            continue
        dir_x = edge / length
        dir_y = np.array([-dir_x[1], dir_x[0]])
        proj_x = hull @ dir_x
        proj_y = hull @ dir_y
        width = proj_x.max() - proj_x.min()
        height = proj_y.max() - proj_y.min()
        area = width * height
        if area < best_area:
            best_area = area
            long_edge = dir_x if width >= height else dir_y
            best_long_edge = long_edge
    return best_long_edge


def _compute_normal_obb(vertices_local, face_normals, face_areas):
    if len(vertices_local) < 3 or len(face_normals) < 3:
        return Matrix.Identity(3)
    z_idx = int(np.argmax(face_areas))
    z_axis = face_normals[z_idx].astype(np.float64)
    z_axis = z_axis / np.linalg.norm(z_axis)
    proj_plane_u = np.array([1.0, 0.0, 0.0])
    if np.abs(np.dot(proj_plane_u, z_axis)) > 0.99:
        proj_plane_u = np.array([0.0, 1.0, 0.0])
    proj_plane_u = proj_plane_u - np.dot(proj_plane_u, z_axis) * z_axis
    proj_plane_u = proj_plane_u / np.linalg.norm(proj_plane_u)
    proj_plane_v = np.cross(z_axis, proj_plane_u)
    points_2d = np.column_stack([vertices_local @ proj_plane_u, vertices_local @ proj_plane_v])
    x_dir_2d = _min_area_rect_2d(points_2d)
    x_axis = x_dir_2d[0] * proj_plane_u + x_dir_2d[1] * proj_plane_v
    x_axis = x_axis / np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)
    y_axis = y_axis / np.linalg.norm(y_axis)
    rot_np = np.array([x_axis, y_axis, z_axis]).T
    if np.linalg.det(rot_np) < 0:
        y_axis = -y_axis
    return Matrix(np.array([x_axis, y_axis, z_axis]).T.tolist()).to_3x3()


def get_obb_orientation_svd_local(obj):
    if obj.type != 'MESH':
        return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh()
    if not mesh.vertices:
        evaluated_obj.to_mesh_clear()
        return None
    vertices_local = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', vertices_local)
    vertices_local = vertices_local.reshape(-1, 3)
    evaluated_obj.to_mesh_clear()
    if len(vertices_local) <= 1:
        return Matrix.Identity(3)
    return compute_obb_orientation(vertices_local).to_3x3()


def get_obb_orientation_normal_local(obj):
    if obj.type != 'MESH':
        return None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh()
    if not mesh.vertices or not mesh.polygons:
        evaluated_obj.to_mesh_clear()
        return None
    vertices_local = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', vertices_local)
    vertices_local = vertices_local.reshape(-1, 3)
    face_normals = np.empty(len(mesh.polygons) * 3, dtype=np.float32)
    mesh.polygons.foreach_get('normal', face_normals)
    face_normals = face_normals.reshape(-1, 3)
    face_areas = np.empty(len(mesh.polygons), dtype=np.float32)
    mesh.polygons.foreach_get('area', face_areas)
    evaluated_obj.to_mesh_clear()
    if len(vertices_local) <= 1:
        return Matrix.Identity(3)
    return _compute_normal_obb(vertices_local, face_normals, face_areas)


def _show_axis_animation(matrices):
    global _axis_anim_data
    now = time.time()
    for m in matrices:
        _axis_anim_data.append((m, now))
    _ensure_axis_anim_running()


def _ensure_axis_anim_running():
    global _axis_anim_handler, _axis_anim_timer_active
    if _axis_anim_handler is None:
        def draw():
            _draw_axis_animations()
        _axis_anim_handler = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_VIEW')
    if not _axis_anim_timer_active:
        _axis_anim_timer_active = True
        if not bpy.app.timers.is_registered(_axis_anim_timer):
            bpy.app.timers.register(_axis_anim_timer, first_interval=0.03, persistent=True)


def _draw_axis_animations():
    global _axis_anim_data
    if not _axis_anim_data:
        return
    now = time.time()
    remaining = []
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.line_width_set(2.0)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    colors = [(1.0, 0.25, 0.25), (0.25, 1.0, 0.25), (0.2, 0.6, 1.0)]
    for matrix, start_time in _axis_anim_data:
        elapsed = now - start_time
        if elapsed >= _AXIS_TIMEOUT:
            continue
        remaining.append((matrix, start_time))
        alpha = 1.0 - elapsed / _AXIS_TIMEOUT
        origin = matrix.translation
        for i in range(3):
            tip = matrix @ Vector([_AXIS_SCALE if j == i else 0 for j in range(3)])
            color = (*colors[i], alpha)
            batch = batch_for_shader(shader, 'LINES', {"pos": [(origin.x, origin.y, origin.z), (tip.x, tip.y, tip.z)]})
            shader.bind()
            shader.uniform_float("color", color)
            batch.draw(shader)
    _axis_anim_data = remaining
    if not _axis_anim_data:
        _stop_axis_anim()


def _axis_anim_timer():
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()
    if _axis_anim_data:
        return 0.03
    return None


def _stop_axis_anim():
    global _axis_anim_handler, _axis_anim_timer_active
    _axis_anim_data.clear()
    _axis_anim_timer_active = False


# ── 操作符 ──────────────────────────────────────────────

class RARA_OT_Model_FixRotation(bpy.types.Operator):
    bl_idname = "rara.model_fix_rotation"
    bl_label = "修复旋转(SVD)"
    bl_description = "协方差 SVD 分解重算物体旋转，适合曲面/有机模型"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        original_mode = context.object.mode if context.object else 'OBJECT'
        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        processed_count = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            obb_rot_matrix_local = get_obb_orientation_svd_local(obj)
            if obb_rot_matrix_local is None:
                continue
            inverse_rotation_matrix = obb_rot_matrix_local.inverted()
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            bmesh.ops.transform(bm, matrix=inverse_rotation_matrix.to_4x4(), verts=bm.verts)
            bm.to_mesh(obj.data)
            obj.data.update()
            bm.free()
            obj.rotation_mode = 'XYZ'
            current_obj_rotation_matrix = obj.rotation_euler.to_matrix()
            combined_rotation_matrix = current_obj_rotation_matrix @ obb_rot_matrix_local
            obj.rotation_euler = combined_rotation_matrix.to_euler(obj.rotation_euler.order)
            processed_count += 1
        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=original_mode)
        if processed_count > 0:
            self.report({'INFO'}, f"已修复 {processed_count} 个网格的旋转。")
            _show_axis_animation([obj.matrix_world.copy() for obj in context.selected_objects if obj.type == 'MESH'])
        return {'FINISHED'}


class RARA_OT_Model_FixRotationByNormals(bpy.types.Operator):
    bl_idname = "rara.model_fix_rotation_by_normals"
    bl_label = "修复旋转(法线OBB)"
    bl_description = "基于面法线 K-Means 找厚度方向 + 2D 旋转卡壳找长边，适合硬表面/L形模型"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        original_mode = context.object.mode if context.object else 'OBJECT'
        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        processed_count = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            obb_rot_matrix_local = get_obb_orientation_normal_local(obj)
            if obb_rot_matrix_local is None:
                continue
            inverse_rotation_matrix = obb_rot_matrix_local.inverted()
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            bmesh.ops.transform(bm, matrix=inverse_rotation_matrix.to_4x4(), verts=bm.verts)
            bm.to_mesh(obj.data)
            obj.data.update()
            bm.free()
            obj.rotation_mode = 'XYZ'
            current_obj_rotation_matrix = obj.rotation_euler.to_matrix()
            combined_rotation_matrix = current_obj_rotation_matrix @ obb_rot_matrix_local
            obj.rotation_euler = combined_rotation_matrix.to_euler(obj.rotation_euler.order)
            processed_count += 1
        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=original_mode)
        if processed_count > 0:
            self.report({'INFO'}, f"已修复 {processed_count} 个网格的旋转（法线OBB）。")
            _show_axis_animation([obj.matrix_world.copy() for obj in context.selected_objects if obj.type == 'MESH'])
        return {'FINISHED'}


classes = (
    RARA_OT_Model_FixRotation,
    RARA_OT_Model_FixRotationByNormals,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    global _axis_anim_handler, _axis_anim_timer_active
    _axis_anim_data.clear()
    if _axis_anim_handler:
        bpy.types.SpaceView3D.draw_handler_remove(_axis_anim_handler, 'WINDOW')
        _axis_anim_handler = None
    if bpy.app.timers.is_registered(_axis_anim_timer):
        bpy.app.timers.unregister(_axis_anim_timer)
    _axis_anim_timer_active = False
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
