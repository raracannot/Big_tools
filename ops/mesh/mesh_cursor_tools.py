# 游标延伸&拍平&切分

import bpy
import bmesh
from mathutils import Vector
from mathutils import geometry


class RARA_OT_ExtendToCursor(bpy.types.Operator):
    bl_idname = "rara.model_extend_to_cursor"
    bl_label = "延伸至游标"
    bl_description = "将选中边线的远端顶点沿边线方向延伸至游标所在 Z 平面"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH':
            return False
        obj = context.active_object
        return obj and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        cursor = context.scene.cursor
        cursor_loc = cursor.location
        cursor_z = cursor.matrix.to_3x3() @ Vector((0, 0, 1))
        cursor_z.normalize()

        inv_mat = obj.matrix_world.inverted()
        cursor_local = inv_mat @ cursor_loc
        normal_local = (inv_mat.to_3x3() @ cursor_z).normalized()

        bm = bmesh.from_edit_mesh(obj.data)
        moved = 0

        for e in bm.edges:
            if not e.select:
                continue
            v1, v2 = e.verts
            d1 = abs((v1.co - cursor_local).dot(normal_local))
            d2 = abs((v2.co - cursor_local).dot(normal_local))

            far_vert = v1 if d1 >= d2 else v2
            near_vert = v2 if d1 >= d2 else v1

            line_dir = far_vert.co - near_vert.co
            intersection = geometry.intersect_line_plane(
                near_vert.co, line_dir, cursor_local, normal_local
            )
            if intersection:
                far_vert.co = intersection
                moved += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"已延伸 {moved} 条边线至游标")
        return {'FINISHED'}


class RARA_OT_FlattenToCursor(bpy.types.Operator):
    bl_idname = "rara.model_flatten_to_cursor"
    bl_label = "拍平至游标"
    bl_description = "将选中顶点拍平至游标所在 Z 平面"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH':
            return False
        obj = context.active_object
        return obj and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        cursor = context.scene.cursor
        cursor_loc = cursor.location
        cursor_z = cursor.matrix.to_3x3() @ Vector((0, 0, 1))
        cursor_z.normalize()

        inv_mat = obj.matrix_world.inverted()
        cursor_local = inv_mat @ cursor_loc
        normal_local = (inv_mat.to_3x3() @ cursor_z).normalized()

        bm = bmesh.from_edit_mesh(obj.data)
        moved = 0

        for v in bm.verts:
            if v.select:
                dist = (v.co - cursor_local).dot(normal_local)
                v.co = v.co - dist * normal_local
                moved += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"已将 {moved} 个顶点拍平至游标")
        return {'FINISHED'}


class RARA_OT_BisectToCursor(bpy.types.Operator):
    bl_idname = "rara.model_bisect_to_cursor"
    bl_label = "切分至游标"
    bl_description = "沿游标 Z 平面对选中网格进行切分"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH':
            return False
        obj = context.active_object
        return obj and obj.type == 'MESH'

    def execute(self, context):
        cursor = context.scene.cursor
        cursor_loc = cursor.location
        cursor_z = cursor.matrix.to_3x3() @ Vector((0, 0, 1))
        cursor_z.normalize()

        bpy.ops.mesh.bisect(
            plane_co=cursor_loc,
            plane_no=cursor_z,
            use_fill=False,
            clear_inner=False,
            clear_outer=False,
        )
        self.report({'INFO'}, "已沿游标平面切分网格")
        return {'FINISHED'}


classes = (RARA_OT_ExtendToCursor, RARA_OT_FlattenToCursor, RARA_OT_BisectToCursor,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
