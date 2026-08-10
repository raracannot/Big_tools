# 删除松散几何体
import bpy
import bmesh


class RARA_OT_ModelDeleteLoose(bpy.types.Operator):
    bl_idname = "rara.model_delete_loose"
    bl_label = "删除松散几何体"
    bl_description = "删除松散的顶点、边和面"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            loose_verts = [v for v in bm.verts if not v.link_edges]
            loose_edges = [e for e in bm.edges if not e.link_faces]
            loose_faces = [f for f in bm.faces if len(f.edges) < 3]
            if loose_verts or loose_edges or loose_faces:
                if loose_faces:
                    bmesh.ops.delete(bm, geom=loose_faces, context='FACES')
                if loose_edges:
                    bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')
                if loose_verts:
                    bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')
                bmesh.update_edit_mesh(obj.data)
                self.report({'INFO'}, "成功删除松散几何体")
            else:
                self.report({'WARNING'}, "没有找到松散的几何体")
        else:
            self.report({'WARNING'}, "请选择一个编辑模式下的网格对象")
        return {'FINISHED'}


classes = (RARA_OT_ModelDeleteLoose,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)