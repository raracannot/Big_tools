# 对调隐藏

import bpy
import bmesh
    
class RARA_OT_Model_ToggleHidden(bpy.types.Operator):
    bl_idname = "rara.model_toggle_hidden"
    bl_label = "对调隐藏"
    bl_description = "对调隐藏和未隐藏的元素\n将隐藏项可见、将可见项隐藏"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.mode in {'EDIT_MESH', 'OBJECT'}

    def execute(self, context):
        mode = context.mode
        if mode == 'EDIT_MESH':
            obj = context.active_object
            bm = bmesh.from_edit_mesh(obj.data)
            visible_verts = {v.index for v in bm.verts if not v.hide}
            visible_edges = {e.index for e in bm.edges if not e.hide}
            visible_faces = {f.index for f in bm.faces if not f.hide}
            bpy.ops.mesh.reveal(select=False)
            for v in bm.verts:
                if v.index in visible_verts:
                    v.hide = True
            for e in bm.edges:
                if e.index in visible_edges:
                    e.hide = True
            for f in bm.faces:
                if f.index in visible_faces:
                    f.hide = True
            bmesh.update_edit_mesh(obj.data)
        elif mode in ['EDIT_CURVE', 'EDIT_SURFACE']:
            obj = context.active_object
            curve = obj.data
            visible_points = set()
            for s in curve.splines:
                for i, p in enumerate(s.bezier_points):
                    if not p.hide:
                        visible_points.add((s, i))
            bpy.ops.curve.reveal(select=False)
            for s in curve.splines:
                for i, p in enumerate(s.bezier_points):
                    if (s, i) in visible_points:
                        p.hide = True
        elif mode == 'EDIT_METABALL':
            obj = context.active_object
            metaball = obj.data
            visible_elements = set()
            for i, element in enumerate(metaball.elements):
                if not element.hide:
                    visible_elements.add(i)
            bpy.ops.mball.reveal_metaelems(select=False)
            for i, element in enumerate(metaball.elements):
                if i in visible_elements:
                    element.hide = True
        else:
            for obj in context.view_layer.objects:
                obj.hide_set(not obj.hide_get())
        return {'FINISHED'}
        
classes = (
    RARA_OT_Model_ToggleHidden,
)

# def toggle_hidden_draw(self, context):
    # layout = self.layout
    # layout.operator("rara.model_toggle_hidden")

    
def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
    # bpy.types.VIEW3D_MT_edit_mesh_showhide.append(toggle_hidden_draw)
    # bpy.types.VIEW3D_MT_object_showhide.append(toggle_hidden_draw)

def unregister():
    # bpy.types.VIEW3D_MT_edit_mesh_showhide.remove(toggle_hidden_draw)
    # bpy.types.VIEW3D_MT_object_showhide.remove(toggle_hidden_draw)
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)