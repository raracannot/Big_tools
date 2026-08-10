# 三点镜像

import numpy as np
import bpy
import bmesh
import mathutils
from mathutils import Vector
from ...utils.gpu_utils import draw_lines_3d
from ...constants import BBOX_EDGES
from ...utils.base_mixin import BaseModalMixin
from ...utils.draw_callbacks import draw_mirror_3d, draw_mirror_2d


# ==========================================
# MirrorPreviewDrawer
# ==========================================
class MirrorPreviewDrawer:
    def __init__(self):
        self.bbox_edges_world = []

    def build_preview(self, context):
        self.bbox_edges_world = build_bbox_edges(context)

    def draw_original_3d(self, context, color=(1.0, 0.6, 0.1, 0.2)):
        if not self.bbox_edges_world:
            return
        lines = []
        for v1, v2 in self.bbox_edges_world:
            lines.extend([v1, v2])
        draw_lines_3d(lines, color)

    def draw_mirrored_3d(self, context, plane_center, normal, color=(1.0, 0.6, 0.1, 0.8)):
        if not self.bbox_edges_world or not plane_center or not normal:
            return
        mirrored_lines = []
        for v1, v2 in self.bbox_edges_world:
            d1 = (v1 - plane_center).dot(normal)
            m1 = v1 - 2 * d1 * normal
            d2 = (v2 - plane_center).dot(normal)
            m2 = v2 - 2 * d2 * normal
            mirrored_lines.extend([m1, m2])
        draw_lines_3d(mirrored_lines, color)


def build_bbox_edges(context):
    bbox_edges_world = []

    if context.mode == "OBJECT":
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            bbox_local = np.array(obj.bound_box, dtype=np.float32)
            world_mat = np.array(obj.matrix_world, dtype=np.float32)
            bbox_h = np.hstack([bbox_local, np.ones((8, 1), dtype=np.float32)])
            bbox_world = (world_mat @ bbox_h.T).T[:, :3]
            for a, b in BBOX_EDGES:
                bbox_edges_world.append((Vector(bbox_world[a]), Vector(bbox_world[b])))
    elif context.mode == "EDIT_MESH":
        selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected_meshes:
            return

        for obj in selected_meshes:
            if obj.mode != 'EDIT':
                continue

            mesh = obj.data
            bm = bmesh.from_edit_mesh(mesh)
            selected_indices = [v.index for v in bm.verts if v.select]
            if not selected_indices:
                continue

            total_verts = len(mesh.vertices)
            all_co = np.empty(total_verts * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", all_co)
            all_co = all_co.reshape(total_verts, 3)

            verts_local = all_co[selected_indices]
            min_co = verts_local.min(axis=0)
            max_co = verts_local.max(axis=0)

            bbox_verts_local = np.array([
                [min_co[0], min_co[1], min_co[2]],
                [max_co[0], min_co[1], min_co[2]],
                [max_co[0], max_co[1], min_co[2]],
                [min_co[0], max_co[1], min_co[2]],
                [min_co[0], min_co[1], max_co[2]],
                [max_co[0], min_co[1], max_co[2]],
                [max_co[0], max_co[1], max_co[2]],
                [min_co[0], max_co[1], max_co[2]],
            ], dtype=np.float32)

            world_mat = np.array(obj.matrix_world, dtype=np.float32)
            bbox_h = np.hstack([bbox_verts_local, np.ones((8, 1), dtype=np.float32)])
            bbox_world = (world_mat @ bbox_h.T).T[:, :3]
            for a, b in BBOX_EDGES:
                bbox_edges_world.append((Vector(bbox_world[a]), Vector(bbox_world[b])))
    return bbox_edges_world


# ==========================================
# Mirror operators
# ==========================================
class RARA_OT_Model_MirrorEdit(BaseModalMixin, bpy.types.Operator):
    bl_idname = "rara.model_three_points_mirror_edit"
    bl_label = "三点镜像 (编辑模式)"
    bl_description = "在编辑模式下通过三个控制点构建镜像平面，将选中顶点沿该平面镜像移动或复制\n【左键】放置/拖动控制点\n【X】删除当前高亮控制点\n【Ctrl+右键】执行移动镜像\n【Shift+右键】执行复制镜像\n【ESC/右键】取消退出"
    bl_options = {'REGISTER', 'UNDO'}
    expected_mode = 'EDIT_MESH'

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH' and context.mode in {'EDIT_MESH', 'OBJECT'}

    def invoke(self, context, event):
        if context.mode != self.expected_mode:
            self.report({'WARNING'}, "请在编辑模式下使用此工具")
            return {'CANCELLED'}
        self._mirror_mode = 'DUPLICATE'
        self._init_snapper_widget(context, MirrorPreviewDrawer)
        args = (self, context)
        self._add_draw_handler_3d(draw_mirror_3d, args)
        self._add_draw_handler_2d(draw_mirror_2d, args)
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        context.area.tag_redraw()

        return {'RUNNING_MODAL'}

    def update_header(self, context):
        mode_name = "复制" if self._mirror_mode == 'DUPLICATE' else "移动"
        msg = f"【编辑模式镜像】已选点: {len(self.widget.points)}/3 | C: 模式切换 [{mode_name}] | 左键: 放置 | X: 删除 | Ctrl+右键: 移动 | Shift+右键: 复制 | 右键/回车: 执行 | ESC: 取消"
        self._set_status(context, msg)

    def modal(self, context, event):
        try:
            if context.mode != self.expected_mode:
                self.finish(context)
                return {'CANCELLED'}
            context.area.tag_redraw()

            if event.type == 'TIMER':
                return {'PASS_THROUGH'}

            if event.type == 'C' and event.value == 'PRESS':
                self._mirror_mode = 'MOVE' if self._mirror_mode == 'DUPLICATE' else 'DUPLICATE'
                self.update_header(context)
                return {'RUNNING_MODAL'}

            if event.type in {'RIGHTMOUSE', 'RET'} and event.value == 'PRESS':
                if event.type == 'RIGHTMOUSE' and not (event.shift or event.ctrl or event.alt):
                    if len(self.widget.points) in {2, 3}:
                        self.execute_mirror(context, mode=self._mirror_mode)
                        self.finish(context)
                        return {'FINISHED'}
                    else:
                        self.finish(context)
                        self.report({'WARNING'}, "请至少绘制2个点")
                        return {'CANCELLED'}
                if len(self.widget.points) in {2, 3}:
                    if event.shift or event.alt:
                        self.execute_mirror(context, mode='DUPLICATE')
                    elif event.ctrl:
                        self.execute_mirror(context, mode='MOVE')
                    else:
                        self.execute_mirror(context, mode=self._mirror_mode)
                    self.finish(context)
                    return {'FINISHED'}
                else:
                    self.report({'WARNING'}, "请至少绘制2个点")
                    return {'RUNNING_MODAL'}

            if event.type == 'ESC' and event.value == 'PRESS':
                self.finish(context)
                self.report({'WARNING'}, "退出工具")
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

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"网格镜像出错: {str(e)}")
            return {'CANCELLED'}

    def execute_mirror(self, context, mode='MOVE'):
        plane_center, normal = self.widget.get_plane_params(context)
        if not plane_center or not normal:
            self.report({'ERROR'}, "点位共线或无效，无法构成镜像平面")
            return

        target_objects = []
        for obj in context.selected_objects:
            if obj.type == 'MESH' and obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(obj.data)
                if any(v.select for v in bm.verts):
                    target_objects.append(obj)
        if not target_objects:
            self.report({'WARNING'}, "没有找到处于编辑模式且有选中顶点的网格物体")
            return

        original_active = context.active_object

        try:
            for obj in target_objects:
                if mode == 'MOVE':
                    bm = bmesh.from_edit_mesh(obj.data)
                    world_mat = obj.matrix_world
                    inv_world_mat = world_mat.inverted()
                    for v in bm.verts:
                        if v.select:
                            world_co = world_mat @ v.co
                            d = (world_co - plane_center).dot(normal)
                            mirrored_world = world_co - 2 * d * normal
                            v.co = inv_world_mat @ mirrored_world
                    bmesh.update_edit_mesh(obj.data)

                elif mode == 'DUPLICATE':
                    obj.select_set(True)
                    context.view_layer.objects.active = obj
                    bpy.ops.mesh.duplicate_move()
                    bm = bmesh.from_edit_mesh(obj.data)
                    world_mat = obj.matrix_world
                    inv_world_mat = world_mat.inverted()
                    for v in bm.verts:
                        if v.select:
                            world_co = world_mat @ v.co
                            d = (world_co - plane_center).dot(normal)
                            mirrored_world = world_co - 2 * d * normal
                            v.co = inv_world_mat @ mirrored_world
                    bmesh.update_edit_mesh(obj.data)
                    if original_active:
                        context.view_layer.objects.active = original_active

        except Exception as e:
            self.report({'ERROR'}, f"处理物体 {obj.name} 时出错: {str(e)}")
            bpy.ops.object.select_all(action='DESELECT')
            if original_active:
                context.view_layer.objects.active = original_active
            return

        self.report({'INFO'}, f"网格镜像完成 ({mode})")

    def finish(self, context):
        self._finish_common(context)


class RARA_OT_Model_MirrorObject(BaseModalMixin, bpy.types.Operator):
    bl_idname = "rara.model_three_points_mirror_object"
    bl_label = "三点镜像 (物体模式)"
    bl_description = "在物体模式下通过三个控制点构建镜像平面，将选中物体沿该平面镜像移动/复制/关联复制\n【左键】放置控制点\n【X】删除当前高亮控制点\n【Ctrl+右键】执行移动镜像\n【Shift+右键】执行复制镜像\n【Alt+右键】执行关联复制镜像\n【回车】执行复制镜像\n【ESC】取消退出"
    bl_options = {'REGISTER', 'UNDO'}
    expected_mode = 'OBJECT'

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH' and context.mode in {'EDIT_MESH', 'OBJECT'}

    def invoke(self, context, event):
        if context.mode != self.expected_mode:
            self.report({'WARNING'}, "请在物体模式下使用此工具")
            return {'CANCELLED'}
        self._mirror_mode = 'DUPLICATE'
        self._init_snapper_widget(context, MirrorPreviewDrawer)
        args = (self, context)
        self._add_draw_handler_3d(draw_mirror_3d, args)
        self._add_draw_handler_2d(draw_mirror_2d, args)
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        mode_name = "复制" if self._mirror_mode == 'DUPLICATE' else "移动"
        msg = f"【物体模式镜像】已选点: {len(self.widget.points)}/3 | C: 模式切换 [{mode_name}] | 左键: 放置 | X: 删除 | 右键/回车: 执行 | ESC: 取消"
        self._set_status(context, msg)

    def modal(self, context, event):
        try:
            if context.mode != self.expected_mode:
                self.finish(context)
                return {'CANCELLED'}
            context.area.tag_redraw()

            if event.type == 'TIMER':
                return {'PASS_THROUGH'}

            if event.type == 'C' and event.value == 'PRESS':
                self._mirror_mode = 'MOVE' if self._mirror_mode == 'DUPLICATE' else 'DUPLICATE'
                self.update_header(context)
                return {'RUNNING_MODAL'}

            if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                if len(self.widget.points) in {2, 3}:
                    if event.shift:
                        self.execute_mirror(context, mode='DUPLICATE')
                        self.finish(context)
                        return {'FINISHED'}
                    elif event.ctrl:
                        self.execute_mirror(context, mode='MOVE')
                        self.finish(context)
                        return {'FINISHED'}
                    elif event.alt:
                        self.execute_mirror(context, mode='LINKED')
                        self.finish(context)
                        return {'FINISHED'}
                    else:
                        self.execute_mirror(context, mode=self._mirror_mode)
                        self.finish(context)
                        return {'FINISHED'}
                if not (event.shift or event.ctrl or event.alt):
                    self.finish(context)
                    return {'CANCELLED'}

            if event.type == 'ESC' and event.value == 'PRESS':
                self.finish(context)
                return {'CANCELLED'}

            if event.type == 'X' and event.value == 'PRESS':
                if self.widget.active_idx is not None:
                    self.widget.points.pop(self.widget.active_idx)
                    self.widget.active_idx = None
                    self.widget.drag_idx = None
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
                    self.execute_mirror(context, mode=self._mirror_mode)
                    self.finish(context)
                    return {'FINISHED'}
                else:
                    self.report({'WARNING'}, "请至少绘制2个点")
                    return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"物体镜像出错: {str(e)}")
            return {'CANCELLED'}

    def execute_mirror(self, context, mode='MOVE'):
        try:
            plane_center, normal = self.widget.get_plane_params(context)
            if not plane_center or not normal:
                self.report({'ERROR'}, "点位共线或无效，无法构成镜像平面")
                return

            if mode in ('DUPLICATE', 'LINKED'):
                bpy.ops.object.duplicate(linked=(mode == 'LINKED'))

            normal = normal.normalized()
            M = mathutils.Matrix.Identity(3)
            for i in range(3):
                for j in range(3):
                    M[i][j] -= 2 * normal[i] * normal[j]
            ref_mat = M.to_4x4()
            ref_mat.translation = 2 * plane_center.dot(normal) * normal

            for obj in context.selected_objects:
                obj.matrix_world = ref_mat @ obj.matrix_world

            self.report({'INFO'}, f"物体镜像完成 ({mode})")
        except Exception as e:
            self.report({'ERROR'}, f"执行镜像时出错: {str(e)}")

    def finish(self, context):
        self._finish_common(context)


classes = (RARA_OT_Model_MirrorEdit, RARA_OT_Model_MirrorObject,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)