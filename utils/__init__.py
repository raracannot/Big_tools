# Big_tools utils 包

import bpy


def get_pref():
    """返回本插件的 AddonPreferences；注册中途可能取不到时返回 None。

    统一入口：子模块请用相对导入 `from ...utils import get_pref`，
    禁止在子包内硬编码根包名查 preferences。
    """
    from .. import __package__ as base_package
    try:
        return bpy.context.preferences.addons[base_package].preferences
    except (KeyError, AttributeError):
        # 插件注册中途（重载/热更新）addons 集合可能尚未包含本包名
        return None





