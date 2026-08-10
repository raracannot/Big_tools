import importlib

from . import addon_prefs
from . import ops
from . import ui

_module_list = [addon_prefs, ops, ui]


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
