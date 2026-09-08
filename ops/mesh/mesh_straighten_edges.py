# 拉直所选边线
# 遍历所有选中的连续边线组：以每组两个端点为直线两端，
# 把组内非端点顶点按所选模式移动到端点上连成的线段上。
#   就近拉直 = 顶点垂直接影到线段并夹紧在 [A,B] 内
#   等距拉直 = 组内顶点按顺序等距分布到 [A,B] 上
# 本文件仅依赖 bpy/bmesh/mathutils，可独立 headless 测试。

import bpy
import bmesh
import mathutils


# ==========================================
# 纯算法部分（可 headless 复用）
# ==========================================

def _collect_open_chains(bm, sel_edges):
    """把选中的连续边拆成“简单开放路径”的有序顶点列表。

    返回 (chains, skipped_verts)。skipped_verts 为因闭合环/分支而跳过的顶点数。
    """
    sel = set(e for e in sel_edges if e.is_valid and not e.hide)
    chains = []
    skipped_verts = 0
    remaining = set(sel)
    while remaining:
        # 取一个连通分量
        seed = next(iter(remaining))
        comp = {seed}
        stack = [seed]
        while stack:
            e = stack.pop()
            for v in e.verts:
                for e2 in v.link_edges:
                    if e2 in remaining and e2 not in comp:
                        comp.add(e2)
                        stack.append(e2)
        remaining -= comp

        comp_verts = set()
        for e in comp:
            comp_verts.update(e.verts)
        deg = {v: sum(1 for e in v.link_edges if e in comp) for v in comp_verts}
        if any(d > 2 for d in deg.values()):
            skipped_verts += len(comp_verts)
            continue
        ends = [v for v in comp_verts if deg[v] == 1]
        if len(ends) != 2:
            # 闭合环 / 无端点：无“两个端点”，跳过
            skipped_verts += len(comp_verts)
            continue

        a, b = ends
        chain = [a]
        cur = a
        prev = None
        while cur is not b:
            nxt = [e for e in cur.link_edges if e in comp and e is not prev]
            if len(nxt) != 1:
                break
            e = nxt[0]
            cur = e.other_vert(cur)
            chain.append(cur)
            prev = e
        if chain[-1] is b:
            chains.append(chain)
        else:
            skipped_verts += len(comp_verts)
    return chains, skipped_verts


def _project_to_segment(p, a, b):
    ab = b - a
    denom = ab.length_squared
    if denom < 1e-12:
        return None
    t = (p - a).dot(ab) / denom
    t = min(max(t, 0.0), 1.0)
    return a + ab * t


def straighten_chains(bm, sel_edges, mode='NEAREST'):
    """拉直。mode: 'NEAREST' 就近 / 'EVEN' 等距。

    返回 (链数, 移动顶点数, 跳过顶点数)。
    """
    chains, skipped = _collect_open_chains(bm, sel_edges)
    moved = 0
    handled = 0
    for chain in chains:
        if len(chain) < 3:
            handled += 1
            continue
        a = chain[0].co
        b = chain[-1].co
        ab = b - a
        if ab.length_squared < 1e-12:
            handled += 1
            continue
        n = len(chain)
        interior = chain[1:-1]
        if mode == 'EVEN':
            targets = [a + ab * (i / (n - 1)) for i in range(1, n - 1)]
        else:  # NEAREST
            targets = [_project_to_segment(v.co, a, b) for v in interior]
        for v, pos in zip(interior, targets):
            if pos is None:
                continue
            v.co = pos
            moved += 1
        handled += 1
    return handled, moved, skipped


# ==========================================
# 算子
# ==========================================

class RARA_OT_Model_StraightenEdges(bpy.types.Operator):
    bl_idname = "rara.model_straighten_edges"
    bl_label = "拉直所选边线"
    bl_description = "把每条连续选中边线拉直：以组内两端点为线段端点，按模式移动中间顶点\n就近拉直=投影到线段；等距拉直=沿线段等距分布\n闭合环/带分支的边组会被跳过"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        name="拉直模式",
        items=(
            ('NEAREST', "就近拉直", "各中间顶点垂直接影到端点线段上"),
            ('EVEN', "等距拉直", "各中间顶点沿端点线段等距分布"),
        ),
        default='NEAREST',
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        sel = [e for e in bm.edges if e.select and not e.hide]
        if not sel:
            self.report({'WARNING'}, "没有选中任何边线")
            return {'CANCELLED'}
        handled, moved, skipped = straighten_chains(bm, sel, self.mode)
        bmesh.update_edit_mesh(obj.data)
        mode_txt = "就近" if self.mode == 'NEAREST' else "等距"
        msg = f"{mode_txt}拉直：处理 {handled} 组边线，移动 {moved} 个顶点"
        if skipped:
            msg += f"，跳过(闭合环/分支) {skipped} 个顶点"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


classes = (RARA_OT_Model_StraightenEdges,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
