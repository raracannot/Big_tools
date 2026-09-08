# 链带重采样算子 v2（AV 腔体溶解思路，旧按钮保留兜底）
# 说明：整片选中(允许多条平行链/共享面/三角或n-gon邻接)统一处理：
# 溶解横向边成腔 -> 打洞 -> 重建链 -> 腔体间行军补面。

import bpy
import bmesh
from mathutils import Vector
from ...utils.math_utils import resample_polyline_np
from ...utils.mesh_resample_strip import (
    ResampleError,
    collect_open_chains,
    resample_region,
)


class RARA_OT_Model_ResampleChainSegments(bpy.types.Operator):
    bl_idname = "rara.model_resample_chain_segments"
    bl_label = "链带重采样 [段数]"
    bl_description = "按目标段数重采样选中的开放边链（可多链/平行链/共享面），端点锚定、腔体化重建\n闭合环/Y形岔路请用旧版按钮"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    segments: bpy.props.IntProperty(name="分段数", default=3, min=1)
    use_curve: bpy.props.BoolProperty(name="平滑曲线", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        try:
            selected = [e for e in bm.edges if e.select and not e.hide]
            chains = collect_open_chains(bm, selected)
            if not chains:
                self.report({'WARNING'}, "没有选中任何边线")
                return {'CANCELLED'}

            jobs = []
            for chain in chains:
                coords = [v.co.copy() for v in chain["verts"]]
                pts = resample_polyline_np(coords, segments=self.segments, use_curve=self.use_curve)
                if len(pts) < 2:
                    raise ResampleError("分段数过小")
                jobs.append({"chain": chain, "positions": [Vector(p) for p in pts]})

            ne, nf = resample_region(bm, selected, jobs)
            bmesh.update_edit_mesh(obj.data)
            self.report({'INFO'}, f"链带重采样完成：{len(jobs)} 条链，新建 {ne} 条链边 / {nf} 个面")
            return {'FINISHED'}
        except ResampleError as e:
            self.report({'WARNING'}, f"重采样不支持：{e}（可用旧版按钮兜底）")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"重采样失败: {e}")
            return {'CANCELLED'}


class RARA_OT_Model_ResampleChainLength(bpy.types.Operator):
    bl_idname = "rara.model_resample_chain_length"
    bl_label = "链带重采样 [长度]"
    bl_description = "按目标段长度重采样选中的开放边链（可多链/平行链/共享面），端点锚定、腔体化重建\n闭合环/Y形岔路请用旧版按钮"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    length: bpy.props.FloatProperty(name="段长度", default=1.0, min=0.001)
    use_curve: bpy.props.BoolProperty(name="平滑曲线", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        try:
            selected = [e for e in bm.edges if e.select and not e.hide]
            chains = collect_open_chains(bm, selected)
            if not chains:
                self.report({'WARNING'}, "没有选中任何边线")
                return {'CANCELLED'}

            jobs = []
            for chain in chains:
                coords = [v.co.copy() for v in chain["verts"]]
                total_len = 0.0
                for i in range(len(coords) - 1):
                    total_len += (coords[i + 1] - coords[i]).length
                if self.length > total_len:
                    raise ResampleError(f"目标段长度 {self.length:.3f} 大于链总长 {total_len:.3f}")
                pts = resample_polyline_np(coords, target_length=self.length, use_curve=self.use_curve)
                if len(pts) < 2:
                    raise ResampleError("段长度过大")
                jobs.append({"chain": chain, "positions": [Vector(p) for p in pts]})

            ne, nf = resample_region(bm, selected, jobs)
            bmesh.update_edit_mesh(obj.data)
            self.report({'INFO'}, f"链带重采样完成：{len(jobs)} 条链，新建 {ne} 条链边 / {nf} 个面")
            return {'FINISHED'}
        except ResampleError as e:
            self.report({'WARNING'}, f"重采样不支持：{e}（可用旧版按钮兜底）")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"重采样失败: {e}")
            return {'CANCELLED'}


classes = (RARA_OT_Model_ResampleChainSegments, RARA_OT_Model_ResampleChainLength,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
