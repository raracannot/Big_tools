# looptools【松弛 Relax】移植（仅依赖 bpy/bmesh/mathutils，可独立 headless 测试）
#
# 算法与循环提取逻辑逐函数对齐 looptools 官方实现（note/looptools/__init__.py），
# 裁剪掉 mirror 派生网格 / 设置缓存 / 属性面板等耦合，直接在编辑网格上运行。

import math
import bpy
import bmesh
import mathutils


# ==========================================
# 通用：边/面字典
# ==========================================

def edgekey(edge):
    return tuple(sorted([edge.verts[0].index, edge.verts[1].index]))


def face_edgekeys(face):
    return [tuple(sorted([edge.verts[0].index, edge.verts[1].index])) for edge in face.edges]


def dict_edge_faces(bm):
    edge_faces = dict([[edgekey(edge), []] for edge in bm.edges if not edge.hide])
    for face in bm.faces:
        if face.hide:
            continue
        for key in face_edgekeys(face):
            edge_faces[key].append(face.index)
    return edge_faces


def dict_face_faces(bm, edge_faces=False):
    if not edge_faces:
        edge_faces = dict_edge_faces(bm)
    connected_faces = dict([[face.index, []] for face in bm.faces if not face.hide])
    for face in bm.faces:
        if face.hide:
            continue
        for edge_key in face_edgekeys(face):
            for connected_face in edge_faces[edge_key]:
                if connected_face == face.index:
                    continue
                connected_faces[face.index].append(connected_face)
    return connected_faces


def dict_vert_verts(edge_keys):
    vert_verts = {}
    for ek in edge_keys:
        for i in range(2):
            if ek[i] in vert_verts:
                vert_verts[ek[i]].append(ek[1 - i])
            else:
                vert_verts[ek[i]] = [ek[1 - i]]
    return vert_verts


# ==========================================
# 环提取：selected / parallel(all)
# ==========================================

def get_connected_selections(edge_keys):
    vert_verts = dict_vert_verts(edge_keys)
    loops = []
    while len(vert_verts) > 0:
        loop = [next(iter(vert_verts))]
        growing = True
        flipped = False
        while growing:
            if loop[-1] not in vert_verts:
                if not flipped:
                    loop.reverse()
                    flipped = True
                else:
                    growing = False
            else:
                extended = False
                for i, next_vert in enumerate(vert_verts[loop[-1]]):
                    if next_vert not in loop:
                        vert_verts[loop[-1]].pop(i)
                        if len(vert_verts[loop[-1]]) == 0:
                            del vert_verts[loop[-1]]
                        if next_vert in vert_verts:
                            if len(vert_verts[next_vert]) == 1:
                                del vert_verts[next_vert]
                            else:
                                vert_verts[next_vert].remove(loop[-1])
                        loop.append(next_vert)
                        extended = True
                        break
                if not extended:
                    if not flipped:
                        loop.reverse()
                        flipped = True
                    else:
                        growing = False
        if loop[0] in vert_verts:
            if loop[-1] in vert_verts[loop[0]]:
                if len(vert_verts[loop[0]]) == 1:
                    del vert_verts[loop[0]]
                else:
                    vert_verts[loop[0]].remove(loop[-1])
                if len(vert_verts[loop[-1]]) == 1:
                    del vert_verts[loop[-1]]
                else:
                    vert_verts[loop[-1]].remove(loop[0])
                loop = [loop, True]
            else:
                loop = [loop, False]
        else:
            loop = [loop, False]
        loops.append(loop)
    return loops


def get_parallel_loops(bm, loops):
    edge_faces = dict_edge_faces(bm)
    connected_faces = dict_face_faces(bm, edge_faces)
    edgeloops = []
    for loop in loops:
        edgeloop = [[sorted([loop[0][i], loop[0][i + 1]]) for i in range(len(loop[0]) - 1)], loop[1]]
        if loop[1]:
            edgeloop[0].append(sorted([loop[0][-1], loop[0][0]]))
        edgeloops.append(edgeloop[:])
    all_edgeloops = []
    has_branches = False
    for loop in edgeloops:
        all_edgeloops.append(loop[0])
        newloops = [loop[0]]
        verts_used = []
        for edge in loop[0]:
            for v in edge:
                if v not in verts_used:
                    verts_used.append(v)
        while len(newloops) > 0:
            side_a = []
            side_b = []
            for i in newloops[-1]:
                i = tuple(i)
                forbidden_side = False
                if i not in edge_faces:
                    has_branches = True
                    break
                for face in edge_faces[i]:
                    if len(side_a) == 0 and forbidden_side != "a":
                        side_a.append(face)
                        if forbidden_side:
                            break
                        forbidden_side = "a"
                        continue
                    elif side_a[-1] in connected_faces[face] and forbidden_side != "a":
                        side_a.append(face)
                        if forbidden_side:
                            break
                        forbidden_side = "a"
                        continue
                    if len(side_b) == 0 and forbidden_side != "b":
                        side_b.append(face)
                        if forbidden_side:
                            break
                        forbidden_side = "b"
                        continue
                    elif side_b[-1] in connected_faces[face] and forbidden_side != "b":
                        side_b.append(face)
                        if forbidden_side:
                            break
                        forbidden_side = "b"
                        continue
            if has_branches:
                break
            newloops.pop(-1)
            sides = []
            if side_a:
                sides.append(side_a)
            if side_b:
                sides.append(side_b)
            for side in sides:
                extraloop = []
                for fi in side:
                    for key in face_edgekeys(bm.faces[fi]):
                        if key[0] not in verts_used and key[1] not in verts_used:
                            extraloop.append(key)
                            break
                if extraloop:
                    for key in extraloop:
                        for new_vert in key:
                            if new_vert not in verts_used:
                                verts_used.append(new_vert)
                    newloops.append(extraloop)
                    all_edgeloops.append(extraloop)
    if has_branches:
        return loops
    loops = []
    for edgeloop in all_edgeloops:
        loop = []
        for i in range(len(edgeloop) - 1):
            for vert in range(2):
                if edgeloop[i][vert] in edgeloop[i + 1]:
                    loop.append(edgeloop[i][vert])
                    break
        if loop:
            for vert in range(2):
                if edgeloop[0][vert] != loop[0]:
                    loop = [edgeloop[0][vert]] + loop
                    break
            for vert in range(2):
                if edgeloop[-1][vert] != loop[-1]:
                    loop.append(edgeloop[-1][vert])
                    break
            if loop[0] == loop[-1]:
                circular = True
                loop = loop[:-1]
            else:
                circular = False
            loops.append([loop, circular])
    return loops


def check_loops(loops, bm):
    valid_loops = []
    for loop, circular in loops:
        if len(loop) < 3:
            continue
        stacked = True
        for i in range(len(loop) - 1):
            if (bm.verts[loop[i]].co - bm.verts[loop[i + 1]].co).length > 1e-6:
                stacked = False
                break
        if stacked:
            continue
        valid_loops.append([loop, circular])
    return valid_loops


# ==========================================
# 样条
# ==========================================

def calculate_cubic_splines(bm, tknots, knots):
    # hack for circular loops
    if knots[0] == knots[-1] and len(knots) > 1:
        circular = True
        k_new1 = []
        for k in range(-1, -5, -1):
            if k - 1 < -len(knots):
                k += len(knots)
            k_new1.append(knots[k - 1])
        k_new2 = []
        for k in range(4):
            if k + 1 > len(knots) - 1:
                k -= len(knots)
            k_new2.append(knots[k + 1])
        for k in k_new1:
            knots.insert(0, k)
        for k in k_new2:
            knots.append(k)
        t_new1 = []
        total1 = 0
        for t in range(-1, -5, -1):
            if t - 1 < -len(tknots):
                t += len(tknots)
            total1 += tknots[t] - tknots[t - 1]
            t_new1.append(tknots[0] - total1)
        t_new2 = []
        total2 = 0
        for t in range(4):
            if t + 1 > len(tknots) - 1:
                t -= len(tknots)
            total2 += tknots[t + 1] - tknots[t]
            t_new2.append(tknots[-1] + total2)
        for t in t_new1:
            tknots.insert(0, t)
        for t in t_new2:
            tknots.append(t)
    else:
        circular = False

    n = len(knots)
    if n < 2:
        return False
    x = tknots[:]
    locs = [bm.verts[k].co[:] for k in knots]
    result = []
    for j in range(3):
        a = [loc[j] for loc in locs]
        h = []
        for i in range(n - 1):
            if x[i + 1] - x[i] == 0:
                h.append(1e-8)
            else:
                h.append(x[i + 1] - x[i])
        q = [False]
        for i in range(1, n - 1):
            q.append(3 / h[i] * (a[i + 1] - a[i]) - 3 / h[i - 1] * (a[i] - a[i - 1]))
        l = [1.0]
        u = [0.0]
        z = [0.0]
        for i in range(1, n - 1):
            l.append(2 * (x[i + 1] - x[i - 1]) - h[i - 1] * u[i - 1])
            if l[i] == 0:
                l[i] = 1e-8
            u.append(h[i] / l[i])
            z.append((q[i] - h[i - 1] * z[i - 1]) / l[i])
        l.append(1.0)
        z.append(0.0)
        b = [False for _ in range(n - 1)]
        c = [False for _ in range(n)]
        d = [False for _ in range(n - 1)]
        c[n - 1] = 0.0
        for i in range(n - 2, -1, -1):
            c[i] = z[i] - u[i] * c[i + 1]
            b[i] = (a[i + 1] - a[i]) / h[i] - h[i] * (c[i + 1] + 2 * c[i]) / 3
            d[i] = (c[i + 1] - c[i]) / (3 * h[i])
        for i in range(n - 1):
            result.append([a[i], b[i], c[i], d[i], x[i]])
    splines = []
    for i in range(len(knots) - 1):
        splines.append([result[i], result[i + n - 1], result[i + (n - 1) * 2]])
    if circular:  # cleaning up after hack
        knots = knots[4:-4]
        tknots = tknots[4:-4]
    return splines


def calculate_linear_splines(bm, tknots, knots):
    splines = []
    for i in range(len(knots) - 1):
        a = bm.verts[knots[i]].co
        b = bm.verts[knots[i + 1]].co
        d = b - a
        t = tknots[i]
        u = tknots[i + 1] - t
        splines.append([a, d, t, u])  # [locStart, locDif, tStart, tDif]
    return splines


def calculate_splines(interpolation, bm, tknots, knots):
    if interpolation == 'cubic':
        return calculate_cubic_splines(bm, tknots, knots[:])
    return calculate_linear_splines(bm, tknots, knots[:])


# ==========================================
# 松弛算法（looptools Relax）
# ==========================================

def relax_calculate_knots(loops):
    all_knots = []
    all_points = []
    for loop, circular in loops:
        knots = [[], []]
        points = [[], []]
        if circular:
            if len(loop) % 2 == 1:  # odd
                extend = [False, True, 0, 1, 0, 1]
            else:  # even
                extend = [True, False, 0, 1, 1, 2]
        else:
            extend = [False, False, 0, 1, 1, 2]
        for j in range(2):
            if extend[j]:
                loop = [loop[-1]] + loop + [loop[0]]
            for i in range(extend[2 + 2 * j], len(loop), 2):
                knots[j].append(loop[i])
            for i in range(extend[3 + 2 * j], len(loop), 2):
                if loop[i] == loop[-1] and not circular:
                    continue
                if len(points[j]) == 0:
                    points[j].append(loop[i])
                elif loop[i] != points[j][0]:
                    points[j].append(loop[i])
            if circular:
                if knots[j][0] != knots[j][-1]:
                    knots[j].append(knots[j][0])
        if len(points[1]) == 0:
            knots.pop(1)
            points.pop(1)
        all_knots.extend(knots)
        all_points.extend(points)
    return all_knots, all_points


def relax_calculate_t(bm, knots, points, regular):
    all_tknots = []
    all_tpoints = []
    for i in range(len(knots)):
        amount = len(knots[i]) + len(points[i])
        mix = []
        for j in range(amount):
            if j % 2 == 0:
                mix.append([True, knots[i][round(j / 2)]])
            elif j == amount - 1:
                mix.append([True, knots[i][-1]])
            else:
                mix.append([False, points[i][int(j / 2)]])
        len_total = 0
        loc_prev = False
        tknots = []
        tpoints = []
        for m in mix:
            loc = mathutils.Vector(bm.verts[m[1]].co[:])
            if not loc_prev:
                loc_prev = loc
            len_total += (loc - loc_prev).length
            if m[0]:
                tknots.append(len_total)
            else:
                tpoints.append(len_total)
            loc_prev = loc
        if regular:
            tpoints = []
            for p in range(len(points[i])):
                tpoints.append((tknots[p] + tknots[p + 1]) / 2)
        all_tknots.append(tknots)
        all_tpoints.append(tpoints)
    return all_tknots, all_tpoints


def relax_calculate_verts(bm, interpolation, tknots, knots, tpoints, points, splines):
    change = []
    move = []
    for i in range(len(knots)):
        for p in points[i]:
            m = tpoints[i][points[i].index(p)]
            if m in tknots[i]:
                n = tknots[i].index(m)
            else:
                t = tknots[i][:]
                t.append(m)
                t.sort()
                n = t.index(m) - 1
            if n > len(splines[i]) - 1:
                n = len(splines[i]) - 1
            elif n < 0:
                n = 0
            if interpolation == 'cubic':
                ax, bx, cx, dx, tx = splines[i][n][0]
                x = ax + bx * (m - tx) + cx * (m - tx) ** 2 + dx * (m - tx) ** 3
                ay, by, cy, dy, ty = splines[i][n][1]
                y = ay + by * (m - ty) + cy * (m - ty) ** 2 + dy * (m - ty) ** 3
                az, bz, cz, dz, tz = splines[i][n][2]
                z = az + bz * (m - tz) + cz * (m - tz) ** 2 + dz * (m - tz) ** 3
                change.append([p, mathutils.Vector([x, y, z])])
            else:  # interpolation == 'linear'
                a, d, t, u = splines[i][n]
                if u == 0:
                    u = 1e-8
                change.append([p, ((m - t) / u) * d + a])
    for c in change:
        move.append([c[0], (bm.verts[c[0]].co + c[1]) / 2])
    return move


# ==========================================
# 入口
# ==========================================

def relax_loops(bm, input_method='selected', interpolation='cubic', iterations=1, regular=True):
    """对选中边环执行 looptools 松弛。返回 (环数, 移动顶点数)。"""
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    edge_keys = [edgekey(edge) for edge in bm.edges if edge.select and not edge.hide]
    loops = get_connected_selections(edge_keys)
    if input_method == 'all':
        loops = get_parallel_loops(bm, loops)
    loops = check_loops(loops, bm)
    if not loops:
        return 0, 0

    knots, points = relax_calculate_knots(loops)
    moved = 0
    for _ in range(max(1, int(iterations))):
        tknots, tpoints = relax_calculate_t(bm, knots, points, regular)
        splines = []
        for i in range(len(knots)):
            splines.append(calculate_splines(interpolation, bm, list(tknots[i]), list(knots[i])))
        move = relax_calculate_verts(bm, interpolation, tknots, knots, tpoints, points, splines)
        for index, loc in move:
            bm.verts[index].co = loc
            moved += 1
    return len(loops), moved


class RARA_OT_Model_LoopToolsRelax(bpy.types.Operator):
    bl_idname = "rara.model_looptools_relax"
    bl_label = "松弛 (LoopTools)"
    bl_description = "对选中的边环做样条松弛平滑（移植自 LoopTools Relax）\n迭代次数越多越平滑；regular=按等距节点分布"
    bl_options = {'REGISTER', 'UNDO'}

    input: bpy.props.EnumProperty(
        name="输入",
        items=(
            ("selected", "仅选中", "只对选中的边环进行松弛"),
            ("all", "全部平行", "把未选中的平行边环也一并纳入输入"),
        ),
        default='selected',
    )
    interpolation: bpy.props.EnumProperty(
        name="插值",
        items=(
            ("cubic", "三次样条", "自然三次样条，结果平滑"),
            ("linear", "线性", "简单快速的线性算法"),
        ),
        default='cubic',
    )
    iterations: bpy.props.IntProperty(name="迭代次数", default=1, min=1, max=25)
    regular: bpy.props.BoolProperty(
        name="等距节点", description="沿环按等距间隔分布节点", default=True)

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        loops_n, moved = relax_loops(
            bm,
            input_method=self.input,
            interpolation=self.interpolation,
            iterations=self.iterations,
            regular=self.regular,
        )
        bmesh.update_edit_mesh(obj.data)
        if moved == 0:
            self.report({'WARNING'}, "没有可用于松弛的边环")
            return {'CANCELLED'}
        self.report({'INFO'}, f"松弛完成：{loops_n} 个环，移动 {moved} 个顶点")
        return {'FINISHED'}


classes = (RARA_OT_Model_LoopToolsRelax,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
