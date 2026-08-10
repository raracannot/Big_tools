# Modal operator base mixin and three-point widget
# Shared infrastructure for interactive operators

import math
import gpu
import bpy
from mathutils import Vector
from bpy_extras import view3d_utils
from .gpu_utils import draw_circle_2d, draw_lines_2d, draw_tris_3d
from .rara_snapper import RaraSnapper
from ..constants import (
    COLOR_WIDGET_TRI, COLOR_WIDGET_POINT, COLOR_HIGHLIGHT,
    CIRCLE_POINT_RADIUS, CIRCLE_POINT_OUTLINE, HIT_RADIUS_POINT, EPS,
)


class ThreePointWidget:
    def __init__(self):
        self.points = []
        self.active_idx = None
        self.drag_idx = None

    def draw_3d(self, context):
        if len(self.points) != 3:
            return
        tri_verts = [self.points[0], self.points[1], self.points[2]]
        draw_tris_3d(tri_verts, COLOR_WIDGET_TRI)

    def draw_2d(self, context):
        region, rv3d = context.region, context.space_data.region_3d
        points_2d = []
        for pt in self.points:
            co2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pt)
            if co2d:
                points_2d.append(co2d)

        if len(points_2d) < 2:
            for i, co2d in enumerate(points_2d):
                color = COLOR_HIGHLIGHT if i == self.active_idx else COLOR_WIDGET_POINT
                draw_circle_2d(co2d, CIRCLE_POINT_RADIUS, color, filled=True)
                draw_circle_2d(co2d, CIRCLE_POINT_OUTLINE, color, filled=False)
            return

        lines = []
        for i in range(len(points_2d) - 1):
            lines.extend([points_2d[i], points_2d[i + 1]])
        if len(points_2d) == 3:
            lines.extend([points_2d[2], points_2d[0]])

        draw_lines_2d(lines, COLOR_WIDGET_POINT, line_width=2.0)

        for i, co2d in enumerate(points_2d):
            color = COLOR_HIGHLIGHT if i == self.active_idx else COLOR_WIDGET_POINT
            draw_circle_2d(co2d, CIRCLE_POINT_RADIUS, color, filled=True)
            draw_circle_2d(co2d, CIRCLE_POINT_OUTLINE, color, filled=False)

    def pick_point(self, context, event, radius=HIT_RADIUS_POINT):
        region = context.region
        rv3d = context.space_data.region_3d
        mouse = (event.mouse_region_x, event.mouse_region_y)
        for idx, pt in enumerate(self.points):
            co2d = view3d_utils.location_3d_to_region_2d(region, rv3d, pt)
            if co2d and math.hypot(mouse[0] - co2d[0], mouse[1] - co2d[1]) < radius:
                return idx
        return None

    def get_plane_params(self, context):
        if len(self.points) < 2:
            return None, None

        p1 = Vector(self.points[0])
        p2 = Vector(self.points[1])

        if len(self.points) == 3:
            p3 = Vector(self.points[2])
        else:
            rv3d = context.space_data.region_3d
            p3 = rv3d.view_matrix.inverted().translation

        normal = (p2 - p1).cross(p3 - p1)
        if normal.length < EPS:
            return None, None

        normal.normalize()
        plane_center = (p1 + p2 + p3) / 3.0 if len(self.points) == 3 else (p1 + p2) / 2.0
        return plane_center, normal


class BaseModalMixin:
    def _init_snapper_widget(self, context, preview_drawer_cls=None):
        self.init_region = context.region
        self.snapper = RaraSnapper()
        self.snapper.build_cache(context, scope='ALL')
        self.widget = ThreePointWidget()
        self.preview_drawer = preview_drawer_cls() if preview_drawer_cls else None
        self._handles_3d = []
        self._handles_2d = []
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        # NOTE: callers MUST wrap modal() in try/except that calls _finish_common,
        # otherwise an unhandled exception leaks this timer forever.
        self._preview_pt = None
        if self.preview_drawer:
            for method in ('build_preview', 'build_edit_preview', 'build_object_preview'):
                if hasattr(self.preview_drawer, method):
                    getattr(self.preview_drawer, method)(context)
                    break

    def _handle_left_mouse_press(self, context, event, max_points=3):
        idx = self.widget.pick_point(context, event)
        if idx is not None:
            self.widget.drag_idx = idx
            self.widget.active_idx = idx
            self._preview_pt = None
        elif len(self.widget.points) < max_points and self._preview_pt is not None:
            self.widget.points.append(self._preview_pt)
            self.widget.drag_idx = len(self.widget.points) - 1
            self.widget.active_idx = self.widget.drag_idx
            self._preview_pt = None

    def _handle_mouse_move(self, context, event, max_points=3):
        if self.widget.drag_idx is not None:
            result = self.snapper.find_nearest(
                context, event, snap_vertex=True, snap_half=True,
                penetrate=context.space_data.shading.show_xray)
            if result[0] is not None:
                self.widget.points[self.widget.drag_idx] = result[0]
        else:
            self.widget.active_idx = self.widget.pick_point(context, event)
            if len(self.widget.points) < max_points:
                result = self.snapper.find_nearest(
                    context, event, snap_vertex=True, snap_half=True,
                    penetrate=context.space_data.shading.show_xray)
                self._preview_pt = result[0]
            else:
                self._preview_pt = None

    def _handle_delete_point(self):
        if self.widget.active_idx is not None:
            self.widget.points.pop(self.widget.active_idx)
            self.widget.active_idx = None
            self.widget.drag_idx = None
            return True
        return False

    def _add_draw_handler_3d(self, func, args):
        h = bpy.types.SpaceView3D.draw_handler_add(func, args, 'WINDOW', 'POST_VIEW')
        self._handles_3d.append(h)
        return h

    def _add_draw_handler_2d(self, func, args):
        h = bpy.types.SpaceView3D.draw_handler_add(func, args, 'WINDOW', 'POST_PIXEL')
        self._handles_2d.append(h)
        return h

    def _set_status(self, context, msg):
        self._hud_text = msg

    def _finish_common(self, context):
        if hasattr(self, '_timer') and self._timer:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None
        self._hud_text = None
        for h in self._handles_3d:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
            except Exception:
                pass
        for h in self._handles_2d:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
            except Exception:
                pass
        self._handles_3d.clear()
        self._handles_2d.clear()
        try:
            context.area.tag_redraw()
        except Exception:
            pass
