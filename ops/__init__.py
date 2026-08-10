import importlib
from . import mesh
from . import object

_module_list = [mesh, object]

def register():
    for module in _module_list:
        if hasattr(module, "register"):
            module.register()

def unregister():
    for module in reversed(_module_list):
        if hasattr(module, "unregister"):
            module.unregister()

def update():
    for module in _module_list:
        if hasattr(module, "update"):
            module.update()
    unregister()
    for module in _module_list:
        importlib.reload(module)
    register()
