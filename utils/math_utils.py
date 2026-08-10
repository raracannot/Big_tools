import time
import numpy as np
import bmesh
import bpy
import mathutils
from mathutils import Vector, Matrix
from bpy_extras import view3d_utils


# ==========================================
# Ray casting
# ==========================================
def ray_cast(context, event):
    region = context.region
    rv3d = context.space_data.region_3d
    depsgraph = context.evaluated_depsgraph_get()

    coord = event.mouse_region_x, event.mouse_region_y
    view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

    hit, location, normal, index, hit_object, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector.normalized())
    if hit:
        return hit_object, location, index
    return None, None, None


# ==========================================
# SVD-based 3-point transform
# ==========================================
def compute_3point_transform(dst_pts, src_pts, keep_scale=False):
    src = np.array([p.to_tuple() for p in src_pts])
    dst = np.array([p.to_tuple() for p in dst_pts])
    centroid_src = np.mean(src, axis=0)
    centroid_dst = np.mean(dst, axis=0)
    src_centered = src - centroid_src
    dst_centered = dst - centroid_dst

    dist_src = np.linalg.norm(src_centered[0] - src_centered[1])
    dist_dst = np.linalg.norm(dst_centered[0] - dst_centered[1])
    scale = dist_src / dist_dst if dist_dst > 0 and not keep_scale else 1.0
    dst_scaled = dst_centered * scale

    H = dst_scaled.T @ src_centered
    U, S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T

    if np.linalg.det(R) < 0:
        Vt[2, :] *= -1
        R = Vt.T @ U.T

    mat = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            mat[i][j] = R[i, j] * scale
    trans = Vector(centroid_src) - mat.to_3x3() @ Vector(centroid_dst)
    mat[0][3] = trans.x
    mat[1][3] = trans.y
    mat[2][3] = trans.z
    return mat


# ==========================================
# Double-click detection
# ==========================================
_last_click_time = 0.0

def is_double_click(diff=0.3):
    global _last_click_time
    current_time = time.time()
    time_diff = current_time - _last_click_time
    _last_click_time = current_time
    return time_diff < diff


# ==========================================
# Edge chain extraction
# ==========================================
def get_continuous_edges(obj, offset=0, inversion=False):#old
    bm = bmesh.from_edit_mesh(obj.data)
    selected_edges = [e for e in bm.edges if e.select]
    chains = []
    visited = set()

    for edge in selected_edges:
        if edge in visited:
            continue

        chain = []
        current_edge = edge
        start_vert = edge.verts[0]
        end_vert = edge.verts[1]

        if inversion:
            start_vert, end_vert = end_vert, start_vert

        forward_chain = []
        cv = end_vert
        while True:
            next_edges = [e for e in cv.link_edges if e.select and e not in visited and e != current_edge]
            if len(next_edges) != 1:
                break
            next_edge = next_edges[0]
            forward_chain.append(next_edge)
            visited.add(next_edge)
            cv = next_edge.other_vert(cv)

        backward_chain = []
        cv = start_vert
        while True:
            next_edges = [e for e in cv.link_edges if e.select and e not in visited and e != current_edge]
            if len(next_edges) != 1:
                break
            next_edge = next_edges[0]
            backward_chain.insert(0, next_edge)
            visited.add(next_edge)
            cv = next_edge.other_vert(cv)

        full_chain = backward_chain + [current_edge] + forward_chain
        is_closed_loop = full_chain[0].verts[0] in full_chain[-1].verts or full_chain[0].verts[1] in full_chain[-1].verts
        if is_closed_loop:
            offset = offset % len(full_chain)
            full_chain = full_chain[offset:] + full_chain[:offset]
        chains.append(full_chain)
        visited.update(full_chain)
    return chains


def order_chain_edges_and_verts(chain):
    if not chain:
        return []
    vert_count = {}
    for edge in chain:
        for v in edge.verts:
            vert_count[v] = vert_count.get(v, 0) + 1
    endpoints = [v for v, c in vert_count.items() if c == 1]
    start_vert = endpoints[0] if endpoints else chain[0].verts[0]

    verts = [start_vert]
    current_vert = start_vert
    for edge in chain:
        if edge.verts[0] == current_vert:
            next_vert = edge.verts[1]
        elif edge.verts[1] == current_vert:
            next_vert = edge.verts[0]
        else:
            return []
        verts.append(next_vert)
        current_vert = next_vert
    return verts


def check_y_shape_edges(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    selected_edges = [e for e in bm.edges if e.select]
    vert_edge_count = {}

    for edge in selected_edges:
        for vert in edge.verts:
            vert_edge_count[vert] = vert_edge_count.get(vert, 0) + 1

    for count in vert_edge_count.values():
        if count > 2:
            return True
    return False


# ==========================================
# Curve smoothing & resampling
# ==========================================
def get_smooth_polyline(points, resolution=10):
    """使用 Catmull-Rom 样条生成高分辨率平滑曲线"""
    points = np.asarray(points)
    if len(points) < 3:
        return points

    is_closed = np.linalg.norm(points[0] - points[-1]) < 1e-5

    if is_closed:
        p_minus1 = points[-2]
        p_plus1 = points[1]
    else:
        p_minus1 = points[0] - (points[1] - points[0])
        p_plus1 = points[-1] + (points[-1] - points[-2])

    P = np.vstack([p_minus1, points, p_plus1])

    t = np.linspace(0, 1, resolution, endpoint=False)
    t2 = t * t
    t3 = t2 * t

    P0 = P[:-3]
    P1 = P[1:-2]
    P2 = P[2:-1]
    P3 = P[3:]

    a0 = 2 * P1
    a1 = -P0 + P2
    a2 = 2 * P0 - 5 * P1 + 4 * P2 - P3
    a3 = -P0 + 3 * P1 - 3 * P2 + P3

    t_expand = t[np.newaxis, :, np.newaxis]
    t2_expand = t2[np.newaxis, :, np.newaxis]
    t3_expand = t3[np.newaxis, :, np.newaxis]

    curve = 0.5 * (a0[:, np.newaxis, :] +
                   a1[:, np.newaxis, :] * t_expand +
                   a2[:, np.newaxis, :] * t2_expand +
                   a3[:, np.newaxis, :] * t3_expand)

    curve = curve.reshape(-1, 3)
    curve = np.vstack([curve, points[-1]])
    return curve


def resample_polyline_np(points, segments=None, target_length=None, use_curve=False):
    """使用 NumPy 对折线进行等距重采样，支持曲线平滑"""
    points = np.asarray(points)
    if len(points) < 2:
        return points

    if use_curve and len(points) >= 3:
        points = get_smooth_polyline(points, resolution=10)

    diffs = points[1:] - points[:-1]
    lengths = np.linalg.norm(diffs, axis=1)

    cum_dist = np.insert(np.cumsum(lengths), 0, 0.0)
    total_length = cum_dist[-1]

    if total_length == 0:
        return points

    if segments is not None:
        targets = np.linspace(0, total_length, segments + 1)
    elif target_length is not None:
        targets = np.arange(0, total_length, target_length)
        if total_length - targets[-1] > 1e-5:
            targets = np.append(targets, total_length)
    else:
        return points

    indices = np.searchsorted(cum_dist, targets, side='right') - 1
    indices = np.clip(indices, 0, len(lengths) - 1)

    seg_start_dists = cum_dist[indices]
    seg_lengths = lengths[indices]

    mask = seg_lengths > 1e-6
    t = np.zeros_like(targets)
    t[mask] = (targets[mask] - seg_start_dists[mask]) / seg_lengths[mask]
    t = np.clip(t, 0.0, 1.0)

    A = points[indices]
    B = points[indices + 1]
    new_points = A + t[:, np.newaxis] * (B - A)

    new_points[0] = points[0]
    new_points[-1] = points[-1]
    return new_points


def apply_resampled_chains(bm, chains, chains_verts, new_points_list, auto_merge=True, epsilon=1e-4):
    """优化版：支持替换原边(自动合并) 或 复制生成新边(关闭合并)"""
    old_verts = set()
    edges_to_delete = []

    for chain_edges, verts in zip(chains, chains_verts):
        old_verts.update(verts)
        edges_to_delete.extend(chain_edges)

    old_verts = list(old_verts)

    if auto_merge:
        bmesh.ops.delete(bm, geom=edges_to_delete, context='EDGES')
        valid_old_verts = [v for v in old_verts if v.is_valid]
    else:
        valid_old_verts = []
        for e in bm.edges:
            e.select = False
        for v in bm.verts:
            v.select = False

    all_new_verts = []

    for new_points in new_points_list:
        prev_v = None
        for pt in new_points:
            v = bm.verts.new(pt)
            all_new_verts.append(v)
            if prev_v:
                new_edge = bm.edges.new([prev_v, v])
                if not auto_merge:
                    new_edge.select = True
            if not auto_merge:
                v.select = True
            prev_v = v

    if auto_merge and valid_old_verts and all_new_verts:
        old_co = np.array([v.co for v in valid_old_verts])
        new_co = np.array([v.co for v in all_new_verts])

        diff = old_co[:, np.newaxis, :] - new_co[np.newaxis, :, :]
        dist_sq = np.sum(diff ** 2, axis=2)
        min_indices = np.argmin(dist_sq, axis=1)

        for i, old_v in enumerate(valid_old_verts):
            closest_new_idx = min_indices[i]
            old_v.co = Vector(new_co[closest_new_idx])

        verts_to_merge = valid_old_verts + [v for v in all_new_verts if v.is_valid]
        bmesh.ops.remove_doubles(bm, verts=verts_to_merge, dist=epsilon)


def apply_resampled_chains_preserve_faces(bm, chains, chains_verts, new_points_list, epsilon=1e-4):
    """纯计算保面 v3：
    1. 记录所有相邻面（外部顶点位置 + 链顶点标记）
    2. 删除旧链边和旧链顶点
    3. 创建新重采样边
    4. 外部顶点按位置找回，链段用有序列表定位 → 重建面
    """

    # ── 1. 记录面 ──
    chain_vert_set = set()
    edges_to_delete = []
    for chain_edges, verts in zip(chains, chains_verts):
        chain_vert_set.update(verts)
        edges_to_delete.extend(chain_edges)

    seen_faces = set()
    recorded_faces = []
    for e in edges_to_delete:
        for f in e.link_faces:
            if f not in seen_faces:
                seen_faces.add(f)
                loop = []
                for v in f.verts:
                    loop.append((Vector(v.co), v in chain_vert_set))
                recorded_faces.append(loop)

    # ── 2. 删除旧链边 + 孤立顶点 ──
    bmesh.ops.delete(bm, geom=edges_to_delete, context='EDGES')
    for v in chain_vert_set:
        if v.is_valid:
            bm.verts.remove(v)

    bm.verts.ensure_lookup_table()

    # ── 3. 创建新链 ──
    all_new_verts = []
    chain_ranges = []  # [(start, end)] per chain
    chain_closed = []  # True/False per chain

    for new_points in new_points_list:
        if len(new_points) < 2:
            continue
        # 检测闭合环：首尾位置接近
        np_arr = np.array(new_points, dtype=np.float64)
        closed = np.linalg.norm(np_arr[0] - np_arr[-1]) < 1e-4
        if closed:
            # 首尾重合 → 跳过最后一个点（用第一个点闭合）
            new_points = new_points[:-1]
            np_arr = np_arr[:-1]

        start = len(all_new_verts)
        prev_v = None
        for pt in new_points:
            v = bm.verts.new(pt)
            all_new_verts.append(v)
            if prev_v:
                bm.edges.new([prev_v, v])
            prev_v = v

        if closed and len(new_points) >= 2:
            # 闭合环：连接尾→首
            bm.edges.new([prev_v, all_new_verts[start]])

        chain_ranges.append((start, len(all_new_verts)))
        chain_closed.append(closed)

    # ── 4. 重建面 ──
    valid_bm_verts = [v for v in bm.verts if v.is_valid]

    for face_loop in recorded_faces:
        new_face_verts = []
        i = 0
        while i < len(face_loop):
            pos, is_chain = face_loop[i]
            if not is_chain:
                # 外部顶点 → 位置匹配（残留链点已全部清除）
                best_v, best_d = None, 0.001
                for v in valid_bm_verts:
                    d = (v.co - pos).length
                    if d < best_d:
                        best_d = d
                        best_v = v
                if best_v:
                    new_face_verts.append(best_v)
                i += 1
            else:
                j = i
                while j < len(face_loop) and face_loop[j][1]:
                    j += 1
                first_pos = face_loop[i][0]
                last_pos = face_loop[j - 1][0]
                dist_fl = (first_pos - last_pos).length

                # 闭合环且段跨过首尾 → 全取
                found_ci = None
                for ci, closed in enumerate(chain_closed):
                    if closed and dist_fl < 1e-4:
                        cs, ce = chain_ranges[ci]
                        segment = [all_new_verts[k] for k in range(cs, ce) if all_new_verts[k].is_valid]
                        new_face_verts.extend(segment)
                        found_ci = ci
                        break
                if found_ci is not None:
                    i = j
                    continue

                # 常规链段匹配
                best_start = None
                best_start_ci = None
                best_end = None
                best_end_ci = None
                best_sd, best_ed = 0.1, 0.1  # 10cm 宽容阈值

                for ci, (cs, ce) in enumerate(chain_ranges):
                    for k in range(cs, ce):
                        nv = all_new_verts[k]
                        if not nv.is_valid:
                            continue
                        sd = (nv.co - first_pos).length
                        ed = (nv.co - last_pos).length
                        if sd < best_sd:
                            best_sd = sd
                            best_start = k
                            best_start_ci = ci
                        if ed < best_ed:
                            best_ed = ed
                            best_end = k
                            best_end_ci = ci

                # 同一链内取区间
                if best_start is not None and best_end is not None and best_start_ci == best_end_ci:
                    lo, hi = min(best_start, best_end), max(best_start, best_end)
                    segment = [all_new_verts[k] for k in range(lo, hi + 1) if all_new_verts[k].is_valid]
                    if best_start > best_end:
                        segment.reverse()
                    new_face_verts.extend(segment)
                else:
                    # fallback: 逐个找最近
                    for k in range(i, j):
                        best_d = 1e9
                        best_v = None
                        for nv in all_new_verts:
                            if nv.is_valid:
                                d = (nv.co - face_loop[k][0]).length
                                if d < best_d:
                                    best_d = d
                                    best_v = nv
                        if best_v:
                            new_face_verts.append(best_v)
                i = j

        # 去重相邻重复
        dedup = []
        for v in new_face_verts:
            if not dedup or dedup[-1] != v:
                dedup.append(v)

        if len(dedup) >= 3:
            try:
                bm.faces.new(dedup)
            except Exception:
                pass

    bm.normal_update()


# ==========================================
# OBB orientation (SVD covariance)
# ==========================================
def compute_obb_orientation(vertices_np):
    """对 Nx3 numpy 顶点数组做协方差 SVD，返回主轴的 4x4 旋转矩阵
    Z→最长轴, X→次长轴, Y→最短轴，保证行列式为正"""
    if len(vertices_np) < 3:
        return Matrix.Identity(4)
    centroid = np.mean(vertices_np, axis=0)
    centered = vertices_np - centroid
    cov = (centered.T @ centered) / len(vertices_np)
    U, s, Vh = np.linalg.svd(cov)
    std_x, std_y, std_z = np.array([1, 0, 0]), np.array([0, 1, 0]), np.array([0, 0, 1])
    Z = U[:, 0]; Z *= 1 if np.dot(Z, std_z) >= 0 else -1
    X = U[:, 1]; X *= 1 if np.dot(X, std_x) >= 0 else -1
    Y = U[:, 2]; Y *= 1 if np.dot(Y, std_y) >= 0 else -1
    rot_np = np.array([X, Y, Z]).T
    if np.linalg.det(rot_np) < 0:
        Y *= -1
    return Matrix(np.array([X, Y, Z]).T.tolist()).to_4x4()


def collect_world_vertices(objects):
    """从多个网格对象收集世界空间顶点坐标，返回扁平化 (x,y,z) 元组列表"""
    verts = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        if obj.type != 'MESH':
            continue
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        matrix = obj.matrix_world
        verts.extend([(matrix @ v.co).to_tuple() for v in mesh.vertices])
        eval_obj.to_mesh_clear()
    return verts


def get_group_obb_orientation(objects):
    """对一组网格对象计算世界空间 OBB 方向，返回 4x4 旋转矩阵"""
    verts = collect_world_vertices(objects)
    if not verts:
        return Matrix.Identity(4)
    return compute_obb_orientation(np.array(verts))
