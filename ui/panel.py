import bpy


class RARA_PT_MainPanel(bpy.types.Panel):
    bl_label = "工具集"
    bl_idname = "RARA_PT_MIRROR_PANEL"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Edit'

    def draw(self, context):
        layout = self.layout
        col = layout.column()#align=True
        col.operator("rara.reload_addon", text="重载模块", icon='FILE_REFRESH')
        col.separator()
        
        if context.mode == 'EDIT_MESH':
            # col.label(text="三点工具", icon='EDITMODE_HLT')
            space_data = context.space_data

            box=col.box()
            row = box.row(align=True)
            row.operator("mesh.hide", text="", icon="VIS_SEL_01").unselected=False #隐藏所选
            row.operator("mesh.hide", text="", icon="VIS_SEL_00").unselected=True #隐藏未选
            row.operator("rara.model_toggle_hidden", text="", icon="UV_SYNC_SELECT") #对调选择
            row.separator()
            row.operator("mesh.reveal", text="",icon="HIDE_OFF") #全部可见
            row.label(text="")
            row.prop(space_data,"clip_start")
            row.prop(space_data,"clip_end")
            row = box.row(align=True)
            row.prop(space_data.overlay,"show_retopology",text="",icon="MOD_MESHDEFORM")
            row.prop(space_data.shading,"show_xray",text="",icon="MOD_OPACITY")
            row.prop(space_data.overlay,"show_face_orientation",text="",icon="NORMALS_FACE")
            row.separator()
            row.operator("mesh.flip_normals", text="反转法向")
            row.operator("rara.model_flip_normals_by_view", text="以视口设法向")

            box=col.box()
            row = box.row()
            row.operator("rara.cursor_to_selected", icon='PIVOT_CURSOR')
            col.separator()

            box=col.box()
            row = box.row()
            row.operator("rara.model_interactive_array_mesh", text="网格阵列")
            row.operator("rara.model_three_point_align", text="三点对齐")
            row.operator("rara.model_three_points_mirror_edit", text="三点镜像")
            row = box.row()
            row.operator("rara.model_three_points_extend_edit", text="延伸至面")
            row.operator("rara.model_three_points_flatten_edit", text="拍平至面")
            row.operator("rara.model_three_points_bisect_edit", text="切分网格")
            # col.separator()
            row = box.row()
            row.operator("rara.model_extend_to_cursor", text="延伸至游标")
            row.operator("rara.model_flatten_to_cursor", text="拍平至游标")
            row.operator("rara.model_bisect_to_cursor", text="切分游标")
            col.separator()
            
            box=col.box()
            row = box.row()
            row.operator("rara.model_slide_edge", text="滑移复制边线")
            row.operator("rara.model_mesh_curvature_slide", text="保持曲率滑移")
            row.operator("rara.model_mesh_free_curvature_slide", text="自由曲率滑移")

            box=col.box()
            row = box.row()
            row.operator("rara.model_evenly_distribute", text="均匀分布")
            row.operator("rara.model_bridge_loop", text="简易桥接")
            row.operator("rara.model_circle_edges", text="圆化边线")
            row = box.row()
            row.operator("rara.model_resample_edges_segments_preserve", text="重采样 [段数]")
            row.operator("rara.model_resample_edges_length_preserve", text="重采样 [长度]")
            col.separator()

            box=col.box()
            row = box.row()
            row.operator("rara.model_weld_verts_to_edges", text="焊接到边")
            row.operator("rara.model_vertical_line", text="垂直线")
            row.operator("rara.model_intersect_edges", text="交点打断")
            row.operator("rara.model_extend_edges", text="延申线")
            row = box.row()
            row.operator("rara.model_find_circle_center", text="反求圆心")
            row.operator("rara.model_round_vertices", text="规整网格")
            row.operator("rara.model_slice", text="模型切片")
            row.operator("rara.model_smooth_bubble", text="平滑起泡")

            row = box.row()
            row.operator("rara.model_visual_align", text="拍平")

            row = box.row()
            row.operator("rara.model_measure", text="测量")
            row.operator("rara.model_delete_loose", text="删除松散")
            col.separator()

            box=col.box()
            row = box.row()
            row.operator("rara.model_copy_elements", text="复制网格元素", icon="COPYDOWN")
            row.operator("rara.model_paste_elements", text="粘贴网格元素", icon="PASTEDOWN")

        elif context.mode == 'OBJECT':
            space_data = context.space_data

            box=col.box()
            row = box.row(align=True)
            row.operator("object.hide_view_set", text="", icon="VIS_SEL_01").unselected=False #隐藏所选
            row.operator("object.hide_view_set", text="", icon="VIS_SEL_00").unselected=True #隐藏未选
            row.operator("rara.model_toggle_hidden", text="", icon="UV_SYNC_SELECT") #对调选择
            row.separator()
            row.operator("object.hide_view_clear", text="",icon="HIDE_OFF") #全部可见
            row.label(text="")
            row.prop(space_data,"clip_start")
            row.prop(space_data,"clip_end")
            row = box.row(align=True)

            row.prop(space_data.overlay,"show_wireframes",text="",icon="MESH_ICOSPHERE")
            row.prop(space_data.shading,"show_xray",text="",icon="MOD_OPACITY")
            row.prop(space_data.overlay,"show_face_orientation",text="",icon="NORMALS_FACE")
            # col.separator()

            box=col.box()
            row = box.row()
            row.operator("rara.cursor_to_selected", icon='PIVOT_CURSOR')
            col.separator()

            box=col.box()
            row = box.row()
            row.operator("rara.model_interactive_array_object", text="物体阵列")
            row.operator("rara.model_three_point_align", text="三点对齐")
            row.operator("rara.model_three_points_mirror_object", text="物体镜像", icon='DUPLICATE')
            row = box.row()
            row.operator("rara.model_visual_layout_align", text="对象对齐")
            row.operator("rara.model_three_points_flatten_object", text="拍平至面", icon='SNAP_FACE')
            row.operator("rara.model_three_points_bisect_object", text="切分网格", icon='MESH_CUBE')
            col.separator()

            box=col.box()
            row = box.row()
            row.operator("rara.model_viewport_move_grid", text="沿视口移动网格")
            row.operator("rara.model_mirror_grid", text="绘制镜像")
            row.operator("rara.model_origin_picker", text="可视化原点选择器")
            row = box.row()
            row.operator("rara.model_fix_rotation", text="修复旋转(SVD)")
            row.operator("rara.model_fix_rotation_by_normals", text="修复旋转(法线OBB)")

            col.separator()

            box=col.box()
            row = box.row()
            row.operator("view3d.copybuffer", text="复制元素", icon="COPYDOWN")
            row.operator("view3d.pastebuffer", text="粘贴元素", icon="PASTEDOWN")

        else:
            col.label(text="请在编辑模式或物体模式下使用", icon='INFO')


classes = (RARA_PT_MainPanel,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)