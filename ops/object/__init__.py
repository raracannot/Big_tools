import importlib
from . import obj_fix_rotation
from . import obj_mirror_grid
from . import obj_origin_picker
from . import obj_viewport_move_grid
from . import obj_visual_align_box
from . import obj_visual_layout
from . import obj_visual_layout_align

_module_list = [
    obj_fix_rotation,
    obj_mirror_grid,
    obj_origin_picker,
    obj_viewport_move_grid,
    obj_visual_align_box,
    obj_visual_layout,
    obj_visual_layout_align,
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
