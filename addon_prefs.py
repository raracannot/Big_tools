import bpy
from . import __package__ as base_package


class BigToolsPreferences(bpy.types.AddonPreferences):
    bl_idname = base_package

    def draw(self, context):
        layout = self.layout
        layout.label(text="Big Tools - 工具集", icon='TOOL_SETTINGS')


classes = (BigToolsPreferences,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)