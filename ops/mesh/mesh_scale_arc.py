# 缩放圆弧：以倒角两侧边 a/e 的虚拟交点(或退化回退点)为轴心，等比缩放选中的倒角弧边。
# 纯 bpy/bmesh/mathutils，可独立 headless 测试。
#
# 几何：若 a/e 所在直线有交点 O，把链上每个顶点 v -> O + s*(v - O)。
#   - b、d 始终留在 a/e 直线上，真圆弧缩放后仍是圆弧(相似变换保圆)；
#   - s=1 不变，s→0 收拢到交点(等效去倒角)，s>1 外扩。
# 退化支持：a/e 不相交(斜交/平行)时，取两直线最近点连线的中点为轴心继续缩放，
# 并把结果标记为“退化”。

import bpy
import bmesh
import mathutils
from mathutils import Vector
from mathutils.geometry import intersect_line_line


# ==========================================
# 链提取（开放简单路径）
# ==========================================

def collect_open_chains(bm, sel_edges):
    sel = set(e for e in sel_edges if e.is_valid and not e.hide)
    chains = []
    skipped_verts = 0
    remaining = set(sel)
    while remaining:
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


# ==========================================
# a/e 直线与轴心
# ==========================================

def _lines_closest(a1, a2, b1, b2):
    """两直线最近点对。返回 (pa, pe, dist)；平行时返回基于投影的回退点对。"""
    r = intersect_line_line(a1, a2, b1, b2)
    if r is not None:
        pa, pe = r
        return pa, pe, (pa - pe).length
    # 平行：取互相在对方线上的投影点作为最近点对（唯一性不强，但稳定）
    da = a2 - a1
    db = b2 - b1
    la = da.length_squared
    lb = db.length_squared
    pa = a1 + da * (((b1 - a1).dot(da)) / la) if la > 1e-12 else a1.copy()
    pe = b1 + db * (((a1 - b1).dot(db)) / lb) if lb > 1e-12 else b1.copy()
    return pa, pe, (pa - pe).length


def _chain_end_candidates(bm, chain, sel):
    """两端点上“未选中、且另一端点不在链上”的邻边作为 a/e 候选。"""
    chain_set = set(chain)
    c0 = [e for e in chain[0].link_edges
          if e not in sel and e.other_vert(chain[0]) not in chain_set]
    cn = [e for e in chain[-1].link_edges
          if e not in sel and e.other_vert(chain[-1]) not in chain_set]
    return c0, cn


# 基准坐标快照缓存：key=(tag, 选中顶点索引)。让滑块/重跑都从同一原始状态计算，幂等、无顺序依赖。
_BASE_CACHE = {}


def _base_key(tag, chains):
    ids = tuple(sorted({v.index for ch in chains for v in ch}))
    return (tag, ids)


def _capture_coords(bm, chains, sel_set):
    ids = set()
    for ch in chains:
        ids.update(ch)
        c0, cn = _chain_end_candidates(bm, ch, sel_set)
        for e in c0 + cn:
            ids.update(e.verts)
    return {v.index: v.co.copy() for v in ids if v.is_valid}


def _pivot_from_coords(bm, chain, sel_set, coords, tol):
    """用给定坐标表(基准)求链轴心。返回 (O|None, degenerate)。"""
    if len(chain) < 2:
        return None, False
    c0, cn = _chain_end_candidates(bm, chain, sel_set)
    if not c0 or not cn:
        return None, False
    v0, vn = chain[0], chain[-1]
    best = None
    for ea in c0:
        fa = ea.other_vert(v0)
        for eb in cn:
            fb = eb.other_vert(vn)
            pa, pe, dist = _lines_closest(
                coords[fa.index], coords[v0.index],
                coords[fb.index], coords[vn.index])
            if best is None or dist < best[0]:
                best = (dist, pa, pe)
    if best is None:
        return None, False
    _, pa, pe = best
    if (pa - pe).length <= tol:
        return (pa + pe) / 2.0, False
    return (pa + pe) / 2.0, True


def _geometry_matches(bm, base, pivots, s_last, chains, tol=1e-4):
    """检查当前几何是否等于“基准 + s_last”应用后的预期（用于识别手动改动/重跑）。"""
    if s_last is None:
        return True
    for ch, pv in zip(chains, pivots):
        O = pv[0]
        if O is None:
            continue
        for v in ch:
            if v.index not in base:
                return False
            exp = O + (base[v.index] - O) * s_last
            if (v.co - exp).length > tol:
                return False
    return True


def scale_chains(bm, sel_edges, scale, tol=1e-5, tag=None):
    """遍历所有选中开放链，从统一的基准快照求交点后统一缩放。

    返回 (链数, 移动顶点数, 跳过顶点数, 退化轴心链数)。
    修正：全部交点基于同一份初始快照计算(顺序无关)，并缓存基准使滑块/重跑幂等。
    """
    chains, skipped = collect_open_chains(bm, sel_edges)
    if not chains:
        return 0, 0, skipped, 0
    sel_set = set(e for e in sel_edges if e.is_valid and not e.hide)
    bm.verts.ensure_lookup_table()

    key = _base_key(tag, chains)
    cache = _BASE_CACHE.get(key)
    if cache is not None:
        pivots = [_pivot_from_coords(bm, ch, sel_set, cache["base"], tol) for ch in chains]
        if not _geometry_matches(bm, cache["base"], pivots, cache["s_last"], chains):
            # 几何已被改动/撤销重跑 -> 重建基准
            cache = None
    if cache is None:
        base = _capture_coords(bm, chains, sel_set)
        cache = {"base": base, "s_last": None}
        _BASE_CACHE[key] = cache

    pivots = [_pivot_from_coords(bm, ch, sel_set, cache["base"], tol) for ch in chains]

    handled = 0
    moved = 0
    degenerated = 0
    for ch, pv in zip(chains, pivots):
        O, degenerate = pv
        if O is None:
            continue
        if degenerate:
            degenerated += 1
        for v in ch:
            v.co = O + (cache["base"][v.index] - O) * scale
            moved += 1
        handled += 1

    cache["s_last"] = scale
    _BASE_CACHE[key] = cache
    return handled, moved, skipped, degenerated


# ==========================================
# 算子
# ==========================================

class RARA_OT_Model_ScaleArc(bpy.types.Operator):
    bl_idname = "rara.model_scale_arc"
    bl_label = "缩放圆弧"
    bl_description = "以倒角弧边两端外侧边 a/e 的虚拟交点为轴心，等比缩放选中倒角弧边\n缩放=1 不变；拉到 0 收拢到交点(等效去倒角)；a/e 无交点(退化)时用两线最近点中点作轴心"
    bl_options = {'REGISTER', 'UNDO'}

    scale: bpy.props.FloatProperty(
        name="缩放比例",
        description="1=原样, <1 收拢向交点(去倒角), >1 外扩",
        default=1.0,
        min=0.0,
        max=5.0,
        precision=3,
    )
    remove: bpy.props.BoolProperty(
        name="去倒角",
        description="勾选后覆盖缩放比例：把每条倒角弧融并为轴心处单个顶点(等效缩放0后融并)",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.prop(self, "remove")
        sub = col.column()
        sub.active = not self.remove
        sub.prop(self, "scale")

    def invoke(self, context, event):
        # 每次重新点击按钮都回到默认：缩放=1(原样)、去倒角=不勾选
        self.scale = 1.0
        self.remove = False
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        sel = [e for e in bm.edges if e.select and not e.hide]
        if not sel:
            self.report({'WARNING'}, "请先选中倒角弧边")
            return {'CANCELLED'}

        # 勾选【去倒角】= 用缩放0塌缩到轴心，再按距离融并重合顶点
        scale = 0.0 if self.remove else self.scale
        handled, moved, skipped, degenerated = scale_chains(bm, sel, scale, tag=obj.name)

        if self.remove and moved:
            chains, _ = collect_open_chains(bm, sel)
            verts = [v for ch in chains for v in ch if v.is_valid]
            key = _base_key(obj.name, chains)
            _BASE_CACHE.pop(key, None)  # 先清理缓存，remove_doubles 后部分顶点会失效
            if len(verts) >= 2:
                bmesh.ops.remove_doubles(bm, verts=verts, dist=1e-4)

        bmesh.update_edit_mesh(obj.data)

        if moved == 0:
            self.report({'WARNING'}, "没有可用于缩放的倒角弧边")
            return {'CANCELLED'}
        if self.remove:
            msg = f"去倒角完成：{handled} 条弧已缩放至0并融并"
        else:
            msg = f"缩放圆弧完成：{handled} 条弧，移动 {moved} 个顶点"
        if degenerated:
            msg += f"，{degenerated} 条轴心退化(无交点，取最近点中点)"
        if skipped:
            msg += f"，跳过(闭合环/分支) {skipped} 个顶点"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


classes = (RARA_OT_Model_ScaleArc,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
