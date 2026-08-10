# 圆化边线

import math
import bpy
import bmesh
import mathutils
from gpu_extras.batch import batch_for_shader
from ...utils.gpu_utils import SHADER, draw_lines_2d, draw_circle_2d
from ...utils.math_utils import get_continuous_edges
from bpy_extras import view3d_utils


# ==========================================
# Circle Edge helpers
# ==========================================

def calculate_circle_center_and_radius(chain):
    verts = list({v for e in chain for v in e.verts})
    center = sum((v.co for v in verts), mathutils.Vector()) / len(verts)
    radius = sum((v.co - center).length for v in verts) / len(verts)
    return center, radius


def calculate_cursor_center_and_radius(chain, context):
    verts = [v for e in chain for v in e.verts]
    center = context.scene.cursor.location.copy()
    radius = sum((v.co - center).length for v in verts) / len(verts)
    return center, radius


def calculate_circle_normal(chain):
    verts = list({v for e in chain for v in e.verts})
    if len(verts) < 3:
        return mathutils.Vector((0, 0, 1))
    v1, v2, v3 = verts[0].co, verts[1].co, verts[2].co
    n = (v2 - v1).cross(v3 - v1)
    return n.normalized() if n.length > 0 else mathutils.Vector((0, 0, 1))


def laplacian_smooth(chain, center, radius, iterations):
    endpoints = set()
    is_closed = chain[0].verts[0] in chain[-1].verts or chain[0].verts[1] in chain[-1].verts
    if not is_closed:
        if len(chain) >= 2:
            start = chain[0].verts[0] if chain[0].verts[0] not in chain[1].verts else chain[0].verts[1]
            end = chain[-1].verts[0] if chain[-1].verts[0] not in chain[-2].verts else chain[-1].verts[1]
        else:
            return
        endpoints = {start, end}

    for _ in range(iterations):
        np = {}
        for e in chain:
            for v in e.verts:
                if v in np:
                    continue
                if v in endpoints:
                    np[v] = v.co
                    continue
                linked = [e2.other_vert(v) for e2 in v.link_edges if e2 in chain]
                if not linked:
                    np[v] = v.co
                    continue
                avg = sum((lv.co for lv in linked), mathutils.Vector()) / len(linked)
                direction = (avg - center).normalized()
                np[v] = v.co.lerp(center + direction * radius, 0.5)
        for v, pos in np.items():
            v.co = pos


def offset_circle(chain, center, normal, factor):
    angle = factor * 2 * math.pi / len(chain)
    rot = mathutils.Matrix.Rotation(angle, 4, normal)
    for e in chain:
        for v in e.verts:
            d = v.co - center
            v.co = center + (d @ rot)


def extract_edge_chain_faces(bm):
    faces = [f for f in bm.faces if f.select]
    if not faces:
        return None
    for e in bm.edges:
        e.select = False
    usage = {e: 0 for e in bm.edges}
    for f in faces:
        for e in f.edges:
            usage[e] += 1
    for e, c in usage.items():
        if c == 1:
            e.select = True
    return True


# ==========================================
# Circle Preview
# ==========================================

class CirclePreview:
    def __init__(self):
        self.center = None
        self.radius = 0.0
        self.chain_pts = []

    def update_data(self, chain, center, radius):
        self.center = center
        self.radius = radius
        self.chain_pts = []
        for e in chain:
            for v in e.verts:
                self.chain_pts.append(v.co)

    def draw(self, context):
        if not self.center or self.radius < 1e-6:
            return
        region = context.region
        rv3d = context.space_data.region_3d
        center_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, self.center)
        if not center_2d:
            return
        draw_circle_2d(center_2d, 6, (0.2, 1.0, 0.5, 1.0), filled=True)
        draw_circle_2d(center_2d, 36, (0.2, 1.0, 0.5, 0.5), filled=False)

        # Draw radius line
        if self.chain_pts:
            mid = self.chain_pts[len(self.chain_pts) // 2]
            mid_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, mid)
            if mid_2d:
                draw_lines_2d([center_2d, mid_2d], (0.2, 1.0, 0.5, 0.6), line_width=1.0)


# ==========================================
# Operator
# ==========================================

class RARA_OT_Model_CircleEdges(bpy.types.Operator):
    bl_idname = "rara.model_circle_edges"
    bl_label = "圆化边线"
    bl_description = "将选中的边线或面的边界圆整为标准圆形\n支持游标或平均圆心、半径缩放、旋转偏移"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object and context.active_object.type == 'MESH'

    circle_factor: bpy.props.FloatProperty(name="圆化系数", default=1.0, min=0.0, max=1.0)
    radius_value: bpy.props.FloatProperty(name="半径缩放", default=1.0, min=0.0)
    smooth_iterations: bpy.props.IntProperty(name="平滑迭代", default=20, min=0, max=100)
    offset_factor: bpy.props.FloatProperty(name="旋转偏移", default=0.0)

    center_method: bpy.props.EnumProperty(
        name="圆心计算",
        items=[
            ('AVERAGE', "平均", "以顶点的平均位置作为圆心"),
            ('CURSOR', "游标", "以游标位置作为圆心"),
        ],
        default='AVERAGE')

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
            self.report({'WARNING'}, "请选择一个编辑模式下的网格对象")
            return {'CANCELLED'}
        self.obj = obj
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)

        extract_edge_chain_faces(bm)

        chains = get_continuous_edges(obj)
        if not chains:
            self.report({'WARNING'}, "未检测到连续的边线")
            return {'CANCELLED'}

        for chain in chains:
            if self.center_method == 'CURSOR':
                center, radius = calculate_cursor_center_and_radius(chain, context)
            else:
                center, radius = calculate_circle_center_and_radius(chain)
            normal = calculate_circle_normal(chain)
            radius *= self.radius_value

            original = {v: v.co.copy() for e in chain for v in e.verts}

            for e in chain:
                for v in e.verts:
                    d = (v.co - center).normalized()
                    v.co = center + d * radius

            offset_circle(chain, center, normal, self.offset_factor)
            laplacian_smooth(chain, center, radius, self.smooth_iterations)

            for e in chain:
                for v in e.verts:
                    v.co = original[v].lerp(v.co, self.circle_factor)

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"圆化完成，共处理 {len(chains)} 条边链")
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "center_method")
        layout.prop(self, "circle_factor")
        layout.prop(self, "radius_value")
        layout.prop(self, "smooth_iterations")
        layout.prop(self, "offset_factor")


classes = (RARA_OT_Model_CircleEdges,)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)