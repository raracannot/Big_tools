# 反求圆心 — 重算逻辑在 execute，支持 Undo 中调参

import bpy
import bmesh
import mathutils
import math
import random


def _find_circle_center_3d(p1, p2, p3):
    v1 = p2 - p1
    v2 = p3 - p1
    normal = v1.cross(v2)
    scale2 = max(v1.length_squared, v2.length_squared, 1e-10)
    if normal.length_squared < scale2 * 1e-12:
        return None, None, None
    normal.normalize()
    a = (p2 - p3).length_squared
    b = (p3 - p1).length_squared
    c = (p1 - p2).length_squared
    d = 2 * (a * b + b * c + c * a) - (a * a + b * b + c * c)
    if abs(d) < 1e-15:
        return None, None, None
    ux = ((a * (b + c - a) * p1.x) + (b * (c + a - b) * p2.x) + (c * (a + b - c) * p3.x)) / d
    uy = ((a * (b + c - a) * p1.y) + (b * (c + a - b) * p2.y) + (c * (a + b - c) * p3.y)) / d
    uz = ((a * (b + c - a) * p1.z) + (b * (c + a - b) * p2.z) + (c * (a + b - c) * p3.z)) / d
    center = mathutils.Vector((ux, uy, uz))
    radius = (p1 - center).length
    return center, radius, normal


def _format_radius(radius):
    if radius > 0.1:
        return f"{radius:.3f}"
    elif 0.001 <= radius <= 0.1:
        return f"{radius:.6f}"
    else:
        return f"{radius:.12f}"


def _compute_circle(points_world, method, ransac_threshold):
    """从世界空间点集反求圆。返回 (center, radius, normal) 或 (None, None, None)"""
    if len(points_world) < 3:
        return None, None, None

    if len(points_world) == 3:
        return _find_circle_center_3d(*points_world)

    if method == 'AVERAGE':
        pts = list(points_world)
        random.shuffle(pts)
        centers = []
        radii = []
        normals = []
        for i in range(0, len(pts) - 2, 3):
            g = pts[i:i + 3]
            if len(g) == 3:
                c, r, n = _find_circle_center_3d(*g)
                if c is not None:
                    centers.append(c)
                    radii.append(r)
                    normals.append(n)
        if not centers:
            return None, None, None
        center = sum(centers, mathutils.Vector()) / len(centers)
        radius = sum(radii) / len(radii)
        normal = (sum(normals, mathutils.Vector()) / len(normals)).normalized()
        return center, radius, normal

    else:  # RANSAC
        best_inliers = -1
        best_center = None
        best_radius = None
        best_normal = None
        for _ in range(200):
            group = random.sample(points_world, 3)
            center, radius, normal = _find_circle_center_3d(*group)
            if center is None:
                continue
            inliers = 0
            for v_co in points_world:
                to_center = v_co - center
                plane_dist = abs(to_center.dot(normal))
                proj = v_co - center - to_center.dot(normal) * normal
                proj_dist = abs(proj.length - radius)
                if plane_dist < ransac_threshold and proj_dist < ransac_threshold:
                    inliers += 1
            if inliers > best_inliers:
                best_inliers = inliers
                best_center = center
                best_radius = radius
                best_normal = normal
        return best_center, best_radius, best_normal


class RARA_OT_FindCircleCenter(bpy.types.Operator):
    bl_idname = "rara.model_find_circle_center"
    bl_label = "反求圆心"
    bl_description = "选择圆上的顶点，反求出圆心位置及圆形半径——可在Undo面板中修改参数"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    mode: bpy.props.EnumProperty(
        name="圆心生成模式",
        items=[('VERTEX', "生成顶点到圆心", ""), ('CURSOR', "生成游标到圆心", "")])

    method: bpy.props.EnumProperty(
        name="计算方法",
        items=[('AVERAGE', "平均", "随机3点分组求圆，取平均值"),
               ('RANSAC', "准确", "RANSAC: 随机200组，取支持者最多的圆")],
        default='AVERAGE')

    ransac_threshold: bpy.props.FloatProperty(
        name="RANSAC 容差", default=0.01, min=0.0,
        description="顶点到圆的平面/径向距离阈值")

    create_centers: bpy.props.BoolProperty(name="生成中心", default=False)
    create_edge_loop: bpy.props.BoolProperty(name="生成边线", default=True)
    circle_vertices: bpy.props.IntProperty(name="边线分段数", default=32, min=3, max=500)

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or context.mode != 'EDIT_MESH':
            return self._cancel(context, "请在编辑模式下选择网格对象")

        bm = bmesh.from_edit_mesh(obj.data)
        selected_verts = [v for v in bm.verts if v.select]
        if len(selected_verts) < 3:
            return self._cancel(context, "请选择至少三个顶点")

        # 取出世界坐标点和 matrix 备用
        wm = obj.matrix_world
        self._points_world = [wm @ v.co.copy() for v in selected_verts]
        self._matrix_world = wm.copy()

        return context.window_manager.invoke_props_dialog(self)

    def _cancel(self, context, msg):
        self.report({'ERROR'}, msg)
        return {'CANCELLED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "method")
        if self.method == 'RANSAC':
            layout.prop(self, "ransac_threshold")
        row = layout.row()
        row.prop(self, "create_centers",text="",icon="PROP_OFF")
        if self.create_centers:
            row.prop(self, "mode",text="")
        else:    
            row.label(text="勾选后启用生成圆心")
        row = layout.row()
        row.prop(self, "create_edge_loop",text="",icon="MESH_CIRCLE")
        if self.create_edge_loop:
            row.prop(self, "circle_vertices")
        else:    
            row.label(text="勾选后启用生成外圆")
    def execute(self, context):
        # 每次执行都重新计算（支持 Undo 调参）
        center, radius, normal = _compute_circle(
            self._points_world, self.method, self.ransac_threshold)
        if center is None:
            self.report({'ERROR'}, "无法计算有效的圆心")
            return {'CANCELLED'}

        obj = context.active_object

        if self.create_centers:
            if self.mode == 'VERTEX':
                bm = bmesh.from_edit_mesh(obj.data)
                local_center = obj.matrix_world.inverted() @ center
                bm.verts.new(local_center)
                bm.normal_update()
                bmesh.update_edit_mesh(obj.data)
            elif self.mode == 'CURSOR':
                context.scene.cursor.location = center
                context.scene.cursor.rotation_euler = normal.to_track_quat('Z', 'Y').to_euler()

        if self.create_edge_loop and radius > 0:
            z_axis = mathutils.Vector((0, 0, 1))
            if normal.dot(z_axis) < -0.999999:
                rotation = mathutils.Euler((0, math.pi, 0)).to_matrix()
            else:
                rotation = mathutils.Matrix.Identity(3)
                if normal.length > 0 and abs(normal.dot(z_axis)) < 0.999999:
                    axis = z_axis.cross(normal).normalized()
                    rotation = mathutils.Matrix.Rotation(z_axis.angle(normal), 3, axis)

            bpy.ops.mesh.primitive_circle_add(
                vertices=self.circle_vertices, radius=radius,
                enter_editmode=False, align='WORLD',
                location=center, rotation=rotation.to_euler())
            bpy.context.active_object.name = "Calculated_Circle"

        self.report({'INFO'}, f"圆心: {center}, 半径: {_format_radius(radius)}")
        return {'FINISHED'}


classes = (RARA_OT_FindCircleCenter,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
