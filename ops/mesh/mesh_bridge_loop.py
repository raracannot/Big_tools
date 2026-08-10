# 简易桥接
import math
import bpy
import bmesh
import mathutils
from gpu_extras.batch import batch_for_shader
from ...utils.gpu_utils import SHADER, draw_lines_2d, draw_circle_2d, draw_text_2d, draw_hud_text
from bpy_extras import view3d_utils


# ==========================================
# Bridge loop helpers
# ==========================================

def edgekey(edge):
    return tuple(sorted([edge.verts[0].index, edge.verts[1].index]))


def get_connected_selections(edge_keys):
    vert_verts = {}
    for ek in edge_keys:
        for i in range(2):
            vert_verts.setdefault(ek[i], []).append(ek[1 - i])
    loops = []
    while vert_verts:
        loop = [next(iter(vert_verts))]
        flipped = False
        growing = True
        while growing:
            if loop[-1] not in vert_verts:
                if not flipped:
                    loop.reverse()
                    flipped = True
                else:
                    growing = False
            else:
                extended = False
                for i, nv in enumerate(vert_verts[loop[-1]]):
                    if nv not in loop:
                        vert_verts[loop[-1]].pop(i)
                        if not vert_verts[loop[-1]]:
                            del vert_verts[loop[-1]]
                        if nv in vert_verts:
                            if len(vert_verts[nv]) == 1:
                                del vert_verts[nv]
                            else:
                                vert_verts[nv].remove(loop[-1])
                        loop.append(nv)
                        extended = True
                        break
                if not extended:
                    if not flipped:
                        loop.reverse()
                        flipped = True
                    else:
                        growing = False
        if loop[0] in vert_verts and loop[-1] in vert_verts[loop[0]]:
            if len(vert_verts[loop[0]]) == 1:
                del vert_verts[loop[0]]
            else:
                vert_verts[loop[0]].remove(loop[-1])
            if len(vert_verts[loop[-1]]) == 1:
                del vert_verts[loop[-1]]
            else:
                vert_verts[loop[-1]].remove(loop[0])
            loops.append([loop, True])
        else:
            loops.append([loop, False])
    return loops


def bridge_get_input(bm, edges=None):
    target_e = edges if edges else bm.edges
    sel = [edgekey(e) for e in target_e if e.select and not e.hide and edgekey(e)]
    return get_connected_selections(sel)


def bridge_calculate_lines(bm, loops, twist, reverse):
    lines = []
    loop1, loop2 = [l[0] for l in loops]
    loop1c, loop2c = [l[1] for l in loops]
    circular = loop1c or loop2c
    circle_full = False

    centers = []
    for loop in (loop1, loop2):
        c = mathutils.Vector()
        for v in loop:
            c += bm.verts[v].co
        c /= len(loop)
        c2 = c.copy()
        for v in loop:
            if bm.verts[v].co == c2:
                c2 += mathutils.Vector((0.01, 0, 0))
                break
        centers.append(c2)
    center1, center2 = centers

    normals = []
    for i, loop in enumerate((loop1, loop2)):
        mat = mathutils.Matrix.Identity(3)
        cx, cy, cz = centers[i]
        for loc in [bm.verts[v].co for v in loop]:
            dx = loc[0] - cx
            dy = loc[1] - cy
            dz = loc[2] - cz
            mat[0][0] += dx * dx
            mat[1][0] += dx * dy
            mat[2][0] += dx * dz
            mat[0][1] += dy * dx
            mat[1][1] += dy * dy
            mat[2][1] += dy * dz
            mat[0][2] += dz * dx
            mat[1][2] += dz * dy
            mat[2][2] += dz * dz
        try:
            mat.invert()
        except Exception:
            normal = mathutils.Vector((0, 0, 1))
            normals.append(normal)
            continue
        vec = mathutils.Vector((1, 1, 1))
        for _ in range(500):
            nvec = (mat @ vec)
            if nvec.length > 0:
                nvec.normalize()
            if (nvec - vec).length < 1e-6:
                break
            vec = nvec
        normals.append(vec if vec.length > 0 else mathutils.Vector((0, 0, 1)))

    if ((center1 + normals[0]) - center2).length < ((center1 - normals[0]) - center2).length:
        normals[0].negate()
    if ((center2 + normals[1]) - center1).length > ((center2 - normals[1]) - center1).length:
        normals[1].negate()

    axis = normals[0].cross(normals[1])
    for i in range(3):
        if abs(axis[i]) < 1e-8:
            axis[i] = 0
    if axis.angle(mathutils.Vector((0, 0, 1)), 0) > 1.5707964:
        axis.negate()
    angle = normals[0].dot(normals[1])
    rotation_matrix = mathutils.Matrix.Rotation(angle, 4, axis)

    if circular:
        if loop2c and not loop1c:
            loop1c, loop2c = True, False
            loop1, loop2 = loop2, loop1
        target = bm.verts[loop2[0]].co - center2
        dif = [[(rotation_matrix @ (bm.verts[v].co - center1)).angle(target, 0), i] for i, v in enumerate(loop1)]
        dif.sort()
        if dif:
            if len(loop1) != len(loop2):
                limit = dif[0][0] * 1.2
                dif2 = []
                for ang, idx in dif:
                    if ang <= limit:
                        dist = (bm.verts[loop2[0]].co - bm.verts[loop1[idx]].co).length
                        dif2.append([dist, ang, idx])
                if dif2:
                    dif2.sort()
                    loop1 = loop1[dif2[0][2]:] + loop1[:dif2[0][2]]
            else:
                loop1 = loop1[dif[0][1]:] + loop1[:dif[0][1]]

    if not circular:
        angles = [(bm.verts[loop1[0]].co - center1).cross(bm.verts[loop1[1]].co - center1).angle(normals[0], 0),
                  (bm.verts[loop2[0]].co - center2).cross(bm.verts[loop2[1]].co - center2).angle(normals[1], 0)]
        limit = 1.5707964
        aok = (angles[0] > limit and angles[1] > limit) or (angles[0] < limit and angles[1] < limit)
        if not aok:
            loop1.reverse()
        elif normals[0].angle(normals[1]) > limit:
            loop1.reverse()

    f2f = (bm.verts[loop1[0]].co - center1).angle(bm.verts[loop2[0]].co - center2, 0)
    f2l = (bm.verts[loop1[0]].co - center1).angle(bm.verts[loop2[-1]].co - center2, 0)
    if f2f > f2l:
        loop1.reverse()

    if len(loop1) == len(loop2):
        if twist:
            loop1 = loop1[twist:] + loop1[:twist] if abs(twist) < len(loop1) else loop1
        if reverse:
            loop1.reverse()
        lines.append([loop1[0], loop2[0]])
        for i in range(1, len(loop1)):
            lines.append([loop1[i], loop2[i]])
    else:
        if len(loop2) > len(loop1):
            loop1, loop2 = loop2, loop1
            loop1c, loop2c = loop2c, loop1c
        if twist:
            loop1 = loop1[twist:] + loop1[:twist] if abs(twist) < len(loop1) else loop1
        if reverse:
            loop1.reverse()
        if loop1c and not loop2c:
            shift = 1
            while shift:
                if len(loop1) - shift < len(loop2):
                    break
                ta = (rotation_matrix @ (bm.verts[loop1[-1]].co - center1)).angle(bm.verts[loop2[-1]].co - center2, 0)
                tb = (rotation_matrix @ (bm.verts[loop1[-1]].co - center1)).angle(bm.verts[loop2[0]].co - center2, 0)
                if tb < ta:
                    loop1 = [loop1[-1]] + loop1[:-1]
                    shift += 1
                else:
                    break
        lines.append([loop1[0], loop2[0]])
        pv2 = 0
        for i in range(len(loop1) - 1):
            if pv2 == len(loop2) - 1 and not loop2c:
                tri, quad = 0, 1
            elif pv2 == len(loop2) - 1 and loop2c:
                tri = (bm.verts[loop1[i + 1]].co - bm.verts[loop2[pv2]].co).length
                quad = (bm.verts[loop1[i + 1]].co - bm.verts[loop2[0]].co).length
                circle_full = 2
            elif len(loop1) - 1 - i == len(loop2) - 1 - pv2 and not circle_full:
                tri, quad = 1, 0
            else:
                rng = range(pv2, min(pv2 + 2, len(loop2)))
                dists = [(bm.verts[loop1[i + 1]].co - bm.verts[loop2[j]].co).length for j in rng]
                tri, quad = dists[0], dists[1] if len(dists) > 1 else dists[0]
            if tri < quad:
                lines.append([loop1[i + 1], loop2[pv2]])
                if circle_full == 2:
                    circle_full = False
            elif not circle_full and pv2 + 1 < len(loop2):
                lines.append([loop1[i + 1], loop2[pv2 + 1]])
                pv2 += 1
            elif loop2c:
                lines.append([loop1[i + 1], loop2[0]])
                pv2 = 0
                circle_full = True
    if loop1c and loop2c:
        lines.append([loop1[0], loop2[0]])
    return lines


def bridge_create_faces(bm, faces, twist):
    for i in range(len(faces)):
        if not faces[i][-1]:
            if faces[i][0] == faces[i][-1]:
                faces[i] = [faces[i][1], faces[i][2], faces[i][3], faces[i][1]]
            else:
                faces[i] = [faces[i][-1]] + faces[i][:-1]
        if faces[i][-1] == faces[i][-2]:
            faces[i] = faces[i][:-1]
    new = []
    for fv in faces:
        try:
            new.append(bm.faces.new([bm.verts[v] for v in fv]))
        except Exception:
            pass
    bm.normal_update()
    return new


def bridge_sort_loops(bm, loops):
    nodes = []
    for loop in loops:
        c = mathutils.Vector()
        for i in loop[0]:
            c += bm.verts[i].co
        c /= len(loop[0])
        nodes.append(c)
    active = 0
    open = list(range(1, len(loops)))
    path = [(0, 0)]
    while open:
        dists = [(nodes[active] - nodes[i]).length for i in open]
        ai = open[dists.index(min(dists))]
        open.remove(ai)
        path.append((ai, min(dists)))
        active = ai
    for i in range(2, len(path)):
        if (nodes[path[i][0]] - nodes[0]).length < path[i][1]:
            path = path[i:] + path[1:i] + [path[0]]
            break
    return [loops[p[0]] for p in path]


def go_bridge(bm, mode, twist, reverse, segments=1):
    cached_loops = bridge_get_input(bm)
    if not cached_loops:
        return
    if len(cached_loops) > 2:
        cached_loops = bridge_sort_loops(bm, cached_loops)
    max_vi = len(bm.verts) - 1
    new_verts = []
    new_faces_data = []
    for i in range(1, len(cached_loops)):
        lines = bridge_calculate_lines(bm, cached_loops[i - 1:i + 1], twist, reverse)
        for li in range(len(lines) - 1):
            v1, v2, v3, v4 = lines[li][0], lines[li + 1][0], lines[li + 1][1], lines[li][1]
            if segments > 1:
                seg_verts = []
                for j in range(segments + 1):
                    t = j / segments
                    nv1 = (1 - t) * bm.verts[v1].co + t * bm.verts[v4].co
                    nv2 = (1 - t) * bm.verts[v2].co + t * bm.verts[v3].co
                    for nv, idx in ((nv1, None), (nv2, None)):
                        merged = None
                        for k, ov in enumerate(bm.verts):
                            if (nv - ov.co).length < 1e-4:
                                merged = k
                                break
                        if merged is None:
                            for k, ov in enumerate(new_verts):
                                if (nv - ov).length < 1e-4:
                                    merged = max_vi + 1 + k
                                    break
                        if merged is None:
                            new_verts.append(nv)
                            max_vi += 1
                            seg_verts.append(max_vi)
                        else:
                            seg_verts.append(merged)
                    if len(seg_verts) == 2:
                        pass
                seg_verts = seg_verts[:segments + 1]
                for j in range(segments):
                    new_faces_data.append([seg_verts[j], seg_verts[j + 1], seg_verts[j + 1 + segments + 1], seg_verts[j + segments + 1]])
            else:
                new_faces_data.append([v1, v2, v3, v4])
    for co in new_verts:
        bm.verts.new(co)
    bm.verts.ensure_lookup_table()
    if new_faces_data:
        new_faces = bridge_create_faces(bm, new_faces_data, twist)
        smooth = bool(bm.faces and sum(f.smooth for f in bm.faces) / len(bm.faces) >= 0.5) if bm.faces else False
        for f in new_faces:
            f.select_set(True)
            f.smooth = smooth
    bmesh.update_edit_mesh(bpy.context.active_object.data, loop_triangles=False, destructive=True)
    bpy.ops.mesh.normals_make_consistent()


# ==========================================
# Bridge Preview
# ==========================================

class BridgePreview:
    def __init__(self):
        self.pair_coords = []

    def update_data(self, bm, loops, twist, reverse, matrix_world):
        self.pair_coords = []
        for i in range(1, len(loops)):
            lines = bridge_calculate_lines(bm, loops[i - 1:i + 1], twist, reverse)
            for a, b in lines:
                self.pair_coords.append((
                    matrix_world @ bm.verts[a].co,
                    matrix_world @ bm.verts[b].co
                ))

    def draw(self, context):
        if not self.pair_coords:
            return
        region = context.region
        rv3d = context.space_data.region_3d
        pts_2d = []
        for ca, cb in self.pair_coords:
            ca_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, ca)
            cb_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, cb)
            if ca_2d and cb_2d:
                pts_2d.extend([ca_2d, cb_2d])
        if pts_2d:
            draw_lines_2d(pts_2d, (0.2, 0.8, 1.0, 0.9), line_width=2.0)

        seen = set()
        for ca, cb in self.pair_coords:
            for c in (ca, cb):
                key = c.to_tuple()
                if key not in seen:
                    seen.add(key)
                    c2d = view3d_utils.location_3d_to_region_2d(region, rv3d, c)
                    if c2d:
                        draw_circle_2d(c2d, 4, (1.0, 0.8, 0.2, 1.0), filled=True)


# ==========================================
# Operator
# ==========================================

class RARA_OT_Model_BridgeLoop(bpy.types.Operator):
    bl_idname = "rara.model_bridge_loop"
    bl_label = "简易桥接"
    bl_description = "桥接多条选中的边循环\n支持不等顶点数的边环智能匹配\n【Ctrl+滚轮】调整扭曲\n【回车/左键】确认执行\n【ESC/右键】取消退出"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    mode: bpy.props.EnumProperty(
        name="模式",
        items=(('basic', "顺序", "按顶点顺序桥接"), ('shortest', "最短", "以最短桥接边优先")),
        default='shortest')
    reverse: bpy.props.BoolProperty(name="反转", default=False)
    twist: bpy.props.IntProperty(name="扭曲", default=0)
    segments: bpy.props.IntProperty(name="分段", default=1, min=1)

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
            self.report({'WARNING'}, "请选择一个编辑模式下的网格对象")
            return {'CANCELLED'}
        self.obj = obj
        self.bm = bmesh.from_edit_mesh(self.obj.data)

        if any(f for f in self.bm.faces if f.select or all(e.select for e in f.edges)):
            self.report({'WARNING'}, "桥接不支持面选择，请仅选择边")
            return {'CANCELLED'}

        self.loops = bridge_get_input(self.bm)
        if len(self.loops) < 2:
            self.report({'WARNING'}, "请至少选中两条边循环")
            return {'CANCELLED'}
        if len(self.loops) > 2:
            self.loops = bridge_sort_loops(self.bm, self.loops)

        self.preview = BridgePreview()
        self.preview.update_data(self.bm, self.loops, self.twist, self.reverse, self.obj.matrix_world)

        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_preview, args, 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        self.update_header(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def draw_preview(self, op, context):
        if context.region != getattr(self, '_init_region', context.region):
            self._init_region = context.region
        self.preview.draw(context)
        hud = getattr(self, '_hud_text', None)
        if hud:
            draw_hud_text(hud, context)

    def update_header(self, context):
        msg = (f"【简易桥接】{len(self.loops)} 条边链 | "
               f"扭曲(Ctrl+滚轮): {self.twist} | "
               f"确认: 回车/左键 | 取消: ESC/右键")
        self._hud_text = msg

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            context.area.tag_redraw()

            if event.type in {'ESC', 'RIGHTMOUSE'}:
                self.finish(context)
                return {'CANCELLED'}

            if event.type in {'RET', 'NUMPAD_ENTER', 'LEFTMOUSE'} and event.value == 'PRESS':
                self.execute_op(context)
                self.finish(context)
                return {'FINISHED'}

            if event.ctrl and event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                self.twist += 1 if event.type == 'WHEELUPMOUSE' else -1
                self.preview.update_data(self.bm, self.loops, self.twist, self.reverse, self.obj.matrix_world)
                self.update_header(context)
                return {'RUNNING_MODAL'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"桥接出错: {str(e)}")
            return {'CANCELLED'}

    def execute_op(self, context):
        go_bridge(self.bm, self.mode, self.twist, self.reverse, self.segments)
        self.report({'INFO'}, f"桥接完成，扭曲:{self.twist}，分段:{self.segments}")

    def finish(self, context):
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if hasattr(self, '_handle') and self._handle:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            except Exception:
                pass
            self._handle = None
        context.area.tag_redraw()
        try:
            self.bm.free()
        except Exception:
            pass


classes = (RARA_OT_Model_BridgeLoop,)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)