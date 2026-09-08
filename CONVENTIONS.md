# Big_tools 开发规范

---

## 规则分级

- **【必须】**（强制条例）：任何情况下不得违反，违反即视为 BUG。
- **【应当】**（建议条例）：默认遵循；确有合理性理由（如该规则不适用）允许违反，但应在代码注释或 commit message 中说明理由。
- 未标注分级的既有章节视为基础约定（同强制执行）。

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

---

# 强制条例

## 模态与绘制安全

所有模态算子（Modal Operator）与 GPU 绘制回调（draw handler / Gizmo）代码，**【必须】使用 `try/except Exception` 包裹核心逻辑**，并准备安全退出（cleanup）路径。模态执行或绘制过程中任何未捕获异常，**【必须】**触发安全退出，确保 draw handler / timer / BMesh 备份等资源被完整回收，禁止因异常残留僵尸 handler 或卡在编辑模式。

### 模态类模板（配合 `utils/base_mixin.BaseModalMixin`）

```python
class RARA_OT_Model_Xxx(BaseModalMixin, bpy.types.Operator):
    bl_idname = "rara.model_xxx"
    bl_label = "标签"
    bl_description = "描述"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        try:
            self._init_snapper_widget(context)      # 若需要
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        except Exception:
            self._cleanup()
            return {'CANCELLED'}

    def modal(self, context, event):
        try:
            if event.type in {'ESC', 'RIGHTMOUSE'}:
                self.finish(context)
                return {'CANCELLED'}
            # 核心事件处理...
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, str(e))
            self.finish(context)
            return {'CANCELLED'}

    def finish(self, context):
        self._finish_common(context)   # BaseModalMixin：移除 handler/timer/widget 等
```

### 绘制回调（draw handler）

```python
def draw_callback():
    try:
        shader.bind()
        batch.draw(shader)
    except ReferenceError:
        pass  # 目标对象可能已被删除/GC
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        # 【必须】恢复 GPU 默认状态
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.line_width_set(1.0)
        gpu.state.point_size_set(1.0)
```

### 关键规则

- `finally` 中恢复 GPU 状态（blend、depth_test、line_width、point_size），不可省略
- 捕获 `ReferenceError`（对象被回收 / 被删除），安全跳过
- `_cleanup()` / `finish()` 中的每个资源移除操作自身也要防御：`if self._xxx:` + `try/except`
- `invoke` 中任何初始化失败也**【必须】**调用 cleanup，避免部分资源泄漏

现有参考：
- `ops/mesh/mesh_bridge_loop.py` — modal 内 try/except，`finish()` 统一移除 handler/timer/bmesh
- `ops/mesh/mesh_three_point_extend.py` / `mesh_obj_mirror.py` — `BaseModalMixin` 用法
- `utils/gpu_utils.py`、`utils/draw_callbacks.py` — 绘制封装集中管理

---

## 中文文件编码安全

插件源码含大量中文（标签、描述、注释）。所有 `.py` 文件保持 **UTF-8 无 BOM**，修改时遵守：

- **【必须】**优先用 Edit 工具修改含中文的文件，避免编码损坏
- **【必须】**禁止用 PowerShell `Get-Content` / `Set-Content` 往返修改中文文件——PowerShell 5.1 按 GBK 控制台代码页解码，易把中文损坏为不可逆 `U+FFFD`；`Set-Content -Encoding UTF8` 还会写入 BOM
- 若必须命令行替换，用 Python 脚本：

```python
p = r"path/to/file.py"
s = open(p, encoding="utf-8").read()
s = s.replace("旧字符串", "新字符串")
open(p, "w", encoding="utf-8", newline="\n").write(s)
```

### 编码判断（避免误判）

- 控制台打印中文乱码（`��`）≠ 文件损坏——可能是 GBK 控制台显示问题
- 判断是否损坏，检查替换字符 `U+FFFD`：

```bash
python -c "s=open(p, encoding='utf-8-sig').read(); print('\ufffd' in s)"   # False=未损坏
```

- 验证中文完整用包含判断而非打印：`python -c "s=open(p, encoding='utf-8').read(); print('清理颜色' in s)"`
- BOM 检测：`open(p, 'rb').read(3) == b'\xef\xbb\xbf'`

---

# 建议条例

## Preferences 统一访问

【应当】在 `utils/__init__.py` 提供统一 `get_pref()`，子包一律通过它取 prefs，禁止在子包内用 `__package__` 拼根包名硬查：

```python
# utils/__init__.py
import bpy

def get_pref():
    return bpy.context.preferences.addons[__package__].preferences
```

```python
# 子模块（ops/mesh/...）中
from ...utils import get_pref

prefs = get_pref()
# 取布尔开关务必带兜底，防旧 prefs 缺属性崩溃
if not getattr(prefs, "show_xxx", True):
    return
```

## UI 开关模式

在 `addon_prefs.py` 添加布尔属性，Panel/Menu/Header 中检查（用 `getattr(prefs, "xxx", True)` 兜底）：

```python
# addon_prefs.py
show_xxx: bpy.props.BoolProperty(name="显示XXX", description="...", default=True)
```

```python
# Panel draw 中
def draw(self, context):
    prefs = get_pref()
    if not getattr(prefs, "show_xxx", True):
        return
    self.layout.operator("rara.model_xxx")
```

## 快捷键 + 菜单/按钮双入口 — invoke/execute fallback

当算子既有快捷键（invoke 会拿到鼠标坐标）又有按钮/菜单入口时，菜单触发不会有有效 3D 视口坐标，`event.mouse_region_x/y` 不可信。

**规则**：invoke 只做“尽力设置参数”，**永不返回 `CANCELLED`**，始终坠落 `execute`；execute 检查参数缺失时走 fallback（如读取当前选中元素）。

```python
def invoke(self, context, event):
    # 尽力从快捷键/鼠标上下文取参数…
    return self.execute(context)   # 永不 CANCELLED

def execute(self, context):
    # 参数缺失时 fallback 到当前选中
    if self.edge_a is None:
        sel = [e for e in bm.edges if e.select]
        ...
    return {'FINISHED'}
```

> 仅纯按钮触发 + `invoke_props_dialog` 的算子不受此限（无鼠标坐标依赖）。
> 不要在 invoke 中 `report(WARNING) + return CANCELLED`，会阻断按钮/菜单入口。

## 网格不可见元素的忽略

所有网格操作（循环选择、拓扑遍历、重采样、焊合、法向分析等），**【应当】**自动忽略不可见元素，把隐藏元素视为屏障：

```python
for edge in bm.edges:
    if edge.hide:
        continue   # 视作屏障，断开遍历/选择
    ...
```

- `bm.xxx.hide` 是元素级隐藏（H 键），`mesh.vertices[i].hide` 与之对应
- 结果因隐藏受限时用 `report({'INFO'})` 提示用户
- 我们多数拓扑工具已过滤 `not e.hide`，保持统一

## GPU 绘制的 X-Ray 深度检测自适应

3D 空间（`POST_VIEW`）绘制**【应当】**随视口 X-Ray 状态切换深度检测：

```python
def draw(context):
    is_xray = bool(context.space_data and
                   getattr(context.space_data.shading, 'show_xray', False))
    try:
        gpu.state.depth_test_set('NONE' if is_xray else 'LESS_EQUAL')
        shader.bind()
        batch.draw(shader)
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        gpu.state.depth_test_set('LESS_EQUAL')
```

- 只影响 3D 绘制；2D 覆盖层（`POST_PIXEL`）不受影响
- `finally` 恢复 `LESS_EQUAL`
- 用 `hasattr(...shading, 'show_xray')` 防御（渲染视图等上下文无此属性）

## 性能优化建议表

新功能存在显著性能优化空间时，**【应当】**先评估并向用户提出建议，由用户决定是否采用；不得擅自引入重型依赖或晦涩优化。

| 优化手段 | 适用场景 | 注意事项 |
|---|---|---|
| NumPy 向量化（广播、`np.linalg`、`bincount`） | 批量顶点/边/面运算 | Blender 自带；适合遍历上万元素 |
| KDTree（`mathutils.kdtree`） | 近邻/焊接/投影 | Blender 内置；点云查询 O(n log n) |
| BVHTree（`mathutils.bvhtree`） | 射线/面碰撞 | Blender 内置；面级空间查询 |
| `foreach_get` / `foreach_set` | 批量属性读写 | 比逐元素快 10~100 倍；纯性能无副作用 |

决策流程：
1. 识别瓶颈（遍历级复杂度/规模）；
2. 给出保守方案与优化方案对比；
3. 由用户选择是否引入优化，代码注释注明两方案。

额外规则：
- 差异达 10 倍以上且优化方案为 Blender 内置（KDTree/BVHTree/numpy/foreach）→ 可直接采用，无需确认
- `foreach_get/set` 无副作用，可自由使用
- 禁止引入额外依赖（`scipy`、`numba` 等）
- KDTree/BVHTree 有构建成本，元素量少时不划算

现有参考：
- `utils/math_utils.py` `resample_polyline_np`（NumPy 重采样/样条）
- `ops/mesh/mesh_resample_chain.py`、`mesh_scale_arc.py`、`mesh_straighten_edges.py`（批量坐标运算）
- `note/lib_archive/...`（参考实现，勿直接 import）

