# 反求圆心

import bpy
import bmesh
import mathutils
import math
import random


def _find_circle_center_3d(p1, p2, p3):
    v1 = p2 - p1
    v2 = p3 - p1
    normal = v1.cross(v2)
    if normal.length < 1e-15:
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


def _group_points_randomly(points, group_size=3):
    points = list(points)
    random.shuffle(points)
    groups = [points[i:i + group_size] for i in range(0, len(points), group_size) if len(points[i:i + group_size]) == group_size]
    return groups


class RARA_OT_FindCircleCenter(bpy.types.Operator):
    bl_idname = "rara.model_find_circle_center"
    bl_label = "反求圆心"
    bl_description = "选择圆上的三个顶点，反求出圆心位置及圆形半径"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    mode: bpy.props.EnumProperty(
        items=[
            ('VERTEX', "生成顶点到圆心", "生成顶点到圆心"),
            ('CURSOR', "生成游标到圆心", "生成游标到圆心"),
        ],
        name="圆心生成模式",
        description="将顶点或游标生成到反求出的圆心上")

    create_centers: bpy.props.BoolProperty(
        name="生成中心",
        description="勾选启用后，会根据反求的圆心生成中心边线",
        default=False)

    create_edge_loop: bpy.props.BoolProperty(
        name="生成边线",
        description="勾选启用后，会根据反求的圆心和半径生成圆形边线",
        default=True)

    circle_vertices: bpy.props.IntProperty(
        name="边线分段数",
        description="生成圆形边线的分段数",
        default=32, min=3, max=128)

    z_rotation: bpy.props.FloatProperty(
        name="Z轴旋转",
        description="圆形绕法线（Z轴）的旋转角度（度）",
        default=0.0)

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or context.mode != 'EDIT_MESH':
            self.report({'ERROR'}, "请在编辑模式下选择网格对象")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        selected_verts = [v for v in bm.verts if v.select]

        if len(selected_verts) < 3:
            self.report({'ERROR'}, "请选择至少三个顶点")
            return {'CANCELLED'}

        world_matrix = obj.matrix_world
        points = [world_matrix @ v.co.copy() for v in selected_verts]

        centers = []
        radii = []
        normals = []

        if len(points) > 3:
            groups = _group_points_randomly(points)
            for group in groups:
                if len(group) == 3:
                    center, radius, normal = _find_circle_center_3d(*group)
                    if center is not None:
                        centers.append(center)
                        radii.append(radius)
                        normals.append(normal)
        else:
            center, radius, normal = _find_circle_center_3d(*points)
            if center is not None:
                centers.append(center)
                radii.append(radius)
                normals.append(normal)

        if not centers:
            self.report({'ERROR'}, "无法计算有效的圆心")
            return {'CANCELLED'}

        self.avg_center = sum(centers, mathutils.Vector()) / len(centers)
        self.avg_radius = sum(radii) / len(radii) if radii else 0
        self.avg_normal = sum(normals, mathutils.Vector()) / len(normals)
        self.avg_normal.normalize()

        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "create_centers")
        layout.prop(self, "mode")
        layout.prop(self, "create_edge_loop")
        row = layout.row()
        row.prop(self, "circle_vertices")

    def execute(self, context):
        obj = context.active_object

        if not hasattr(self, 'avg_center'):
            self.report({'ERROR'}, "圆心计算失败")
            return {'CANCELLED'}

        if self.create_centers:
            if self.mode == 'VERTEX':
                bm = bmesh.from_edit_mesh(obj.data)
                local_center = obj.matrix_world.inverted() @ self.avg_center
                bm.verts.new(local_center)
                bm.normal_update()
                bmesh.update_edit_mesh(obj.data)
            elif self.mode == 'CURSOR':
                context.scene.cursor.location = self.avg_center
                context.scene.cursor.rotation_euler = self.avg_normal.to_track_quat('Z', 'Y').to_euler()

        if self.create_edge_loop and self.avg_radius > 0:
            z_axis = mathutils.Vector((0, 0, 1))
            if self.avg_normal.dot(z_axis) < -0.999999:
                rotation = mathutils.Euler((0, math.pi, 0)).to_matrix()
            else:
                rotation = mathutils.Matrix.Identity(3)
                if self.avg_normal.length > 0 and abs(self.avg_normal.dot(z_axis)) < 0.999999:
                    axis = z_axis.cross(self.avg_normal)
                    axis.normalize()
                    angle = z_axis.angle(self.avg_normal)
                    rotation = mathutils.Matrix.Rotation(angle, 3, axis)

            bpy.ops.mesh.primitive_circle_add(
                vertices=self.circle_vertices,
                radius=self.avg_radius,
                enter_editmode=False,
                align='WORLD',
                location=self.avg_center,
                rotation=rotation.to_euler()
            )

            circle_obj = bpy.context.active_object
            circle_obj.name = "Calculated_Circle"

        formatted_radius = _format_radius(self.avg_radius)
        self.report({'INFO'}, f"生成圆心成功: {self.avg_center}, 该圆的半径为 {formatted_radius}")
        return {'FINISHED'}


classes = (RARA_OT_FindCircleCenter,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)