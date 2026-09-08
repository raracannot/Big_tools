# 边线重采样

import bpy
import bmesh
from ...utils.math_utils import (
    get_continuous_edges, order_chain_edges_and_verts,
    check_y_shape_edges, resample_polyline_np, apply_resampled_chains
)


class RARA_OT_Model_ResampleSegmentsPreserve(bpy.types.Operator):
    bl_idname = "rara.model_resample_edges_segments_preserve"
    bl_label = "边线重采样 [段数]"
    bl_description = "按目标段数等距重采样边线(旧版，仅重链不自动补面，会留下洞)\n稳定补面请用「链带重采样」新版\n支持平滑曲线模式（Catmull-Rom样条插值）"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    segments: bpy.props.IntProperty(name="分段数", default=3, min=1)
    offset: bpy.props.IntProperty(name="起点偏移", default=0)
    use_curve: bpy.props.BoolProperty(name="平滑曲线", default=False)
    auto_merge: bpy.props.BoolProperty(name="自动合并", default=True)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        if check_y_shape_edges(obj):
            self.report({'ERROR'}, "当前边线存在多个岔路，请检查后重试")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        chains = get_continuous_edges(obj, self.offset)
        chains_verts = [order_chain_edges_and_verts(chain) for chain in chains]

        new_points_list = []
        for verts in chains_verts:
            if not verts or len(verts) < 2:
                continue
            path_co = [v.co for v in verts]
            new_pts = resample_polyline_np(path_co, segments=self.segments, use_curve=self.use_curve)
            new_points_list.append(new_pts)

        # 重链（删旧边 → 建新边 → 坐标焊接），不做自动补面
        apply_resampled_chains(bm, chains, chains_verts, new_points_list, auto_merge=self.auto_merge)
        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class RARA_OT_Model_ResampleLengthPreserve(bpy.types.Operator):
    bl_idname = "rara.model_resample_edges_length_preserve"
    bl_label = "边线重采样 [长度]"
    bl_description = "按目标段长度等距重采样边线(旧版，仅重链不自动补面，会留下洞)\n稳定补面请用「链带重采样」新版\n支持平滑曲线模式（Catmull-Rom样条插值）"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    length: bpy.props.FloatProperty(name="段长度", default=1.0, min=0.01)
    offset: bpy.props.IntProperty(name="环形起点偏移", default=0, min=0)
    use_curve: bpy.props.BoolProperty(name="平滑曲线", default=False)
    auto_merge: bpy.props.BoolProperty(name="自动合并", default=True)
    inversion: bpy.props.BoolProperty(name="翻转边链", default=True)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        if check_y_shape_edges(obj):
            self.report({'ERROR'}, "当前边线存在多个岔路，请检查后重试")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        chains = get_continuous_edges(obj, self.offset)
        chains_verts = [order_chain_edges_and_verts(chain) for chain in chains]

        if self.inversion:
            chains_verts = [verts[::-1] for verts in chains_verts]

        # 重链（删旧边 → 建新边 → 坐标焊接），不做自动补面
        new_points_list = []
        for verts in chains_verts:
            if not verts or len(verts) < 2:
                continue
            path_co = [v.co for v in verts]
            new_pts = resample_polyline_np(path_co, target_length=self.length, use_curve=self.use_curve)
            new_points_list.append(new_pts)

        apply_resampled_chains(bm, chains, chains_verts, new_points_list, auto_merge=self.auto_merge)
        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


classes = (RARA_OT_Model_ResampleSegmentsPreserve, RARA_OT_Model_ResampleLengthPreserve,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)