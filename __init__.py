
bl_info = {
    "name": "[test][开发中]网格编辑工具集",
    "author": "RARA(来一点咖啡吗)",
    "version": (2, 3, 2),
    "blender": (5, 1, 0),
    "description": "网格编辑工具集",
    "category": "Mesh",
}

import bpy
import importlib
from . import register_module


class RARA_OT_ReloadPlugin(bpy.types.Operator):
    bl_idname = "rara.reload_addon"
    bl_label = "重载子模块"
    bl_description = "重新加载所有子模块，用于插件开发期间的热更新"
    bl_options = {'REGISTER'}

    def execute(self, context):
        try:
            register_module.update()
            self.report({'INFO'}, "插件重载完成")
        except Exception as e:
            self.report({'ERROR'}, f"重加载失败: {str(e)}")
            return {'CANCELLED'}
        return {'FINISHED'}


def register():
    importlib.reload(register_module)
    register_module.register()
    bpy.utils.register_class(RARA_OT_ReloadPlugin)


def unregister():
    bpy.utils.unregister_class(RARA_OT_ReloadPlugin)
    register_module.unregister()


if bpy.app.background:
    print(f"{bl_info['name']}_V{bl_info['version']} 后台模式忽略加载")
    def register(): pass
    def unregister(): pass


if __name__ == "__main__":
    register()
