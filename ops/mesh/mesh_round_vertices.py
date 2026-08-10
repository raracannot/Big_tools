# 规整网格

import bpy
import bmesh
import mathutils


# ==========================================
# Operator
# ==========================================
class RARA_OT_Model_RoundVertices(bpy.types.Operator):
    bl_idname = "rara.model_round_vertices"
    bl_label = "规整网格"
    bl_description = "将选中顶点按指定单位和坐标系对齐到网格\n支持三轴独立控制和世界/局部坐标系"
    bl_options = {'REGISTER', 'UNDO'}

    round_x: bpy.props.BoolProperty(name="X轴", default=True)
    round_y: bpy.props.BoolProperty(name="Y轴", default=True)
    round_z: bpy.props.BoolProperty(name="Z轴", default=True)

    coordinate_system: bpy.props.EnumProperty(
        name="坐标系统",
        items=[
            ('WORLD', "世界坐标", "使用世界坐标系"),
            ('LOCAL', "局部坐标", "使用对象局部坐标系"),
        ],
        default='WORLD')

    round_unit: bpy.props.FloatProperty(
        name="规整单位", default=0.0, min=0.0, max=100.0,
        unit="LENGTH", precision=6,
        description="输入0时不执行规整")

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.edit_object and context.edit_object.type == 'MESH'

    def execute(self, context):
        obj = context.edit_object
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "需要编辑模式下的网格对象")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        selected = [v for v in bm.verts if v.select]
        if not selected:
            self.report({'WARNING'}, "没有选中的顶点")
            return {'CANCELLED'}
        if self.round_unit == 0.0:
            self.report({'INFO'}, "规整单位为0，未执行规整")
            return {'FINISHED'}

        for v in selected:
            if self.coordinate_system == 'WORLD':
                wc = obj.matrix_world @ v.co
            else:
                wc = v.co.copy()

            ru = self.round_unit
            if self.round_x:
                wc.x = round(wc.x / ru) * ru
            if self.round_y:
                wc.y = round(wc.y / ru) * ru
            if self.round_z:
                wc.z = round(wc.z / ru) * ru

            if self.coordinate_system == 'WORLD':
                v.co = obj.matrix_world.inverted() @ wc
            else:
                v.co = wc

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"规整完成，处理了 {len(selected)} 个顶点")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.label(text="规整轴向:")
        row.prop(self, "round_x")
        row.prop(self, "round_y")
        row.prop(self, "round_z")
        layout.prop(self, "coordinate_system")
        layout.prop(self, "round_unit")


classes = (RARA_OT_Model_RoundVertices,)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)