import numpy as np
import bmesh
import mathutils
from mathutils import Vector
from bpy_extras import view3d_utils


class ScreenVertexSnapper:
    """屏幕空间顶点吸附器：双路径(NP投影 + Raycast)找最近吸附点"""

    def __init__(self):
        self.cached_points_np = None  # (N, 3) 世界空间吸附点数组
        self.cached_objects = set()   # 选中物体的名称集合，Ray路径跳过这些

    # ==========================================
    # 构建吸附点缓存（一次性，invoke或某些update时调用）
    # ==========================================
    def build_cache(self, context):
        self.cached_objects.clear()
        all_points_list = []

        for obj in context.visible_objects:
            # 非网格物体：只取原点作为吸附点（灯光、相机、空物体等）
            if obj.type != 'MESH':
                all_points_list.append(np.array(obj.matrix_world.translation, dtype=np.float32).reshape(1, 3))
                continue

            if obj.mode == 'EDIT':
                obj.update_from_editmode()

            mesh = obj.data
            num_verts = len(mesh.vertices)
            if num_verts == 0:
                continue

            # foreach_get 一次性取全部顶点坐标
            verts = np.zeros(num_verts * 3, dtype=np.float32)
            mesh.vertices.foreach_get("co", verts)
            verts = verts.reshape((num_verts, 3))

            if obj.select_get():
                # 选中物体：记录名称→Ray路径排除；吸附全部顶点+边中点
                self.cached_objects.add(obj.name)
                num_edges = len(mesh.edges)
                if num_edges > 0:
                    edges = np.zeros(num_edges * 2, dtype=np.int32)
                    mesh.edges.foreach_get("vertices", edges)
                    edges = edges.reshape((num_edges, 2))
                    edge_centers = (verts[edges[:, 0]] + verts[edges[:, 1]]) * 0.5
                    all_points = np.vstack((verts, edge_centers))
                else:
                    all_points = verts
            else:
                # 未选中物体：只取无面浮点 + 松散边中点（减噪，不吸附面内部顶点）
                num_loops = len(mesh.loops)
                if num_loops > 0:
                    loop_verts = np.zeros(num_loops, dtype=np.int32)
                    mesh.loops.foreach_get("vertex_index", loop_verts)
                    linked_verts = np.unique(loop_verts)
                    floating_mask = np.ones(num_verts, dtype=bool)
                    floating_mask[linked_verts] = False
                    floating_verts = verts[floating_mask]
                else:
                    floating_verts = verts

                num_edges = len(mesh.edges)
                if num_edges > 0:
                    is_loose = np.zeros(num_edges, dtype=bool)
                    mesh.edges.foreach_get("is_loose", is_loose)
                    if np.any(is_loose):
                        edges = np.zeros(num_edges * 2, dtype=np.int32)
                        mesh.edges.foreach_get("vertices", edges)
                        loose_edges = edges.reshape((num_edges, 2))[is_loose]
                        edge_centers = (verts[loose_edges[:, 0]] + verts[loose_edges[:, 1]]) * 0.5
                        all_points = np.vstack((floating_verts, edge_centers)) if len(floating_verts) > 0 else edge_centers
                    else:
                        all_points = floating_verts
                else:
                    all_points = floating_verts

                if len(all_points) == 0:
                    continue

            # 坐标转世界空间
            num_all = len(all_points)
            coords_4d = np.empty((num_all, 4), dtype=np.float32)
            coords_4d[:, :3] = all_points
            coords_4d[:, 3] = 1.0

            world_matrix = np.array(obj.matrix_world, dtype=np.float32)
            world_pts = (coords_4d @ world_matrix.T)[:, :3]
            all_points_list.append(world_pts)

        if all_points_list:
            self.cached_points_np = np.vstack(all_points_list)
        else:
            self.cached_points_np = None

    # ==========================================
    # 场景射线（鼠标→3D世界）
    # ==========================================
    def _ray_cast_scene(self, context, event):
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
    # Ray路径：场景射线击中面后，枚举面的顶点/边中点，取屏幕最近
    # 注意：跳过 cached_objects（选中物体）中的面，由NP路径覆盖
    # 作用：覆盖NP遗漏的物体（如非选中物体上被面覆盖的顶点）
    # ==========================================
    def _get_raycast_nearest(self, context, event):
        obj, world_pos, face_index = self._ray_cast_scene(context, event)
        # 未击中 / 击中选中物体(NP已缓存) / face_index无效 → 跳过
        if not obj or obj.name in self.cached_objects or face_index < 0:
            return None, float('inf')

        pts = []
        world_mat = obj.matrix_world

        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            if face_index < len(bm.faces):
                face = bm.faces[face_index]
                pts.extend([world_mat @ v.co for v in face.verts])
                pts.extend([world_mat @ ((e.verts[0].co + e.verts[1].co) * 0.5) for e in face.edges])
        else:
            mesh = obj.data
            if face_index < len(mesh.polygons):
                poly = mesh.polygons[face_index]
                for v_idx in poly.vertices:
                    pts.append(world_mat @ mesh.vertices[v_idx].co)
                for edge_key in poly.edge_keys:
                    v1_co = mesh.vertices[edge_key[0]].co
                    v2_co = mesh.vertices[edge_key[1]].co
                    pts.append(world_mat @ ((v1_co + v2_co) * 0.5))

        if not pts:
            return None, float('inf')

        region = context.region
        rv3d = context.space_data.region_3d
        mouse = (event.mouse_region_x, event.mouse_region_y)

        best_pt = None
        min_dist_sq = float('inf')

        for p in pts:
            co2d = view3d_utils.location_3d_to_region_2d(region, rv3d, p)
            if co2d:
                dist_sq = (co2d[0] - mouse[0])**2 + (co2d[1] - mouse[1])**2
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    best_pt = p

        return best_pt, min_dist_sq

    # ==========================================
    # 找最近吸附点：NP路径 + Raycast路径 → 取屏幕更近者
    #
    # NP路径：
    #   - 全部缓存点投影到屏幕 → 批量计算距离
    #   - XRay模式：直接取最近（无遮挡检测）
    #   - 非XRay模式：从相机向最近点发射线，命中其他物体→标记遮挡→重选下一个
    #   - 保护：最多重试10次；最近点>2500px → 放弃NP
    #
    # Ray路径：
    #   - 场景射线击中面 → 枚举面的顶点/边中点 → 取屏幕最近
    #   - 补充NP遗漏（非选中物体上的面顶点）
    #
    # 双路比较 → 返回屏幕距离更近的吸附点
    # ==========================================
    def find_nearest(self, context, event):
        mouse_x, mouse_y = event.mouse_region_x, event.mouse_region_y
        region = context.region
        rv3d = context.space_data.region_3d

        np_pt = None
        np_dist_sq = float('inf')

        # ── NP路径 ──
        if self.cached_points_np is not None:
            persp_matrix = np.array(rv3d.perspective_matrix, dtype=np.float32)
            num_points = len(self.cached_points_np)

            coords_4d = np.empty((num_points, 4), dtype=np.float32)
            coords_4d[:, :3] = self.cached_points_np
            coords_4d[:, 3] = 1.0

            ndc = coords_4d @ persp_matrix.T
            w = ndc[:, 3:4]

            # 剔除视锥体后方(w≤0)的点
            valid_mask = w[:, 0] > 0
            if np.any(valid_mask):
                ndc = ndc / np.where(w == 0, 1e-8, w)
                screen_x = (ndc[:, 0] + 1.0) * (region.width / 2.0)
                screen_y = (ndc[:, 1] + 1.0) * (region.height / 2.0)

                dx = screen_x - mouse_x
                dy = screen_y - mouse_y
                dist_sq = dx**2 + dy**2
                dist_sq[~valid_mask] = np.inf

                show_xray = context.space_data.shading.show_xray
                depsgraph = context.evaluated_depsgraph_get()
                camera_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (mouse_x, mouse_y))

                # 逐点取最近→检查遮挡→被遮挡则取下一个（最多10次）
                for _ in range(10):
                    min_idx = np.argmin(dist_sq)
                    # 无可选点 或 最近点距鼠标超50px → 放弃NP
                    if dist_sq[min_idx] == np.inf or dist_sq[min_idx] > 2500:
                        break

                    p_world = Vector(self.cached_points_np[min_idx])

                    # XRay模式：无遮挡检测，直接取
                    if not show_xray:
                        # 从相机向该点发射线，检查是否被其他物体遮挡
                        ray_dir = (p_world - camera_origin).normalized()
                        hit, hit_loc, _, _, _, _ = context.scene.ray_cast(depsgraph, camera_origin, ray_dir)
                        if hit:
                            hit_dist = (hit_loc - p_world).length
                            if hit_dist > 0.01:  # 击中的不是该点本身 → 被遮挡
                                dist_sq[min_idx] = np.inf
                                continue

                    np_pt = p_world
                    np_dist_sq = dist_sq[min_idx]
                    break

        # ── Ray路径 ──
        ray_pt, ray_dist_sq = self._get_raycast_nearest(context, event)

        if np_pt is None and ray_pt is None:
            return None

        # 双路比较：取屏幕距离更近者
        if np_dist_sq < ray_dist_sq:
            return np_pt
        return ray_pt
