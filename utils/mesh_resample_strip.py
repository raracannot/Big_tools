# 链带重采样核心 v2（AV 腔体溶解思路，纯拓扑，无 gpu 依赖，可独立 headless 测试）
#
# 整体流程（对应 AV_ResampleMesh.main）：
#   1. 输入为开放边链集合（允许多条链 / 相邻平行链共享面 / 三角或多边形邻接）。
#      Y 形岔路、闭合环、三角扇/pole 仍会拒绝并提示退回旧版。
#   2. "腔体化"：把链上每个内部顶点处未选中的横向边 dissolve 掉，使链两侧的面
#      合并成一个大 n-gon"腔体"；腔体边界环 = 链路径 + 外侧轨道 + 端帽。
#   3. 删除旧链内部顶点（连带删除腔体面 / 链边 / 横边），端点锚定保留。
#   4. 按给定位置重建每条链（新链端点 = 原锚点坐标）。
#   5. 逐个腔体在"链路径 ↔ 远侧轨道(或相邻链路径)"两条边界之间"行军(tri/quad)"
#      补面 —— 数量不一致自动插三角，不破坏外侧网格。
#   6. 新面局部法向一致性校正（沿共享边定向传播）。

import bmesh
import mathutils


class ResampleError(Exception):
    """可读的重采样失败原因，用于提示用户退回旧版。"""


# ==========================================
# 链提取
# ==========================================

def collect_open_chains(bm, selected_edges):
    """把选中边拆成开放的顶点有序链。

    返回 [{verts:[BMVert], edges:[BMEdge], closed:bool}, ...]
    对 Y 形岔路 / 闭合环抛出 ResampleError。
    """
    sel_set = set(e for e in selected_edges if e.is_valid and not e.hide)
    if not sel_set:
        raise ResampleError("没有选中任何边线")

    deg = {}
    for v in bm.verts:
        if v.hide:
            continue
        d = 0
        for e in v.link_edges:
            if e in sel_set:
                d += 1
        if d:
            deg[v] = d
        if d > 2:
            raise ResampleError("所选边存在 Y 形岔路，请逐条重采样")

    seen_edges = set()
    chains = []
    for e0 in sel_set:
        if e0 in seen_edges:
            continue
        comp = {e0}
        stack = [e0]
        while stack:
            e = stack.pop()
            for v in e.verts:
                for e2 in v.link_edges:
                    if e2 in sel_set and e2 not in comp:
                        comp.add(e2)
                        stack.append(e2)
        seen_edges.update(comp)

        comp_verts = set()
        for e in comp:
            comp_verts.update(e.verts)
        ends = [v for v in comp_verts if deg.get(v, 0) == 1]
        if not ends:
            raise ResampleError("暂不支持闭合环形边，请使用旧版重采样按钮")

        start_v = ends[0]
        chain_edges = []
        chain_verts = [start_v]
        v = start_v
        incoming = None
        while True:
            cand = [e for e in v.link_edges if e in comp and e is not incoming]
            if not cand:
                break
            cur_e = cand[0]
            chain_edges.append(cur_e)
            v = cur_e.other_vert(v)
            chain_verts.append(v)
            if deg[v] == 1:
                break
            incoming = cur_e
        if len(chain_verts) != len(chain_edges) + 1:
            raise ResampleError("边链提取异常，请逐条重采样")
        chains.append({"verts": chain_verts, "edges": chain_edges, "closed": False})

    return chains


# ==========================================
# 小工具
# ==========================================

def _edge_exists(v_a, v_b):
    for e in v_a.link_edges:
        if e.other_vert(v_a) is v_b:
            return True
    return False


def _selected_runs(flags):
    """在环形 boolean 数组中找出所有连续 True 段，返回 [(start,end)...]（含端点）。

    flags[i] 表示 ring 的第 i 条边是否选中。全 True 视作单个整环段。
    """
    n = len(flags)
    if n == 0:
        return []
    if all(flags):
        return [(0, n - 1)]
    s = next(i for i, fl in enumerate(flags) if not fl)
    fl = flags[s:] + flags[:s]
    runs = []
    i = 0
    while i < n:
        if fl[i]:
            j = i
            while j < n and fl[j]:
                j += 1
            runs.append(((s + i) % n, (s + j - 1) % n))
            i = j
        else:
            i += 1
    return runs


# ==========================================
# 主流程：腔体化 + 重建
# ==========================================

def resample_region(bm, selected_edges, jobs, select_new=True):
    """对整片选中边执行链带重采样。

    selected_edges : 编辑网格中选中的边（可跨多条链、共享面、三角/多边邻接）。
    jobs          : [{chain:{verts,edges}, positions:[Vector,...]}, ...]
                    positions 首尾必须与链端点坐标一致。
    返回 (重建链边总数, 新建面总数)。失败抛 ResampleError（可能留下半成品，由操作符 UNDO 兜底）。
    """
    sel = set(e for e in selected_edges if e.is_valid and not e.hide)
    if not sel:
        raise ResampleError("没有选中任何边线")
    if not jobs:
        raise ResampleError("没有可重建的链")

    edge_to_chain = {}
    for ci, job in enumerate(jobs):
        for e in job["chain"]["edges"]:
            if e in sel:
                edge_to_chain[e] = ci
    for e in sel:
        if e not in edge_to_chain:
            raise ResampleError("存在未归入链的选中边，请重新选择")

    # ---- 内部顶点（选中度 >=2） ----
    deg = {}
    for v in bm.verts:
        d = sum(1 for e in v.link_edges if e in sel)
        if d:
            deg[v] = d
    interior = [v for v, d in deg.items() if d >= 2]

    # ---- 1) 腔体化：溶解内部顶点上的未选中横边 ----
    diss = set()
    for v in interior:
        for e in v.link_edges:
            if e in sel:
                continue
            if len(e.link_faces) == 2:
                diss.add(e)
    if diss:
        bmesh.ops.dissolve_edges(bm, edges=list(diss))

    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # 溶解后，链内部顶点应只剩两条链边；否则(三角扇/pole 等)放弃
    for job in jobs:
        for v in job["chain"]["verts"][1:-1]:
            if v.is_valid:
                non_chain = [e for e in v.link_edges if e not in sel]
                if len(non_chain) != 0:
                    raise ResampleError("链内部顶点仍有外侧连接(三角扇/pole 等)，请使用旧版按钮")

    # ---- 2) 腔体面 + 边界环分解 ----
    chamber_faces = set()
    for e in sel:
        if e.is_valid:
            chamber_faces.update(e.link_faces)
    if not chamber_faces:
        raise ResampleError("选中边没有可重建的相邻面")

    recs = []
    for f in chamber_faces:
        ring = [lp.vert for lp in f.loops]
        fedge = [lp.edge for lp in f.loops]
        flags = [e in sel for e in fedge]
        runs = _selected_runs(flags)
        if not runs:
            continue
        if len(runs) > 2:
            raise ResampleError("区域结构复杂(多个岔路腔体)，请使用旧版按钮")
        rr = []
        for (sidx, eidx) in runs:
            cid = edge_to_chain.get(fedge[sidx])
            if cid is None:
                raise ResampleError("内部：腔体边归属异常")
            k = sidx
            while True:
                if edge_to_chain.get(fedge[k]) != cid:
                    raise ResampleError("区域交叉异常，请使用旧版按钮")
                if k == eidx:
                    break
                k = (k + 1) % len(fedge)
            chain_edges = jobs[cid]["chain"]["edges"]
            n_run_edges = (eidx - sidx) % len(fedge) + 1
            if n_run_edges != len(chain_edges):
                raise ResampleError("腔体只覆盖链的一部分(pole/中断)，请使用旧版按钮")
            rr.append({"start": sidx, "end": eidx, "chain": cid})
        recs.append({"ring": ring, "runs": rr})

    # ---- 3) 删除旧链（DEL_VERTS 连带删除所邻接的腔体面/链边/横边） ----
    all_interior = []
    single_edges = []
    for job in jobs:
        V = job["chain"]["verts"]
        iv = [v for v in V[1:-1] if v.is_valid]
        if iv:
            all_interior.extend(iv)
        else:
            single_edges.extend([e for e in job["chain"]["edges"] if e.is_valid])
    if all_interior:
        bmesh.ops.delete(bm, geom=all_interior, context='VERTS')
    if single_edges:
        bmesh.ops.delete(bm, geom=single_edges, context='EDGES')
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # ---- 4) 重建每条链 ----
    new_paths = {}
    total_edges = 0
    for ci, job in enumerate(jobs):
        V = job["chain"]["verts"]
        positions = job["positions"]
        if len(positions) < 2:
            raise ResampleError("目标分段太少")

        v0 = V[0]
        vm = V[-1]
        if not (v0.is_valid and vm.is_valid):
            raise ResampleError("链端点丢失，操作中断")
        v0.co = mathutils.Vector(positions[0])
        vm.co = mathutils.Vector(positions[-1])

        P = [v0]
        for p in positions[1:-1]:
            P.append(bm.verts.new(mathutils.Vector(p)))
        P.append(vm)
        ce = []
        for a, b in zip(P, P[1:]):
            ce.append(bm.edges.new([a, b]))
        total_edges += len(ce)
        new_paths[ci] = {"verts": P, "edges": ce}

    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # ---- 5) 逐个腔体补面 ----
    created_faces = []
    for r in recs:
        n = len(r["ring"])
        runs = r["runs"]
        if len(runs) == 1:
            sidx, eidx = runs[0]["start"], runs[0]["end"]
            # 链路径节点 = run 边起点到终点后一个节点(含两端)；远侧为其后剩余节点
            far_nodes = []
            i = (eidx + 2) % n
            while i != sidx:
                far_nodes.append(r["ring"][i])
                i = (i + 1) % n
            P_nodes = list(new_paths[runs[0]["chain"]]["verts"])
            R_nodes = far_nodes
        else:
            r0, r1 = runs[0], runs[1]
            P_nodes = list(new_paths[r0["chain"]]["verts"])
            R_nodes = list(new_paths[r1["chain"]]["verts"])

        P, R = _orient_pair(bm, P_nodes, R_nodes)
        if P is None:
            raise ResampleError("腔体端部无法配对，请使用旧版按钮")
        fill_open_side(bm, P, R, created_faces)

    orient_new_faces(bm, created_faces)

    # ---- 6) 清理残留 wire ----
    involved = set()
    for np_ in new_paths.values():
        involved.update(np_["verts"])
    for r in recs:
        if len(r["runs"]) == 1:
            involved.update(r["ring"])
    _clean_leftover_wires(bm, involved)

    if select_new:
        for np_ in new_paths.values():
            for v in np_["verts"]:
                if v.is_valid:
                    v.select = True
            for e in np_["edges"]:
                if e.is_valid:
                    e.select = True

    bm.normal_update()
    return total_edges, len(created_faces)


def _orient_pair(bm, P, R):
    """返回 (P,R) 的某个方向组合，使 P[0]-R[0]、P[-1]-R[-1] 有现成边(端帽)。

    无匹配时返回 (None, None)。
    """
    cands = (
        (list(P), list(R)),
        (list(P), list(R)[::-1]),
        (list(P)[::-1], list(R)),
        (list(P)[::-1], list(R)[::-1]),
    )
    for a, b in cands:
        if len(a) >= 2 and len(b) >= 2:
            if _edge_exists(a[0], b[0]) and _edge_exists(a[-1], b[-1]):
                return a, b
    return None, None


# ==========================================
# 两条开放路径之间的补面（行军 tri/quad）
# ==========================================

def fill_open_side(bm, P, Q, created_faces):
    nc, nq = len(P), len(Q)
    if nc < 2 or nq < 2:
        raise ResampleError("目标分段过少，无法补面")
    i = j = 0
    while True:
        if i == nc - 1 and j == nq - 1:
            break
        if i == nc - 1:
            created_faces.append(bm.faces.new([P[i], Q[j], Q[j + 1]]))
            j += 1
            continue
        if j == nq - 1:
            created_faces.append(bm.faces.new([P[i], Q[j], P[i + 1]]))
            i += 1
            continue
        fp = (i + 1) / (nc - 1)
        fq = (j + 1) / (nq - 1)
        eps = 1e-9
        if fp < fq - eps:
            created_faces.append(bm.faces.new([P[i], Q[j], P[i + 1]]))
            i += 1
        elif fq < fp - eps:
            created_faces.append(bm.faces.new([P[i], Q[j], Q[j + 1]]))
            j += 1
        else:
            created_faces.append(bm.faces.new([P[i], P[i + 1], Q[j + 1], Q[j]]))
            i += 1
            j += 1


# ==========================================
# 新面法向局部定向（沿共享边传播）
# ==========================================

def _loop_start(face, edge):
    for loop in face.loops:
        if loop.edge is edge:
            return 0 if loop.vert is edge.verts[0] else 1
    return None


def orient_new_faces(bm, faces):
    if not faces:
        return
    created = set(faces)
    rev = {f: None for f in faces}
    queue = []
    for f in faces:
        for e in f.edges:
            for g in e.link_faces:
                if g not in created:
                    sf = _loop_start(f, e)
                    sg = _loop_start(g, e)
                    if sf is not None and sg is not None:
                        rev[f] = (sf == sg)
                        queue.append(f)
                        break
            if rev[f] is not None:
                break
    head = 0
    while head < len(queue):
        a = queue[head]
        head += 1
        for e in a.edges:
            sa_cur = _loop_start(a, e)
            if sa_cur is None:
                continue
            sa = 1 - sa_cur if rev[a] else sa_cur
            for b in e.link_faces:
                if b in created and rev[b] is None:
                    sb_cur = _loop_start(b, e)
                    if sb_cur is None:
                        continue
                    want = 1 - sa
                    rev[b] = (sb_cur != want)
                    queue.append(b)
    for f in faces:
        if rev.get(f):
            f.normal_flip()


def _clean_leftover_wires(bm, involved_verts):
    targets = []
    for v in involved_verts:
        if not v.is_valid:
            continue
        for e in v.link_edges:
            if not e.is_valid:
                continue
            if not e.link_faces:
                targets.append(e)
    if targets:
        bmesh.ops.delete(bm, geom=list(set(targets)), context='EDGES')
