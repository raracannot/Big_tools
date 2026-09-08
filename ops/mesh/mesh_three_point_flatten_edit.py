# 三点拍平 (编辑模式)

import bpy
import bmesh
from bpy_extras import view3d_utils
from ...utils.gpu_utils import draw_lines_2d
from ...utils.base_mixin import BaseModalMixin
from ...utils.draw_callbacks import draw_widget_3d, draw_flatten


# ==========================================
# Flatten Preview (Edit mode)
# ==========================================
class FlattenPreview:
    def __init__(self):
        self.edges_world = []

    def build_edit_preview(self, context):
        self.edges_world.clear()
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return
        bm = bmesh.from_edit_mesh(obj.data)
        world_mat = obj.matrix_world

        for e in bm.edges:
            if e.verts[0].select or e.verts[1].select:
                self.edges_world.append((
                    world_mat @ e.verts[0].co,
                    world_mat @ e.verts[1].co,
                    e.verts[0].select,
                    e.verts[1].select
                ))

    def draw(self, context, plane_center, normal, color=(0.8, 0.2, 1.0, 0.9)):
        if not self.edges_world or not plane_center or not normal:
            return

        region = context.region
        rv3d = context.space_data.region_3d
        flattened_lines_2d = []

        for v1, v2, sel1, sel2 in self.edges_world:
            p1 = v1 - (v1 - plane_center).dot(normal) * normal if sel1 else v1
            p2 = v2 - (v2 - plane_center).dot(normal) * normal if sel2 else v2

            co1 = view3d_utils.location_3d_to_region_2d(region, rv3d, p1)
            co2 = view3d_utils.location_3d_to_region_2d(region, rv3d, p2)

            if co1 and co2:
                flattened_lines_2d.extend((co1, co2))

        if flattened_lines_2d:
            draw_lines_2d(flattened_lines_2d, color, line_width=2.0)


class RARA_OT_Model_FlattenEdit(BaseModalMixin, bpy.types.Operator):
    bl_idname = "rara.model_three_points_flatten_edit"
    bl_label = "三点拍平 (编辑模式)"
    bl_description = "在编辑模式下通过三个控制点构建虚拟平面，将选中顶点投影拍平至该平面\n【左键】放置/拖动控制点\n【X】删除当前高亮控制点\n【回车】确认执行拍平\n【ESC/右键】取消退出"
    bl_options = {'REGISTER', 'UNDO'}
    expected_mode = 'EDIT_MESH'

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH' and context.mode in {'EDIT_MESH', 'OBJECT'}

    def invoke(self, context, event):
        if context.mode != self.expected_mode:
            self.report({'WARNING'}, "请在编辑模式下使用此工具")
            return {'CANCELLED'}
        self._init_snapper_widget(context)
        self.preview_drawer = FlattenPreview()
        self.preview_drawer.build_edit_preview(context)
        args = (self, context)
        self._add_draw_handler_3d(draw_widget_3d, args)
        self._add_draw_handler_2d(draw_flatten, args)
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        msg = f"【拍平至面】已选点: {len(self.widget.points)}/3 | 左键: 放置/拖动 | X: 删除 | 回车: 确认拍平 | ESC: 取消"
        self._set_status(context, msg)

    def modal(self, context, event):
        try:
            if context.mode != self.expected_mode:
                self.finish(context)
                return {'CANCELLED'}
            context.area.tag_redraw()

            if event.type == 'TIMER':
                return {'PASS_THROUGH'}

            if event.type in {'ESC', 'RIGHTMOUSE'}:
                self.finish(context)
                return {'CANCELLED'}

            if event.type == 'X' and event.value == 'PRESS':
                self._handle_delete_point()
                self.update_header(context)
                return {'RUNNING_MODAL'}

            if event.type == 'MOUSEMOVE':
                self._handle_mouse_move(context, event)
                return {'RUNNING_MODAL'}

            if event.type == 'LEFTMOUSE':
                if event.value == 'PRESS':
                    self._handle_left_mouse_press(context, event)
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.value == 'RELEASE':
                    self.widget.drag_idx = None
                    return {'RUNNING_MODAL'}

            if event.type == 'RET' and event.value == 'PRESS':
                if len(self.widget.points) in {2, 3}:
                    self.execute_flatten(context)
                    self.finish(context)
                    return {'FINISHED'}
                else:
                    self.report({'WARNING'}, "请至少绘制2个点")
                    return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"拍平至面出错: {str(e)}")
            return {'CANCELLED'}

    def execute_flatten(self, context):
        try:
            plane_center, normal = self.widget.get_plane_params(context)
            if not plane_center or not normal:
                self.report({'ERROR'}, "点位共线或无效，无法构成平面")
                return

            obj = context.active_object
            bm = bmesh.from_edit_mesh(obj.data)
            world_matrix = obj.matrix_world
            inv_world_matrix = world_matrix.inverted()

            for v in bm.verts:
                if v.select:
                    world_co = world_matrix @ v.co
                    distance = (world_co - plane_center).dot(normal)
                    projected_world_co = world_co - distance * normal
                    v.co = inv_world_matrix @ projected_world_co

            bmesh.update_edit_mesh(obj.data)
            self.report({'INFO'}, "顶点已精确拍平至面")
        except Exception as e:
            self.report({'ERROR'}, f"执行拍平时出错: {str(e)}")

    def finish(self, context):
        self._finish_common(context)


classes = (RARA_OT_Model_FlattenEdit,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
