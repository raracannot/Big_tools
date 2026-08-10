import importlib
from . import panel

_module_list = [panel]


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
