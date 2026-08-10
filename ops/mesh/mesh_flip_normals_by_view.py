# 以视口反转法向

import bpy
import bmesh
import mathutils


class RARA_OT_ModelFlipNormalsByView(bpy.types.Operator):
    bl_idname = "rara.model_flip_normals_by_view"
    bl_label = "以视口反转法向"
    bl_description = "将选中的网格的法向朝向视口"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.edit_object
        if not obj:
            obj = context.active_object
        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            me = obj.data
            bm = bmesh.from_edit_mesh(me)
            space = context.space_data
            region_3d = space.region_3d
            if region_3d.is_perspective:
                cam_location = region_3d.view_matrix.inverted().translation
            for face in bm.faces:
                if face.select:
                    if region_3d.is_perspective:
                        normal_to_cam = (cam_location - face.calc_center_median()).normalized()
                    else:
                        normal_to_cam = region_3d.view_rotation @ mathutils.Vector((0.0, 0.0, 1.0))
                    if face.normal.dot(normal_to_cam) < 0:
                        face.normal_flip()
            bmesh.update_edit_mesh(me)
        else:
            self.report({'WARNING'}, "请选择一个编辑模式下的网格对象")
        return {'FINISHED'}


classes = (RARA_OT_ModelFlipNormalsByView,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
