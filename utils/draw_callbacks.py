# Shared draw callbacks for modal operators
# Used by mirror, align, extend, flatten, bisect, weld operators

from bpy_extras import view3d_utils
from .gpu_utils import draw_circle_2d, draw_hud_text
from ..constants import (
    COLOR_SNAP_PREVIEW, CIRCLE_POINT_RADIUS, CIRCLE_POINT_OUTLINE,
    COLOR_BBOX_ORIGINAL_VISIBLE, COLOR_BBOX_ORIGINAL_DIM,
)


def _draw_hud(self, context):
    hud = getattr(self, '_hud_text', None)
    if hud:
        draw_hud_text(hud, context)


def _draw_preview(self, context):
    preview = getattr(self, '_preview_pt', None)
    if preview is None:
        return
    co2d = view3d_utils.location_3d_to_region_2d(
        context.region, context.region_data, preview)
    if co2d:
        draw_circle_2d(co2d, CIRCLE_POINT_RADIUS, COLOR_SNAP_PREVIEW, filled=True)
        draw_circle_2d(co2d, CIRCLE_POINT_OUTLINE, COLOR_SNAP_PREVIEW, filled=False)


def draw_mirror_3d(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.widget.draw_3d(context)
    mirror_mode = getattr(self, '_mirror_mode', 'DUPLICATE')
    color = COLOR_BBOX_ORIGINAL_VISIBLE if mirror_mode == 'DUPLICATE' else COLOR_BBOX_ORIGINAL_DIM
    self.preview_drawer.draw_original_3d(context, color=color)
    plane_center, normal = self.widget.get_plane_params(context)
    if plane_center and normal:
        self.preview_drawer.draw_mirrored_3d(context, plane_center, normal)


def draw_mirror_2d(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.widget.draw_2d(context)
    _draw_preview(self, context)
    _draw_hud(self, context)


def draw_align(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.drawer.draw(context)

    preview = getattr(self, '_preview_pt', None)
    if preview is not None:
        co2d = view3d_utils.location_3d_to_region_2d(
            context.region, context.region_data, preview)
        if co2d:
            num = len(self.drawer.points)
            col_idx = num // self.drawer.group_size
            c = self.drawer.color_map[col_idx] if col_idx < len(self.drawer.color_map) else (1, 1, 1, 1)
            preview_color = (c[0], c[1], c[2], 0.5)
            draw_circle_2d(co2d, 6, preview_color, filled=True)
            draw_circle_2d(co2d, 8, preview_color, filled=False)
    _draw_hud(self, context)


def draw_widget_3d(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.widget.draw_3d(context)


def draw_extend(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.widget.draw_2d(context)
    plane_center, normal = self.widget.get_plane_params(context)
    if plane_center and normal:
        self.preview_drawer.draw(context, plane_center, normal, self.extend_far_point)
    _draw_preview(self, context)
    _draw_hud(self, context)


def draw_flatten(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.widget.draw_2d(context)
    plane_center, normal = self.widget.get_plane_params(context)
    if plane_center and normal:
        self.preview_drawer.draw(context, plane_center, normal)
    _draw_preview(self, context)
    _draw_hud(self, context)


def draw_flatten_object(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.widget.draw_2d(context)
    plane_center, normal = self.widget.get_plane_params(context)
    if plane_center and normal:
        self.preview_drawer.draw(context, plane_center, normal)
    _draw_preview(self, context)
    _draw_hud(self, context)


def draw_bisect(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.widget.draw_2d(context)
    plane_center, normal = self.widget.get_plane_params(context)
    if plane_center and normal:
        self.preview_drawer.draw(context, plane_center, normal, self.use_fill, self.clear_inner, self.clear_outer)
    _draw_preview(self, context)
    _draw_hud(self, context)


def draw_bisect_object(self, context):
    if context.region != getattr(self, "init_region", None):
        return
    self.widget.draw_2d(context)
    _draw_preview(self, context)
    _draw_hud(self, context)


def draw_intersections(self, context):
    self.preview_drawer.draw(context)
    _draw_hud(self, context)


def draw_weld(self, context):
    self.preview_drawer.draw(context)
    _draw_hud(self, context)
