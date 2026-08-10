# 三点切分

import gpu
import bpy
import bmesh
from mathutils import Vector
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from ...utils.gpu_utils import SHADER, draw_circle_2d, draw_text_2d, draw_lines_2d
from ...utils.base_mixin import BaseModalMixin
from ...utils.draw_callbacks import draw_widget_3d, draw_bisect, draw_bisect_object


class BisectPreview:
    def draw(self, context, plane_center, normal, use_fill, clear_inner, clear_outer):
        if not plane_center or not normal:
            return
        obj = context.active_object
        if not obj:
            return

        from ...utils.gpu_utils import SHADER
        max_axis = max(obj.dimensions)
        size = max_axis * 1.5 if max_axis > 0 else 10.0
        arrow_len = size * 0.3

        region = context.region
        rv3d = context.space_data.region_3d

        outer_end = plane_center + normal * arrow_len
        inner_end = plane_center - normal * arrow_len

        co_center = view3d_utils.location_3d_to_region_2d(region, rv3d, plane_center)
        co_outer = view3d_utils.location_3d_to_region_2d(region, rv3d, outer_end)
        co_inner = view3d_utils.location_3d_to_region_2d(region, rv3d, inner_end)

        if not (co_center and co_outer and co_inner):
            return

        outer_color = (1.0, 0.0, 0.0, 1.0) if clear_outer else (0.0, 0.5, 0.0, 1.0)
        inner_color = (1.0, 0.0, 0.0, 1.0) if clear_inner else (0.0, 0.0, 0.5, 1.0)
        bg_color = (1.0, 1.0, 1.0, 1.0)

        gpu.state.blend_set('ALPHA')
        SHADER.bind()

        SHADER.uniform_float("color", outer_color)
        batch_for_shader(SHADER, 'LINES', {"pos": [co_center, co_outer]}).draw(SHADER)

        SHADER.uniform_float("color", inner_color)
        batch_for_shader(SHADER, 'LINES', {"pos": [co_center, co_inner]}).draw(SHADER)

        if use_fill:
            draw_circle_2d(co_center, 9, bg_color, filled=True)
        else:
            draw_circle_2d(co_center, 9, bg_color, filled=False)

        draw_circle_2d(co_outer, 12, bg_color, filled=True)
        draw_text_2d("O", (co_outer[0] - 6, co_outer[1] - 6), outer_color, size=14)
        draw_circle_2d(co_inner, 12, bg_color, filled=True)
        draw_text_2d("I", (co_inner[0] - 3, co_inner[1] - 6), inner_color, size=14)
        gpu.state.blend_set('NONE')


class RARA_OT_Model_BisectEdit(BaseModalMixin, bpy.types.Operator):
    bl_idname = "rara.model_three_points_bisect_edit"
    bl_label = "三点切分 (编辑模式)"
    bl_description = "在编辑模式下通过三个控制点构建切分平面，对网格进行切分操作\n【左键】放置/拖动控制点\n【X】删除当前高亮控制点\n【F】切换填充切口开关\n【I】切换清空内侧开关\n【O】切换清空外侧开关\n【Shift+滚轮】调整切分阈值\n【回车】确认执行切分\n【ESC/右键】取消退出"
    bl_options = {'REGISTER', 'UNDO'}
    expected_mode = 'EDIT_MESH'

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH' and context.mode in {'EDIT_MESH', 'OBJECT'}

    use_fill: bpy.props.BoolProperty(default=False)
    clear_inner: bpy.props.BoolProperty(default=False)
    clear_outer: bpy.props.BoolProperty(default=False)
    threshold: bpy.props.FloatProperty(default=0.0001, min=0.0, max=1.0)

    def invoke(self, context, event):
        if context.mode != self.expected_mode:
            self.report({'WARNING'}, "请在编辑模式下使用此工具")
            return {'CANCELLED'}
        self._init_snapper_widget(context)
        self.preview_drawer = BisectPreview()
        args = (self, context)
        self._add_draw_handler_3d(draw_widget_3d, args)
        self._add_draw_handler_2d(draw_bisect, args)
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        msg = (f"【切分网格】已选点: {len(self.widget.points)}/3 | "
               f"填充(F): {'开' if self.use_fill else '关'} | "
               f"清空内侧(I): {'开' if self.clear_inner else '关'} | "
               f"清空外侧(O): {'开' if self.clear_outer else '关'} | "
               f"阈值(Shift+滚轮): {self.threshold:.4f} | "
               f"回车: 确认切分 | ESC: 取消")
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

            if event.value == 'PRESS':
                if event.type == 'F':
                    self.use_fill = not self.use_fill
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'I':
                    self.clear_inner = not self.clear_inner
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'O':
                    self.clear_outer = not self.clear_outer
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'X':
                    self._handle_delete_point()
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'RET':
                    if len(self.widget.points) in {2, 3}:
                        self.execute_bisect(context)
                        self.finish(context)
                        return {'FINISHED'}
                    else:
                        self.report({'WARNING'}, "请至少绘制2个点")
                        return {'RUNNING_MODAL'}

            if event.shift and event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                step = 0.001 if event.type == 'WHEELUPMOUSE' else -0.001
                self.threshold = max(0.0, min(1.0, self.threshold + step))
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

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"三点切分出错: {str(e)}")
            return {'CANCELLED'}

    def execute_bisect(self, context):
        try:
            plane_center, normal = self.widget.get_plane_params(context)
            if not plane_center or not normal:
                self.report({'ERROR'}, "点位共线或无效，无法构成平面")
                return

            bpy.ops.mesh.bisect(
                plane_co=plane_center,
                plane_no=normal,
                use_fill=self.use_fill,
                clear_inner=self.clear_inner,
                clear_outer=self.clear_outer,
                threshold=self.threshold
            )
            self.report({'INFO'}, "网格切分完成")
        except Exception as e:
            self.report({'ERROR'}, f"执行切分时出错: {str(e)}")

    def finish(self, context):
        self._finish_common(context)


class RARA_OT_Model_BisectObject(BaseModalMixin, bpy.types.Operator):
    bl_idname = "rara.model_three_points_bisect_object"
    bl_label = "三点切分 (物体模式)"
    bl_description = "在物体模式下通过三个控制点构建切分平面，将选中网格物体一分为二（原物体保留内侧，复制体保留外侧）\n【左键】放置/拖动控制点\n【X】删除当前高亮控制点\n【F】切换填充切口开关\n【Shift+滚轮】调整切分阈值\n【回车】确认执行切分\n【ESC/右键】取消退出"
    bl_options = {'REGISTER', 'UNDO'}
    expected_mode = 'OBJECT'

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH' and context.mode in {'EDIT_MESH', 'OBJECT'}

    use_fill: bpy.props.BoolProperty(default=False)
    threshold: bpy.props.FloatProperty(default=0.0001, min=0.0, max=1.0)

    def invoke(self, context, event):
        if context.mode != self.expected_mode:
            self.report({'WARNING'}, "请在物体模式下使用此工具")
            return {'CANCELLED'}
        if not any(obj.type == 'MESH' for obj in context.selected_objects):
            self.report({'WARNING'}, "请先选中至少一个网格对象！")
            return {'CANCELLED'}
        self._init_snapper_widget(context)
        args = (self, context)
        self._add_draw_handler_3d(draw_widget_3d, args)
        self._add_draw_handler_2d(draw_bisect_object, args)
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        msg = (f"【物体切分】已选点: {len(self.widget.points)}/3 | "
               f"填充(F): {'开' if self.use_fill else '关'} | "
               f"阈值(Shift+滚轮): {self.threshold:.4f} | "
               f"回车: 确认切分 | ESC: 取消")
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

            if event.value == 'PRESS':
                if event.type == 'F':
                    self.use_fill = not self.use_fill
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'X':
                    self._handle_delete_point()
                    self.update_header(context)
                    return {'RUNNING_MODAL'}
                elif event.type == 'RET':
                    if len(self.widget.points) in {2, 3}:
                        self.execute_bisect(context)
                        self.finish(context)
                        return {'FINISHED'}
                    else:
                        self.report({'WARNING'}, "请至少绘制2个点")
                        return {'RUNNING_MODAL'}

            if event.shift and event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                step = 0.001 if event.type == 'WHEELUPMOUSE' else -0.001
                self.threshold = max(0.0, min(1.0, self.threshold + step))
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

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"物体切分出错: {str(e)}")
            return {'CANCELLED'}

    def execute_bisect(self, context):
        try:
            plane_center, normal = self.widget.get_plane_params(context)
            if not plane_center or not normal:
                self.report({'ERROR'}, "点位共线或无效，无法构成平面")
                return

            selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
            final_selected = []

            for obj in selected_meshes:
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                context.view_layer.objects.active = obj
                bpy.ops.object.duplicate()
                obj_copy = context.active_object

                bpy.ops.object.select_all(action='DESELECT')
                context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.bisect(plane_co=plane_center, plane_no=normal, use_fill=self.use_fill, clear_inner=True, clear_outer=False, threshold=self.threshold)
                bpy.ops.object.mode_set(mode='OBJECT')
                bpy.ops.object.select_all(action='DESELECT')

                context.view_layer.objects.active = obj_copy
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.bisect(plane_co=plane_center, plane_no=normal, use_fill=self.use_fill, clear_inner=False, clear_outer=True, threshold=self.threshold)
                bpy.ops.object.mode_set(mode='OBJECT')
                final_selected.extend([obj, obj_copy])

            bpy.ops.object.select_all(action='DESELECT')
            for obj in final_selected:
                obj.select_set(True)
            if final_selected:
                context.view_layer.objects.active = final_selected[0]

            self.report({'INFO'}, "对象切分完成")
        except Exception as e:
            self.report({'ERROR'}, f"执行物体切分时出错: {str(e)}")

    def finish(self, context):
        self._finish_common(context)


classes = (RARA_OT_Model_BisectEdit, RARA_OT_Model_BisectObject,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)