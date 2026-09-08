# 拍平至游标

import bpy
import bmesh
from mathutils import Vector


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


classes = (RARA_OT_FlattenToCursor,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
