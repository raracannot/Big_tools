import mathutils
from mathutils import Vector
from bpy_extras import view3d_utils


class RaySnapper:
    """无需缓存的纯射线吸附器。
    穿透排除物体后，对命中面的顶点/边中点/三等分点/边线投影/面心进行屏幕距离比对。
    返回最近的世界空间吸附点、类型、物体、法线。
    """

    def __init__(self, exclusions=None):
        self._exclusions = set(exclusions) if exclusions else set()
        self._screen_limit = 30.0  # 像素距离阈值，超过不吸附

    # ─── 对外接口 ─────────────────────────────
    def find_nearest(self, context, mouse_x, mouse_y):
        """返回 (world_pos, snap_type, obj, normal)
        snap_type: 'VERT' | 'MIDPOINT' | 'ONETHIRD' | 'EDGE' | 'FACE' | None
        若未命中任何可吸附目标则返回 (None, None, None, None)
        """
        region = context.region
        rv3d = context.region_data

        # 1. 穿透排除物体 → 命中面
        hit_obj, loc, norm, face_idx, mat = self._penetrate_and_hit(context, region, rv3d, mouse_x, mouse_y)
        if not hit_obj:
            return None, None, None, None

        # 2. 获取 evaluated mesh
        mesh = self._get_evaluated_mesh(context, hit_obj)
        if not mesh or face_idx >= len(mesh.polygons):
            return None, None, None, None

        poly = mesh.polygons[face_idx]
        mouse = Vector((mouse_x, mouse_y))
        best_dist = self._screen_limit
        best_pt, best_type = None, None

        # 3. VERT ─ 面上各顶点投影
        for v_idx in poly.vertices:
            world_pt = mat @ mesh.vertices[v_idx].co
            d = self._screen_dist(region, rv3d, world_pt, mouse)
            if d is not None and d < best_dist:
                best_dist = d
                best_pt = world_pt
                best_type = 'VERT'
        if best_type == 'VERT' and best_dist < 8.0:
            return best_pt, best_type, hit_obj, norm

        # 3. MIDPOINT / ONETHIRD ─ 边中点和三等分点
        pv = poly.vertices
        n = len(pv)
        for i in range(n):
            v1 = mat @ mesh.vertices[pv[i]].co
            v2 = mat @ mesh.vertices[pv[(i + 1) % n]].co

            mid = (v1 + v2) * 0.5
            d = self._screen_dist(region, rv3d, mid, mouse)
            if d is not None and d < best_dist:
                best_dist = d
                best_pt = mid
                best_type = 'MIDPOINT'

            for frac in (1.0 / 3.0, 2.0 / 3.0):
                pt = v1 + (v2 - v1) * frac
                d = self._screen_dist(region, rv3d, pt, mouse)
                if d is not None and d < best_dist:
                    best_dist = d
                    best_pt = pt
                    best_type = 'ONETHIRD'

        # 5. EDGE ─ 仅在前几类均未命中时启用兜底
        if best_type is None:
            origin_3d = view3d_utils.region_2d_to_origin_3d(region, rv3d, (mouse_x, mouse_y))
            direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (mouse_x, mouse_y))

            for i in range(n):
                v1 = mat @ mesh.vertices[pv[i]].co
                v2 = mat @ mesh.vertices[pv[(i + 1) % n]].co

                result = mathutils.geometry.intersect_line_line(
                    origin_3d, origin_3d + direction, v1, v2)
                if result is None:
                    continue
                proj_pt = result[1]
                edge_vec = v2 - v1
                edge_len = edge_vec.length
                if edge_len < 1e-6:
                    continue
                t = (proj_pt - v1).dot(edge_vec) / edge_len
                if 0 <= t <= edge_len:
                    d = self._screen_dist(region, rv3d, proj_pt, mouse)
                    if d is not None and d < best_dist:
                        best_dist = d
                        best_pt = proj_pt
                        best_type = 'EDGE'

        # 6. FACE ─ 面心兜底
        if best_pt is None and loc:
            best_pt = loc
            best_type = 'FACE'

        return best_pt, best_type, hit_obj, norm

    # ─── 穿透射线（最多20层） ────────────────
    def _penetrate_and_hit(self, context, region, rv3d, mx, my):
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (mx, my))
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (mx, my))
        depsgraph = context.evaluated_depsgraph_get()

        ray_start = origin
        for _ in range(20):
            hit = context.scene.ray_cast(depsgraph, ray_start, direction)
            if not hit[0]:
                return None, None, None, -1, None
            _, loc, norm, face_idx, obj, mat = hit
            if obj in self._exclusions or (hasattr(obj, "original") and obj.original in self._exclusions):
                ray_start = loc + direction * 0.001
                continue
            return obj, loc, norm, face_idx, mat
        return None, None, None, -1, None

    # ─── 辅助 ─────────────────────────────────
    @staticmethod
    def _get_evaluated_mesh(context, obj):
        depsgraph = context.evaluated_depsgraph_get()
        try:
            eval_obj = obj.evaluated_get(depsgraph)
            return eval_obj.to_mesh()
        except Exception:
            return None

    @staticmethod
    def _screen_dist(region, rv3d, world_pt, mouse):
        co2d = view3d_utils.location_3d_to_region_2d(region, rv3d, world_pt)
        if co2d is None:
            return None
        return (co2d - mouse).length
