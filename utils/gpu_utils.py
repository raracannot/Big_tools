import gpu
import blf
import math
import time
from mathutils import Vector, Matrix
from gpu_extras.batch import batch_for_shader
from ..constants import BBOX_EDGES, CIRCLE_SEGMENTS, CIRCLE_SEGMENTS_16

SHADER = gpu.shader.from_builtin('UNIFORM_COLOR')
_POLYLINE = None
_VIEWPORT_SIZE = None


def _get_polyline_shader():
    global _POLYLINE
    if _POLYLINE is None:
        try:
            _POLYLINE = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        except ValueError:
            _POLYLINE = SHADER
    return _POLYLINE


CIRCLE_VERTS = [
    (math.cos(2 * math.pi * i / CIRCLE_SEGMENTS), math.sin(2 * math.pi * i / CIRCLE_SEGMENTS))
    for i in range(CIRCLE_SEGMENTS)
]
CIRCLE_VERTS_FAN = [(0.0, 0.0)] + CIRCLE_VERTS + [CIRCLE_VERTS[0]]

CIRCLE_VERTS_16 = [
    (math.cos(2 * math.pi * i / CIRCLE_SEGMENTS_16), math.sin(2 * math.pi * i / CIRCLE_SEGMENTS_16))
    for i in range(CIRCLE_SEGMENTS_16 + 1)
]
CIRCLE_OUTLINE_16 = CIRCLE_VERTS_16[1:]


def make_bbox_wireframe(min_v, max_v, matrix=None):
    local = [
        Vector((min_v.x, min_v.y, min_v.z)), Vector((max_v.x, min_v.y, min_v.z)),
        Vector((max_v.x, max_v.y, min_v.z)), Vector((min_v.x, max_v.y, min_v.z)),
        Vector((min_v.x, min_v.y, max_v.z)), Vector((max_v.x, min_v.y, max_v.z)),
        Vector((max_v.x, max_v.y, max_v.z)), Vector((min_v.x, max_v.y, max_v.z)),
    ]
    if matrix:
        local = [matrix @ v for v in local]
    lines = []
    for a, b in BBOX_EDGES:
        lines.extend([local[a], local[b]])
    return lines


def draw_lines_3d(verts, color, line_width=2.0, depth_test='LESS_EQUAL'):
    if not verts:
        return
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(line_width)
    gpu.state.depth_test_set(depth_test)
    SHADER.bind()
    SHADER.uniform_float("color", color)
    batch = batch_for_shader(SHADER, 'LINES', {"pos": verts})
    batch.draw(SHADER)
    gpu.state.blend_set('NONE')
    gpu.state.line_width_set(1.0)
    gpu.state.depth_test_set('NONE')


def draw_bbox_3d(min_v, max_v, color, matrix=None, line_width=2.0, depth_test='LESS_EQUAL'):
    lines = make_bbox_wireframe(min_v, max_v, matrix)
    draw_lines_3d(lines, color, line_width, depth_test)


def draw_tris_3d(verts, color, depth_test='LESS_EQUAL'):
    if not verts or len(verts) < 3:
        return
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set(depth_test)
    SHADER.bind()
    SHADER.uniform_float("color", color)
    batch = batch_for_shader(SHADER, 'TRIS', {"pos": verts})
    batch.draw(SHADER)
    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')


def make_circle_verts_2d(center, radius):
    return [(center.x + v[0] * radius, center.y + v[1] * radius) for v in CIRCLE_VERTS_FAN]


def draw_lines_2d(verts, color, line_width=2.0, region=None):
    if line_width <= 1.0:
        SHADER.bind()
        SHADER.uniform_float("color", color)
        batch = batch_for_shader(SHADER, 'LINES', {"pos": verts})
        gpu.state.blend_set('ALPHA')
        batch.draw(SHADER)
        gpu.state.blend_set('NONE')
        return

    poly = _get_polyline_shader()
    if poly is SHADER:
        SHADER.bind()
        SHADER.uniform_float("color", color)
        batch = batch_for_shader(SHADER, 'LINES', {"pos": verts})
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(line_width)
        batch.draw(SHADER)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')
        return

    import bpy
    if region is None:
        region = bpy.context.region
    poly.bind()
    poly.uniform_float("lineWidth", line_width)
    poly.uniform_float("color", color)
    global _VIEWPORT_SIZE
    if _VIEWPORT_SIZE is None or _VIEWPORT_SIZE != (region.width, region.height):
        _VIEWPORT_SIZE = (region.width, region.height)
    poly.uniform_float("viewportSize", (region.width, region.height))
    batch = batch_for_shader(poly, 'LINES', {"pos": verts})
    gpu.state.blend_set('ALPHA')
    batch.draw(poly)
    gpu.state.blend_set('NONE')


def draw_circle_2d(center, radius, color, filled=False):
    cx, cy = center[0], center[1]
    if filled:
        verts = [(cx + v[0] * radius, cy + v[1] * radius) for v in CIRCLE_VERTS_FAN]
    else:
        verts = [(cx + v[0] * radius, cy + v[1] * radius) for v in CIRCLE_VERTS_16]

    draw_type = 'TRI_FAN' if filled else 'LINE_STRIP'
    SHADER.bind()
    SHADER.uniform_float("color", color)
    batch = batch_for_shader(SHADER, draw_type, {"pos": verts})
    gpu.state.blend_set('ALPHA')
    batch.draw(SHADER)
    gpu.state.blend_set('NONE')


def draw_dashed_line_2d(points, color, dash_length=10, gap_length=8):
    if len(points) < 2:
        return
    speed = 50
    offset = (time.time() * speed) % (dash_length + gap_length)

    all_verts = []
    for i in range(len(points) - 1):
        _dash_segment(points[i], points[i + 1], offset, dash_length, gap_length, all_verts)
    if len(points) > 2:
        _dash_segment(points[-1], points[0], offset, dash_length, gap_length, all_verts)

    if all_verts:
        draw_lines_2d(all_verts, color, line_width=4.0)


def _dash_segment(a, b, offset, dash_length, gap_length, out_verts):
    vec = (b[0] - a[0], b[1] - a[1])
    length = math.hypot(vec[0], vec[1])
    if length == 0:
        return
    dir = (vec[0] / length, vec[1] / length)
    pos = offset % (dash_length + gap_length)
    while pos < length:
        start_p = (a[0] + dir[0] * pos, a[1] + dir[1] * pos)
        end_pos = min(pos + dash_length, length)
        end_p = (a[0] + dir[0] * end_pos, a[1] + dir[1] * end_pos)
        if end_pos > pos:
            out_verts.extend([start_p, end_p])
        pos += dash_length + gap_length


def draw_text_2d(text, position, color, size=16):
    font_id = 0
    try:
        blf.enable(blf.DEPTH_TEST)
    except AttributeError:
        pass
    blf.size(font_id, size)
    blf.color(font_id, *color)
    blf.position(font_id, position[0], position[1], 0)
    blf.draw(font_id, text)
    try:
        blf.disable(blf.DEPTH_TEST)
    except AttributeError:
        pass


def draw_hud_text(text, context, font_size=20, color=(1, 1, 1, 1)):
    """底部居中 HUD 文本，带阴影，跟随视口刷新，不被其他工具打断"""
    if text == None:
        return
    font_id = 0
    blf.size(font_id, font_size)
    blf.enable(font_id, blf.SHADOW)
    blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.8)
    tw, th = blf.dimensions(font_id, text)
    blf.position(font_id, (context.region.width - tw) / 2, 30, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, text)
    blf.disable(font_id, blf.SHADOW)
