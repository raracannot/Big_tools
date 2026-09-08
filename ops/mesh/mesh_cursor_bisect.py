# 切分至游标

import bpy
from mathutils import Vector


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


classes = (RARA_OT_BisectToCursor,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
