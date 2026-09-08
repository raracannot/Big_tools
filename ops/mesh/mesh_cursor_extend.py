# 延伸至游标

import bpy
import bmesh
from mathutils import Vector
from mathutils import geometry


class RARA_OT_ExtendToCursor(bpy.types.Operator):
    bl_idname = "rara.model_extend_to_cursor"
    bl_label = "延伸至游标"
    bl_description = "将选中边线距游标平面较近的端点沿边线方向延伸至游标所在平面"
    bl_options = {'REGISTER', 'UNDO'}

    extend_far_point: bpy.props.BoolProperty(name="延伸较远点", default=False)

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

            if (self.extend_far_point and d1 >= d2) or (not self.extend_far_point and d1 < d2):
                moving_vert = v1
                static_vert = v2
            else:
                moving_vert = v2
                static_vert = v1

            line_dir = moving_vert.co - static_vert.co
            intersection = geometry.intersect_line_plane(
                static_vert.co, line_dir, cursor_local, normal_local
            )
            if intersection:
                moving_vert.co = intersection
                moved += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"已延伸 {moved} 条边线至游标")
        return {'FINISHED'}


classes = (RARA_OT_ExtendToCursor,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
