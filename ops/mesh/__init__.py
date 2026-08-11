import importlib
from . import mesh_bridge_loop
from . import mesh_circle_edges
from . import mesh_copy_paste
from . import mesh_cursor_tools
from . import mesh_curvature_slide
from . import mesh_delete_loose
from . import mesh_evenly_distribute
from . import mesh_extend_edges
from . import mesh_extend_flatten
from . import mesh_find_circle_center
from . import mesh_free_curvature_slide
from . import mesh_flip_normals_by_view
from . import mesh_intersect_edges
from . import mesh_measure
from . import mesh_obj_align
from . import mesh_obj_bisect
from . import mesh_obj_interactive_array
from . import mesh_obj_mirror
from . import mesh_obj_toggle_hidden
from . import mesh_resample_edges
from . import mesh_round_vertices
from . import mesh_slide_edge
from . import mesh_smooth_bubble
from . import mesh_vertical_line
from . import mesh_visual_align
from . import mesh_weld_to_edges
from . import model_slice

_module_list = [
    mesh_bridge_loop,
    mesh_circle_edges,
    mesh_copy_paste,
    mesh_cursor_tools,
    mesh_curvature_slide,
    mesh_delete_loose,
    mesh_evenly_distribute,
    mesh_extend_edges,
    mesh_extend_flatten,
    mesh_find_circle_center,
    mesh_free_curvature_slide,
    mesh_flip_normals_by_view,
    mesh_intersect_edges,
    mesh_measure,
    mesh_obj_align,
    mesh_obj_bisect,
    mesh_obj_interactive_array,
    mesh_obj_mirror,
    mesh_obj_toggle_hidden,
    mesh_resample_edges,
    mesh_round_vertices,
    mesh_slide_edge,
    mesh_smooth_bubble,
    mesh_vertical_line,
    mesh_visual_align,
    mesh_weld_to_edges,
    model_slice,
]

def register():
    for module in _module_list:
        if hasattr(module, "register"):
            module.register()

def unregister():
    for module in reversed(_module_list):
        if hasattr(module, "unregister"):
            module.unregister()

def update():
    unregister()
    for module in _module_list:
        importlib.reload(module)
    register()
