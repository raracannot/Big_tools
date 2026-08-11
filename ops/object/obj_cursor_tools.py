# 移动游标到所选

import bpy
import bmesh
from mathutils import Vector, Matrix


def _get_selected_points(context):
    mode = context.mode
    points = []
    single_rot = None

    if mode == 'OBJECT':
        for obj in context.selected_objects:
            points.append(obj.matrix_world.translation.copy())
        if len(points) == 1:
            single_rot = context.selected_objects[0].matrix_world.to_quaternion()
        return points, single_rot

    if mode == 'EDIT_MESH':
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return points, None
        bm = bmesh.from_edit_mesh(obj.data)
        for v in bm.verts:
            if v.select:
                points.append(obj.matrix_world @ v.co.copy())
        return points, None

    if mode == 'EDIT_CURVE':
        obj = context.active_object
        if not obj or obj.type not in ('CURVE', 'SURFACE'):
            return points, None
        for spline in obj.data.splines:
            for bp in spline.bezier_points:
                if bp.select_control_point or bp.select_left_handle or bp.select_right_handle:
                    points.append(obj.matrix_world @ bp.co.copy())
            for pt in spline.points:
                if pt.select:
                    points.append(obj.matrix_world @ pt.co.to_3d())
        return points, None

    if mode == 'EDIT_ARMATURE':
        obj = context.active_object
        if not obj or obj.type != 'ARMATURE':
            return points, None
        for bone in obj.data.edit_bones:
            if bone.select_head:
                points.append(obj.matrix_world @ bone.head.copy())
            elif bone.select:
                points.append(obj.matrix_world @ bone.head.copy())
        return points, None

    if mode == 'EDIT_METABALL':
        obj = context.active_object
        if not obj or obj.type != 'META':
            return points, None
        for elem in obj.data.elements:
            if elem.select:
                points.append(obj.matrix_world @ elem.co.copy())
        return points, None

    if mode == 'EDIT_LATTICE':
        obj = context.active_object
        if not obj or obj.type != 'LATTICE':
            return points, None
        for pt in obj.data.points:
            if pt.select:
                points.append(obj.matrix_world @ pt.co_deform.copy())
        return points, None

    return points, None


def _compute_cursor_rotation(points):
    if len(points) == 0:
        return None, None

    if len(points) == 1:
        return Vector((0, 0, 0)), 'XYZ'

    if len(points) == 2:
        z = (points[1] - points[0]).normalized()
        if z.length < 0.0001:
            return Vector((0, 0, 0)), 'XYZ'
        ref = Vector((0, 0, 1))
        if abs(z.dot(ref)) > 0.999:
            ref = Vector((1, 0, 0))
        x = z.cross(ref).normalized()
        y = z.cross(x)
        rot_mat = Matrix((x, y, z)).transposed().to_4x4()
        return rot_mat.to_euler('XYZ'), 'XYZ'

    normal = Vector((0, 0, 0))
    p0 = points[0]
    p1 = points[1]
    for i in range(2, len(points)):
        normal += (points[i] - p0).cross(points[i] - p1)
    if normal.length < 0.0001:
        return Vector((0, 0, 0)), 'XYZ'
    z = normal.normalized()
    ref = Vector((0, 0, 1))
    if abs(z.dot(ref)) > 0.999:
        ref = Vector((1, 0, 0))
    x = z.cross(ref).normalized()
    y = z.cross(x)
    rot_mat = Matrix((x, y, z)).transposed().to_4x4()
    return rot_mat.to_euler('XYZ'), 'XYZ'


class RARA_OT_CursorToSelected(bpy.types.Operator):
    bl_idname = "rara.cursor_to_selected"
    bl_label = "移动游标到所选"
    bl_description = "将 3D 游标移动到选中元素的中心位置，并根据选中数量自动对齐旋转方向"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area.type == 'VIEW_3D'

    def execute(self, context):
        points, single_rot = _get_selected_points(context)
        if not points:
            self.report({'WARNING'}, "未选中任何元素")
            return {'CANCELLED'}

        center = sum(points, Vector((0, 0, 0))) / len(points)
        context.scene.cursor.location = center

        if single_rot is not None:
            context.scene.cursor.rotation_mode = 'QUATERNION'
            context.scene.cursor.rotation_quaternion = single_rot
        else:
            euler, mode = _compute_cursor_rotation(points)
            if euler is not None:
                context.scene.cursor.rotation_mode = mode
                context.scene.cursor.rotation_euler = euler

        self.report({'INFO'}, f"游标已移至 {len(points)} 个元素的中心")
        return {'FINISHED'}


classes = (RARA_OT_CursorToSelected,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
