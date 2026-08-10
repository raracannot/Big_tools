# 平滑/起泡

import numpy as np
import bpy
import bmesh
import gpu
import blf
from gpu_extras.batch import batch_for_shader
from ...utils.gpu_utils import SHADER, draw_hud_text

SMOOTH_TYPES = [
    ('LAPLACIAN', "拉普拉斯", ""),
    ('CUBIC', "三次样条", ""),
    ('TAUBIN', "双边保形", ""),
    ('BILATERAL', "双边滤波", ""),
    ('MEAN_SHIFT', "均值漂移", ""),
]
SMOOTH_NAMES = [t[1] for t in SMOOTH_TYPES]


def build_adjacency(bm):
    n = len(bm.verts)
    adj = [[] for _ in range(n)]
    es = {e.index for e in bm.edges if e.select}
    for e in bm.edges:
        if e.index in es:
            i, j = e.verts[0].index, e.verts[1].index
            adj[i].append(j)
            adj[j].append(i)
    return adj


# smoothing algorithms (co = (n,3) numpy array, adj = list of neighbor idx)

def laplacian(co, adj, fixed, damping):
    out = co.copy()
    for i in range(len(co)):
        if i in fixed or not adj[i]:
            continue
        avg = np.mean(co[adj[i]], axis=0)
        out[i] = co[i] + (avg - co[i]) * damping
    return out


def cubic(co, adj, fixed, damping):
    # 匹配原始 cubic_smooth_func: lerp 后乘 (3d^2 - 2d^3)
    f = damping * (3 * damping ** 2 - 2 * damping ** 3)
    out = co.copy()
    for i in range(len(co)):
        if i in fixed or not adj[i]:
            continue
        avg = np.mean(co[adj[i]], axis=0)
        out[i] = co[i] + (avg - co[i]) * f
    return out


def taubin(co, adj, fixed, damping):
    # 原始: smooth_pass(damping) + smooth_pass(-damping)
    co = laplacian(co, adj, fixed, damping)
    co = laplacian(co, adj, fixed, -damping)
    return co


def bilateral(co, adj, fixed, damping):
    sigma_s = 1.0
    sigma_r = 0.1
    out = co.copy()
    for i in range(len(co)):
        if i in fixed or not adj[i]:
            continue
        nb = adj[i]
        diffs = co[i] - co[nb]
        dists = np.linalg.norm(diffs, axis=1)
        ws = np.exp(-dists ** 2 / (2 * sigma_s ** 2))
        wr = np.exp(-dists ** 2 / (2 * sigma_r ** 2))
        w = ws * wr
        den = w.sum()
        if den > 1e-12:
            numer = (co[nb] * w[:, None]).sum(axis=0)
            out[i] = co[i] + (numer / den - co[i]) * damping
    return out


def mean_shift(co, adj, fixed, bandwidth):
    out = co.copy()
    bw2 = 2 * bandwidth ** 2
    for i in range(len(co)):
        if i in fixed or not adj[i]:
            continue
        nb = adj[i]
        diffs = co[i] - co[nb]
        dists = (diffs ** 2).sum(axis=1)
        w = np.exp(-dists / bw2)
        den = w.sum()
        if den > 0:
            out[i] = (co[nb] * w[:, None]).sum(axis=0) / den
    return out


def run_one_iter(co, adj, fixed, select, smooth_type, damping, bubble_step, normals):
    if abs(bubble_step) > 1e-8:
        for i in range(len(co)):
            # 仅选中的、非固定的顶点起泡，方向沿法线
            if select[i] and i not in fixed and adj[i]:
                co[i] += normals[i] * bubble_step

    if smooth_type == 'LAPLACIAN':
        return laplacian(co, adj, fixed, damping)
    elif smooth_type == 'CUBIC':
        return cubic(co, adj, fixed, damping)
    elif smooth_type == 'TAUBIN':
        return taubin(co, adj, fixed, damping)
    elif smooth_type == 'BILATERAL':
        return bilateral(co, adj, fixed, damping)
    elif smooth_type == 'MEAN_SHIFT':
        return mean_shift(co, adj, fixed, damping)
    return co


def run_smooth(co, adj, fixed, select, smooth_type, damping, bubble_step, iterations, normals):
    for _ in range(iterations):
        co = run_one_iter(co, adj, fixed, select, smooth_type, damping, bubble_step, normals)
    return co


# GPU draw

def draw_preview(self, context):
    try:
        if self._co is None:
            return
    except Exception:
        return

    co_w = self._co  # shape (n, 3)，编辑模式局部坐标
    obj = self.obj
    me = obj.data
    wm = np.array(obj.matrix_world, dtype=np.float64)

    n = len(co_w)
    # 把所有顶点转为世界坐标 (n, 3)
    ones = np.ones((n, 4), dtype=np.float64)
    ones[:, :3] = co_w
    w = (wm @ ones.T).T[:, :3]  # (n, 3)

    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')

    # 青色边线（从 w[i] 取坐标）
    if self._edge_pairs is not None and len(self._edge_pairs) > 0:
        el = []
        for a, b in self._edge_pairs:
            el.append(tuple(w[a]))
            el.append(tuple(w[b]))
        if el:
            SHADER.bind()
            SHADER.uniform_float("color", (0.2, 0.8, 1.0, 0.8))
            batch = batch_for_shader(SHADER, 'LINES', {"pos": el})
            batch.draw(SHADER)

    # 黄色固定点
    fp = [tuple(w[i]) for i in self._fixed_set if i < n]
    if fp:
        SHADER.bind()
        SHADER.uniform_float("color", (1.0, 0.8, 0.2, 1.0))
        gpu.state.point_size_set(8)
        batch = batch_for_shader(SHADER, 'POINTS', {"pos": fp})
        batch.draw(SHADER)
    gpu.state.point_size_set(1)

    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')


def draw_hud_callback(op, context):
    hud = getattr(op, '_hud_text', None)
    draw_hud_text(hud, context)


# Operator

class RARA_OT_Model_SmoothBubble(bpy.types.Operator):
    bl_idname = "rara.model_smooth_bubble"
    bl_label = "平滑/起泡"
    bl_description = (
        "GPU实时预览的平滑/起泡工具，退出时才应用\n"
        "【Tab】切换平滑算法【Ctrl+滚轮】阻尼【Shift+滚轮】迭代\n"
        "【Alt+滚轮】起泡【滚轮】穿透给视口\n"
        "【回车】应用【ESC】取消"
    )
    bl_options = {'REGISTER', 'UNDO'}

    iterations: bpy.props.IntProperty(default=10, min=1, max=255)
    damping: bpy.props.FloatProperty(default=0.6, min=0.0, max=1.0)
    bubble_factor: bpy.props.FloatProperty(default=0.0, min=-5.0, max=5.0)

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH' and context.mode == 'EDIT_MESH'

    def invoke(self, context, event):
        self._smooth_idx = 0
        self._co = None
        self.obj = context.active_object
        self.me = self.obj.data
        bm = bmesh.from_edit_mesh(self.me)

        # 固定点：场景已设置 + 自动提取选中面/边的边界顶点
        self._fixed_set = set(context.scene.get("rara_pinned_verts", []))
        sel_faces = {f for f in bm.faces if f.select}
        sel_verts = {v for v in bm.verts if v.select}
        if sel_faces:
            for f in sel_faces:
                for e in f.edges:
                    linked = [lf for lf in e.link_faces if lf in sel_faces]
                    if len(linked) <= 1:
                        self._fixed_set.update({v.index for v in e.verts})
        elif sel_verts:
            self._fixed_set.update({v.index for v in sel_verts})

        es = {e.index for e in bm.edges if e.select}
        if not es:
            self.report({'WARNING'}, "未选择任何边")
            return {'CANCELLED'}

        n = len(bm.verts)
        self._orig_co = np.zeros((n, 3), dtype=np.float64)
        for v in bm.verts:
            self._orig_co[v.index] = v.co

        # 选中顶点 mask（用于起泡过滤）
        self._select = np.zeros(n, dtype=bool)
        for v in bm.verts:
            if v.select:
                self._select[v.index] = True

        self._adj = build_adjacency(bm)

        bm.normal_update()
        self._normals = np.array([v.normal for v in bm.verts])

        # GPU 绘制用的边和三角面（相对顶点索引，基于原始坐标）
        self._edge_pairs = []
        seen = set()
        for v in bm.verts:
            if not v.select:
                continue
            for e in v.link_edges:
                a, b = e.verts[0].index, e.verts[1].index
                key = (min(a, b), max(a, b))
                if key not in seen and e.index in es:
                    seen.add(key)
                    self._edge_pairs.append(key)
        self._tri_indices = []
        tri_set = set()
        for v_idx in range(n):
            if v_idx in self._fixed_set:
                continue
            nb = self._adj[v_idx]
            for ai in range(len(nb)):
                for bi in range(ai + 1, len(nb)):
                    a, b = nb[ai], nb[bi]
                    if b in self._adj[a]:
                        key = tuple(sorted((v_idx, a, b)))
                        if key not in tri_set:
                            tri_set.add(key)
                            self._tri_indices.append((v_idx, a, b))

        bm.free()

        self._recalc()
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            draw_preview, (self, context), 'WINDOW', 'POST_VIEW')
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            draw_hud_callback, (self, context), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        self._update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _recalc(self):
        self._co = run_smooth(
            self._orig_co.copy(), self._adj, self._fixed_set, self._select,
            SMOOTH_TYPES[self._smooth_idx][0], self.damping,
            self.bubble_factor / self.iterations,
            self.iterations, self._normals
        )

    def _update_header(self, context):
        pinned = len(self._fixed_set)
        msg = (
            f"【平滑起泡】{SMOOTH_NAMES[self._smooth_idx]}(Tab) | "
            f"阻尼:{self.damping:.2f}(Ctrl+滚轮) | "
            f"迭代:{self.iterations}(Shift+滚轮) | "
            f"起泡:{self.bubble_factor:.2f}(Alt+滚轮) | "
            f"固定:{pinned} | "
            f"回车:应用 | ESC:取消"
        )
        self._hud_text = msg

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            context.area.tag_redraw()
            changed = False

            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self._cleanup(context)
                return {'CANCELLED'}
            if event.type in {'RET', 'NUMPAD_ENTER', 'LEFTMOUSE'} and event.value == 'PRESS':
                self._apply(context)
                self._cleanup(context)
                return {'FINISHED'}

            if event.type == 'TAB' and event.value == 'PRESS':
                self._smooth_idx = (self._smooth_idx + 1) % len(SMOOTH_TYPES)
                self._recalc()
                self._update_header(context)
                return {'RUNNING_MODAL'}

            if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and event.value == 'PRESS':
                step = 1 if event.type == 'WHEELUPMOUSE' else -1
                if event.ctrl:
                    self.damping = max(0.0, min(1.0, self.damping + step * 0.05))
                    changed = True
                elif event.shift:
                    self.iterations = max(1, self.iterations + step)
                    changed = True
                elif event.alt:
                    self.bubble_factor = max(-5.0, min(5.0, self.bubble_factor + step * 0.2))
                    changed = True
                # 无修饰键的滚轮：穿透给 Blender 原生视口操作

            if changed:
                self._recalc()
                self._update_header(context)

            return {'PASS_THROUGH'}
        except Exception as e:
            self._cleanup(context)
            self.report({'ERROR'}, f"平滑起泡出错: {str(e)}")
            return {'CANCELLED'}

    def _apply(self, context):
        bm = bmesh.from_edit_mesh(self.me)
        co_np = self._co
        for v in bm.verts:
            c = co_np[v.index]
            v.co = (float(c[0]), float(c[1]), float(c[2]))
        bm.normal_update()
        bmesh.update_edit_mesh(self.me)
        bm.free()
        self.report({'INFO'}, (
            f"平滑完成 ({SMOOTH_NAMES[self._smooth_idx]}, "
            f"阻尼:{self.damping:.2f}, "
            f"迭代:{self.iterations})"
            + (f", 起泡:{self.bubble_factor:.2f}" if abs(self.bubble_factor) > 1e-6 else "")
        ))

    def _cleanup(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if hasattr(self, '_handle') and self._handle:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            except Exception:
                pass
            self._handle = None
        if hasattr(self, '_handle_2d') and self._handle_2d:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, 'WINDOW')
            except Exception:
                pass
            self._handle_2d = None
        try:
            context.area.tag_redraw()
        except Exception:
            pass


classes = (RARA_OT_Model_SmoothBubble,)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)