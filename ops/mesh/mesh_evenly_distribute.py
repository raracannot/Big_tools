# 均匀分布

import bpy
import bmesh
import mathutils
from ...utils.math_utils import get_continuous_edges, check_y_shape_edges


# ==========================================
# Evenly Distribute helpers
# ==========================================

def evenly_linear(path, strength):
    if len(path) < 3:
        return
    for i in range(1, len(path) - 1):
        t = i / (len(path) - 1)
        orig = path[i].co.copy()
        target = path[0].co.lerp(path[-1].co, t)
        path[i].co = orig.lerp(target, strength)


def evenly_average(path, strength, iterations, seg_len):
    for _ in range(iterations):
        for i in range(1, len(path) - 1):
            pl = (path[i].co - path[i - 1].co).length
            nl = (path[i + 1].co - path[i].co).length
            if pl > seg_len:
                path[i].co = path[i].co.lerp(path[i - 1].co, (pl - seg_len) / pl)
            if nl > seg_len:
                path[i].co = path[i].co.lerp(path[i + 1].co, (nl - seg_len) / nl)
    if strength < 1.0:
        pass


def evenly_adjacent(path, strength, iterations, total_len):
    for _ in range(iterations):
        new_pos = [v.co.copy() for v in path]
        for i in range(1, len(path) - 1):
            pl = (path[i].co - path[i - 1].co).length
            nl = (path[i + 1].co - path[i].co).length
            avg = (pl + nl) * 0.5
            if pl > 0:
                pv = (path[i].co - path[i - 1].co).normalized()
                new_pos[i] = path[i - 1].co + pv * avg
            if nl > 0:
                nv = (path[i + 1].co - path[i].co).normalized()
                new_pos[i] = path[i + 1].co - nv * avg
        for i in range(len(path)):
            path[i].co = new_pos[i]

        ntl = sum((path[i].co - path[i - 1].co).length for i in range(1, len(path)))
        if ntl > 0:
            sf = total_len / ntl
            sp = path[0].co
            for i in range(1, len(path) - 1):
                path[i].co = sp + (path[i].co - sp) * sf


# ==========================================
# Operator
# ==========================================

class RARA_OT_Model_EvenlyDistribute(bpy.types.Operator):
    bl_idname = "rara.model_evenly_distribute"
    bl_label = "均匀分布"
    bl_description = "对选中的连续边线进行长度均分优化\n支持三种算法：线性分布、平均缩放、邻边缩放"
    bl_options = {'REGISTER', 'UNDO'}

    strength: bpy.props.FloatProperty(name="强度", default=1.0, min=0.0, max=1.0)

    smooth_type: bpy.props.EnumProperty(
        name="平滑类型",
        items=[
            ('LINEAR', "线性分布", "使用线性插值进行均匀分布"),
            ('AVERAGE', "平均缩放", "每次迭代趋向于平均边长"),
            ('ADJACENT', "邻边缩放", "每次迭代趋于与临边等长"),
        ],
        default='LINEAR')

    iterations: bpy.props.IntProperty(name="迭代次数", default=20, min=1,
        description="除线性分布外的算法迭代次数")

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        if check_y_shape_edges(obj):
            self.report({'ERROR'}, "当前边线存在多个岔路，请检查后重试")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        chains = get_continuous_edges(obj)

        for chain in chains:
            if not chain:
                continue
            path = [chain[0].verts[0]]
            cv = path[0]
            for edge in chain:
                nv = edge.other_vert(cv)
                if nv and nv.is_valid:
                    path.append(nv)
                    cv = nv
            if len(path) < 2:
                continue

            total_len = sum((path[i].co - path[i - 1].co).length for i in range(1, len(path)))
            seg_len = total_len / (len(path) - 1) if len(path) > 1 else 0

            orig = [v.co.copy() for v in path]

            if self.smooth_type == 'LINEAR':
                evenly_linear(path, self.strength)
            elif self.smooth_type == 'AVERAGE':
                evenly_average(path, self.strength, self.iterations, seg_len)
            elif self.smooth_type == 'ADJACENT':
                evenly_adjacent(path, self.strength, self.iterations, total_len)

            if self.strength < 1.0 and self.smooth_type != 'LINEAR':
                for i, v in enumerate(path):
                    v.co = orig[i].lerp(v.co, self.strength)

        bm.normal_update()
        bmesh.update_edit_mesh(obj.data)
        bm.free()
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


classes = (RARA_OT_Model_EvenlyDistribute,)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)