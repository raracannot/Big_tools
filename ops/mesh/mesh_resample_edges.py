# 边线重采样

import bpy
import bmesh
import numpy as np
from ...utils.math_utils import (
    get_continuous_edges, order_chain_edges_and_verts,
    check_y_shape_edges, resample_polyline_np, apply_resampled_chains
)


class RARA_OT_Model_ResampleSegmentsPreserve(bpy.types.Operator):
    bl_idname = "rara.model_resample_edges_segments_preserve"
    bl_label = "边线重采样 [保面·段数]"
    bl_description = "按目标段数等距重采样边线，使用原生edge_face_add填充面\n支持平滑曲线模式（Catmull-Rom样条插值）"
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

        # 使用旧版逻辑：删旧边 → 建新边 → merge
        apply_resampled_chains(bm, chains, chains_verts, new_points_list, auto_merge=self.auto_merge)
        bmesh.update_edit_mesh(obj.data)

        # 收集受影响的边界边 + 新链边 → 仅填充受影响区域
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

        # 找到所有与重采样链顶点相连的开放边
        new_co = np.array(new_points_list[0] if new_points_list else [])
        affected_edges = set()
        if len(new_co) > 0:
            for e in bm.edges:
                if len(e.link_faces) >= 2:
                    continue
                for v in e.verts:
                    dist = np.min(np.linalg.norm(new_co - np.array(v.co), axis=1))
                    if dist < 0.1:
                        affected_edges.add(e)
                        break

        # 也纳入相邻的开放边（传播一次）
        expanded = set(affected_edges)
        for e in affected_edges:
            for v in e.verts:
                for neighbor_e in v.link_edges:
                    if neighbor_e.is_valid and len(neighbor_e.link_faces) < 2:
                        expanded.add(neighbor_e)

        for e in expanded:
            e.select = True
        if affected_edges:
            bmesh.update_edit_mesh(obj.data)
            if self.auto_merge:
                bpy.ops.mesh.edge_face_add()
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


class RARA_OT_Model_ResampleLengthPreserve(bpy.types.Operator):
    bl_idname = "rara.model_resample_edges_length_preserve"
    bl_label = "边线重采样 [保面·长度]"
    bl_description = "按目标段长度等距重采样边线，使用原生edge_face_add填充面\n支持平滑曲线模式（Catmull-Rom样条插值）"
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

        # ── Phase 1: 记录存活边界边 ──
        chain_edge_set = set()
        for chain_edges in chains:
            for e in chain_edges:
                chain_edge_set.add(e)

        surviving_boundary = set()
        for e in chain_edge_set:
            for f in e.link_faces:
                for f_edge in f.edges:
                    if f_edge not in chain_edge_set:
                        surviving_boundary.add(f_edge)

        # ── Phase 2: 删除 + 重采样 ──
        new_points_list = []
        for verts in chains_verts:
            if not verts or len(verts) < 2:
                continue
            path_co = [v.co for v in verts]
            new_pts = resample_polyline_np(path_co, target_length=self.length, use_curve=self.use_curve)
            new_points_list.append(new_pts)

        apply_resampled_chains(bm, chains, chains_verts, new_points_list, auto_merge=self.auto_merge)
        bmesh.update_edit_mesh(obj.data)

        # ── Phase 3: 识别新链边 ──
        bm = bmesh.from_edit_mesh(obj.data)
        new_co = np.array(new_points_list[0]) if new_points_list else np.empty((0, 3))
        new_chain_edges = set()
        if len(new_co) > 0:
            for e in bm.edges:
                if len(e.link_faces) >= 2:
                    continue
                for v in e.verts:
                    if np.min(np.linalg.norm(new_co - np.array(v.co), axis=1)) < 0.01:
                        new_chain_edges.add(e)
                        break

        # ── Phase 4: 选中 + 填充 ──
        for e in surviving_boundary:
            if e.is_valid:
                e.select = True
        for e in new_chain_edges:
            e.select = True
        bmesh.update_edit_mesh(obj.data)
        if self.auto_merge:
            bpy.ops.mesh.edge_face_add()
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