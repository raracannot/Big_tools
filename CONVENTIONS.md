# Big_tools 开发规范

---

## 文件命名

```
{domain}_{subdomain}_{action_description}.py
```

| 部分 | 说明 | 示例 |
|---|---|---|
| `domain` | 操作域 | `mesh_` `obj_` `model_` |
| `subdomain` | 作用范围 | `edit_` `obj_` (对象级) |
| `action` | 功能描述 | `flip_normals` `close_loop` `physics_drop` |

- 文件名**只允许英文 ASCII**，禁止中文、空格、括号
- 禁止使用 `[test]` `[abandon]` 等标记前缀
- 禁止将独立 `bl_info` 块留在子模块中（合并残留）

```
mesh_flip_normals.py
obj_physics_drop.py
mesh_close_loop.py
model_slice.py
```

---

## 类命名

| 类型 | 前缀 | 示例 |
|---|---|---|
| Operator | `RARA_OT_` + PascalCase | `RARA_OT_Model_FlipNormals` |
| Panel | `RARA_PT_` + PascalCase | `RARA_PT_MainPanel` |
| Menu | `RARA_MT_` + PascalCase | `RARA_MT_EditorMenu` |
| PropertyGroup | `RARA_` + PascalCase | `RARA_MeasureSettings` |
| Preferences | `BigToolsPreferences` | （固定名称，仅一个） |

### bl_idname 对应规则

| 类型 | bl_idname 格式 |
|---|---|
| Operator | `"rara.{snake_case}"` |
| Panel | `"RARA_PT_{UPPER_SNAKE}"` |
| Menu | `"RARA_MT_{UPPER_SNAKE}"` |

```python
# Operator
class RARA_OT_Model_FlipNormals(bpy.types.Operator):
    bl_idname = "rara.model_flip_normals"

# Panel
class RARA_PT_MainPanel(bpy.types.Panel):
    bl_idname = "RARA_PT_MAIN_PANEL"

# PropertyGroup
class RARA_MeasureSettings(bpy.types.PropertyGroup):
    ...
```

---

## 属性命名

### Scene 属性（挂载到 `bpy.types.Scene`）

```python
bpy.types.Scene.rara_{snake_case} = bpy.props.PointerProperty(type=...)
```

### Preferences 属性

```python
show_{feature_name}: bpy.props.BoolProperty(
    name="中文名", description="中文说明", default=True)
```

---

## 每个 Operator 必须包含

```python
class RARA_OT_Model_FeatureName(bpy.types.Operator):
    bl_idname = "rara.model_feature_name"
    bl_label = "中文标签"
    bl_description = "中文功能说明"   # 必须显式声明，禁止用 docstring 代替
    bl_options = {'REGISTER', 'UNDO'}  # 不可省略
```

- **禁止**使用类 docstring 代替 `bl_description`
- 工具/调试类 operator 如不涉及数据修改，可省略 `bl_options`
- 建议实现 `@classmethod poll(cls, context)` 做上下文守卫

---

## 文件结构模板

```python
# 功能简述

import bpy

class RARA_OT_Model_Xxx(bpy.types.Operator):
    bl_idname = "rara.model_xxx"
    bl_label = "标签"
    bl_description = "描述"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ...
    def execute(self, context):
        ...
        return {'FINISHED'}

classes = (
    RARA_OT_Model_Xxx,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
```

- **禁止**出现 `if __name__ == "__main__": register()`（根 `__init__.py` 除外）
- **禁止**出现独立 `bl_info = {...}` 块

---

## 目录注册模板

每个子包 `__init__.py`：

```python
import importlib

# 显式 import 以支持静态分析
from . import mesh_flip_normals
from . import mesh_close_loop

MODULE_NAMES = [
    "mesh_flip_normals",
    "mesh_close_loop",
]

_module_list = [
    mesh_flip_normals,
    mesh_close_loop,
]

def register():
    for module in _module_list:
        if hasattr(module, "register"):
            module.register()

def unregister():
    for module in reversed(_module_list):
        if hasattr(module, "unregister"):
            module.unregister()
```

---

## Preferences 访问

```python
from . import __package__ as base_package

def get_pref():
    return bpy.context.preferences.addons[base_package].preferences
```

---

## 错误报告

```python
self.report({'WARNING'}, "...")   # 用户操作不当（如未选中对象）
self.report({'ERROR'}, "...")     # 操作失败
self.report({'INFO'}, "...")      # 操作成功提示

return {'CANCELLED'}              # 与 WARNING/ERROR 搭配
return {'FINISHED'}               # 与 INFO 搭配
```

- 不要用 `{'INFO'}` 搭配 `{'CANCELLED'}`
- 不要用 `{'ERROR'}` 搭配 `{'FINISHED'}`

---

## 文件清理清单

新文件 / 从外部合并文件时，必须检查并移除：

- [ ] 独立 `bl_info = {...}` 块
- [ ] `if __name__ == "__main__":` 块（根 `__init__.py` 中的开发用法除外）
- [ ] 非 `RARA_OT_` / `RARA_PT_` / `RARA_MT_` 前缀的类名
- [ ] 非 `"rara."` 前缀的 `bl_idname`
- [ ] 用 docstring 代替 `bl_description` 的写法
- [ ] `__package__` 直接硬编码用于 preferences 查找
- [ ] 插件目录内保存临时文件（应用 `tempfile.gettempdir()`）
- [ ] 重复定义的函数

---

## 背景模式

根 `__init__.py` 应在 `bpy.app.background` 模式下短路，避免在命令行渲染等场景下注册失败：

```python
if bpy.app.background:
    print(f"{bl_info['name']}_V{bl_info['version']} 后台模式忽略加载")
    def register(): pass
    def unregister(): pass
```

---

## 模块级函数

工具函数应尽量放在 `utils/` 子包中，禁止放在 `ops/` 子包文件模块级别。如必须与 operator 同文件，应放在 operator 类内部作为 `@staticmethod` 或放在 `utils/` 统一管理。
