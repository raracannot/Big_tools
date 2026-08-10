# 模型切片

import bpy
import bmesh
import mathutils


class RARA_OT_ModelSlice(bpy.types.Operator):
    bl_idname = "rara.model_slice"
    bl_label = "模型切片"
    bl_description = "根据输入的分段数，对选中的网格进行分层切片划线"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    axis_mode: bpy.props.EnumProperty(
        name="轴向模式",
        description="选择轴向模式",
        items=[
            ('WORLD', "世界轴", "依据世界轴"),
            ('LOCAL', "局部轴", "依据局部轴"),
            ('CURSOR', "游标轴", "依据游标轴"),
            ('VIEW', "视口轴", "依据视口轴向")
        ],
        default='WORLD')
    only_selected: bpy.props.BoolProperty(
        name="仅选中面", default=True,
        description="切分将仅在选中项范围内完成")
    x_segments: bpy.props.IntProperty(name="X轴分段数", default=1, min=0)
    y_segments: bpy.props.IntProperty(name="Y轴分段数", default=1, min=0)
    z_segments: bpy.props.IntProperty(name="Z轴分段数", default=1, min=0)
    threshold: bpy.props.FloatProperty(name="轴阈值", default=0.001, min=0, max=1)

    def _cut_mesh(self, obj, axis, segments, min_point, max_point, unselected_vert_indices, unselected_edge_indices, unselected_face_indices, only_selected, new_edges):
        min_val = min_point[axis]
        max_val = max_point[axis]
        if segments > 1:
            step = (max_val - min_val) / segments
            for i in range(1, segments):
                plane_co = [0, 0, 0]
                plane_no = [0, 0, 0]
                plane_co[axis] = min_val + i * step
                plane_no[axis] = 1

                plane_co = mathutils.Vector(plane_co)
                plane_no = mathutils.Vector(plane_no)

                bpy.ops.mesh.select_all(action='SELECT')

                bm = bmesh.from_edit_mesh(obj.data)
                bm.verts.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
                bm.faces.ensure_lookup_table()

                if only_selected:
                    for v_idx in unselected_vert_indices:
                        if v_idx < len(bm.verts):
                            bm.verts[v_idx].select = False
                    for e_idx in unselected_edge_indices:
                        if e_idx < len(bm.edges):
                            bm.edges[e_idx].select = False
                    for f_idx in unselected_face_indices:
                        if f_idx < len(bm.faces):
                            bm.faces[f_idx].select = False

                bmesh.update_edit_mesh(obj.data)

                bpy.ops.mesh.bisect(
                    plane_co=plane_co,
                    plane_no=plane_no,
                    use_fill=False,
                    clear_inner=False,
                    clear_outer=False,
                    threshold=self.threshold)

                bm = bmesh.from_edit_mesh(obj.data)
                new_edges += [e for e in bm.edges if e.select]
        return new_edges

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            if self.axis_mode == 'WORLD':
                rotation_matrix = mathutils.Matrix.Identity(4)
            elif self.axis_mode == 'LOCAL':
                rotation_matrix = obj.matrix_world.copy()
            elif self.axis_mode == 'CURSOR':
                rotation_matrix = context.scene.cursor.rotation_euler.to_matrix().to_4x4()
            elif self.axis_mode == 'VIEW':
                region = context.region
                rv3d = context.space_data.region_3d
                view_matrix = rv3d.view_matrix
                rotation_matrix = view_matrix.inverted().to_4x4()

            original_matrix = obj.matrix_world.copy()
            try:
                bm = bmesh.from_edit_mesh(obj.data)
                selected_verts = [v for v in bm.verts if v.select]
                if not selected_verts:
                    self.report({'WARNING'}, "没有选中的顶点")
                    return {'CANCELLED'}
                unselected_vert_indices = {v.index for v in bm.verts if not v.select}
                unselected_edge_indices = {e.index for e in bm.edges if not e.select}
                unselected_face_indices = {f.index for f in bm.faces if not f.select}
                new_edges = []

                obj.matrix_world = rotation_matrix.inverted() @ obj.matrix_world

                min_x, max_x = float('inf'), float('-inf')
                min_y, max_y = float('inf'), float('-inf')
                min_z, max_z = float('inf'), float('-inf')

                for vertex in selected_verts:
                    v = (obj.matrix_world @ mathutils.Vector((vertex.co.x, vertex.co.y, vertex.co.z, 1))).xyz
                    min_x = min(min_x, v.x)
                    max_x = max(max_x, v.x)
                    min_y = min(min_y, v.y)
                    max_y = max(max_y, v.y)
                    min_z = min(min_z, v.z)
                    max_z = max(max_z, v.z)

                min_point = mathutils.Vector((min_x, min_y, min_z))
                max_point = mathutils.Vector((max_x, max_y, max_z))

                self._cut_mesh(obj, 0, self.x_segments, min_point, max_point, unselected_vert_indices, unselected_edge_indices, unselected_face_indices, self.only_selected, new_edges)
                self._cut_mesh(obj, 1, self.y_segments, min_point, max_point, unselected_vert_indices, unselected_edge_indices, unselected_face_indices, self.only_selected, new_edges)
                self._cut_mesh(obj, 2, self.z_segments, min_point, max_point, unselected_vert_indices, unselected_edge_indices, unselected_face_indices, self.only_selected, new_edges)

                bmesh.update_edit_mesh(obj.data)
                bpy.ops.mesh.select_all(action='DESELECT')
                for e in new_edges:
                    e.select = True

                obj.matrix_world = rotation_matrix @ obj.matrix_world
                bpy.context.view_layer.update()

                self.report({'INFO'}, "网格分层切分成功")
            except Exception as e:
                obj.matrix_world = original_matrix
                self.report({'ERROR'}, f"模型切片失败: {e}")
                return {'CANCELLED'}
        else:
            self.report({'WARNING'}, "请选中一个网格对象并进入编辑模式")
        return {'FINISHED'}


classes = (RARA_OT_ModelSlice,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)