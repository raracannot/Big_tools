# Big_tools 开发规范

## 一、项目结构

```
Big_tools/
├── __init__.py           # 插件入口 bl_info
├── register_module.py    # 模块注册管理
├── addon_prefs.py        # 插件偏好设置
├── config.py             # 全局配置
├── ops/
│   ├── __init__.py       # 操作符模块汇总
│   ├── _base.py          # BaseModalMixin + ThreePointWidget
│   ├── _callbacks.py     # 公共绘制回调
│   └── *.py              # 各功能操作符
├── ui/
│   ├── __init__.py
│   └── panel.py          # 侧栏面板
├── utils/
│   ├── gpu_utils.py      # GPU 绘制工具
│   ├── math_utils.py     # 数学工具
│   ├── rara_snapper.py   # 统一吸附器
│   ├── snapper.py        # 旧版吸附器
│   └── ray_snapper.py    # 射线吸附器
└── lib/                  # 旧版/测试脚本
```

## 二、操作符模板

### 2.1 简单执行型（无 Modal）

```python
import bpy

class RARA_OT_Example(bpy.types.Operator):
    bl_idname = "rara.example"
    bl_label = "示例工具"
    bl_description = "工具描述\n\n【执行】功能说明"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.mode == 'EDIT_MESH'

    def execute(self, context):
        # 主逻辑
        self.report({'INFO'}, "执行完成")
        return {'FINISHED'}


_classes = [RARA_OT_Example]

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
```

### 2.2 Modal 操作符（有 overlay/交互绘制）

```python
import bpy
import gpu
import mathutils
from ..utils.rara_snapper import RaraSnapper

class RARA_OT_ExampleModal(bpy.types.Operator):
    bl_idname = "rara.example_modal"
    bl_label = "示例模态工具"
    bl_description = "功能描述\n【左键】操作说明 | 【B】快捷功能 | 【ESC】取消退出"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        # 1. 模式检查
        if context.mode != 'EDIT_MESH':
            self.report({'WARNING'}, "请在编辑模式下使用")
            return {'CANCELLED'}

        # 2. 初始化属性
        self.init_region = context.region
        self.obj = context.edit_object
        self._snap_enabled = True
        self.bm = None

        # 3. 初始化吸附器
        self.snapper = RaraSnapper()
        self.snapper.build_cache(context, scope='ALL')

        # 4. 注册绘制回调
        args = (self, context)
        self._handle_3d = bpy.types.SpaceView3D.draw_handler_add(
            draw_callback_3d, args, 'WINDOW', 'POST_VIEW')
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            draw_callback_2d, args, 'WINDOW', 'POST_PIXEL')

        # 5. 注册定时器和模态
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        context.window_manager.modal_handler_add(self)

        # 6. 更新状态栏
        self.update_header(context)
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        """统一状态栏格式：【工具名】状态信息 | 快捷键"""
        snap = "全" if self._snap_enabled else "顶点"
        msg = f"【示例工具】B: 吸附({snap}) | 左键: 操作 | ESC: 取消"
        context.area.header_text_set(msg)
        context.workspace.status_text_set(msg)

    def modal(self, context, event):
        try:
            # ── 定时器驱动重绘 ──
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            # ── 导航事件直通 ──
            nav_events = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
                          'TRACKPADPAN', 'TRACKPADZOOM'}
            if event.type in nav_events:
                return {'PASS_THROUGH'}

            # ── 退出 ──
            if event.type == 'ESC' and event.value == 'PRESS':
                self.finish(context)
                return {'CANCELLED'}

            # ── 吸附开关 (B键) ──
            if event.type == 'B' and event.value == 'PRESS':
                self._snap_enabled = not self._snap_enabled
                self.update_header(context)
                self.report({'INFO'}, f"吸附: {'全吸附' if self._snap_enabled else '仅顶点'}")
                return {'RUNNING_MODAL'}

            # ── 功能快捷键 ──
            # ... 主逻辑

            return {'PASS_THROUGH'}

        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"示例工具出错: {str(e)}")
            return {'CANCELLED'}

    def finish(self, context):
        """清理所有资源"""
        if hasattr(self, '_timer') and self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if hasattr(self, '_handle_3d') and self._handle_3d:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle_3d, 'WINDOW')
            self._handle_3d = None
        if hasattr(self, '_handle_2d') and self._handle_2d:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, 'WINDOW')
            self._handle_2d = None
        context.area.header_text_set(None)
        context.workspace.status_text_set(None)
        context.area.tag_redraw()
```

### 2.3 三点工具（使用 BaseModalMixin）

```python
from ._base import BaseModalMixin

class RARA_OT_ThreePointExample(BaseModalMixin, bpy.types.Operator):
    bl_idname = "rara.three_point_example"
    bl_label = "三点示例"
    bl_description = "【左键】放置控制点 | 【X】删除点 | 【回车】确认 | 【ESC】取消"
    bl_options = {'REGISTER', 'UNDO'}
    expected_mode = 'EDIT_MESH'

    def invoke(self, context, event):
        if context.mode != self.expected_mode:
            self.report({'WARNING'}, f"请在{self.expected_mode}模式下使用")
            return {'CANCELLED'}

        self._init_snapper_widget(context)  # 自动初始化 snapper, widget, timer, handles
        args = (self, context)
        self._add_draw_handler_3d(draw_3d, args)
        self._add_draw_handler_2d(draw_2d, args)
        context.window_manager.modal_handler_add(self)
        self.update_header(context)
        return {'RUNNING_MODAL'}

    def update_header(self, context):
        msg = f"【三点示例】已选点: {len(self.widget.points)}/3 | 左键: 放置 | X: 删除 | ESC: 取消"
        self._set_status(context, msg)

    def modal(self, context, event):
        try:
            if event.type == 'TIMER':
                context.area.tag_redraw()
                return {'PASS_THROUGH'}

            if context.mode != self.expected_mode:
                self.finish(context)
                return {'CANCELLED'}

            # ... 使用 self._handle_mouse_move / self._handle_left_mouse_press 等

            if event.type == 'ESC' and event.value == 'PRESS':
                self.finish(context)
                return {'CANCELLED'}

            return {'PASS_THROUGH'}
        except Exception as e:
            self.finish(context)
            self.report({'ERROR'}, f"三点示例出错: {str(e)}")
            return {'CANCELLED'}

    def finish(self, context):
        self._finish_common(context)
```

`BaseModalMixin` 自动提供：
- `self.snapper` — RaraSnapper 实例
- `self.widget` — ThreePointWidget 控制点系统
- `self._preview_pt` — 吸附预览点
- `self._handles_3d` / `self._handles_2d` — 绘制句柄列表
- `self._timer` — 定时器
- `_handle_left_mouse_press()` — 左键处理
- `_handle_mouse_move()` — 鼠标移动处理
- `_handle_delete_point()` — 删除控制点
- `_finish_common()` — 完整资源清理

## 三、必须遵循的规范

### 3.1 bl_options

```python
bl_options = {'REGISTER', 'UNDO'}  # 所有操作符必须有 UNDO
```

### 3.2 bl_description 格式

```
功能概述（1-2 行）

【快捷键】功能说明 | 【快捷键】功能说明 | ...
```

示例：
```python
bl_description = "三点对齐\n通过三组对应点计算空间变换矩阵\n\n【左键】放置控制点 | 【B】吸附切换 | 【X】清空 | 【回车】确认 | 【ESC】取消"
```

### 3.3 状态栏格式

```
【工具名】核心状态 | 快捷键1: 说明 | 快捷键2: 说明 | ...
```

示例：
```python
"【可视化测量】段数: 3 | B: 吸附(全) | 锁定: X | Ctrl+左键: 拖动点 | T: 标记参考 | ESC: 退出"
```

### 3.4 异常保护

所有 Modal 操作符的 `modal()` 方法必须包裹 `try/except`：

```python
def modal(self, context, event):
    try:
        # ...
    except Exception as e:
        self.finish(context)
        self.report({'ERROR'}, f"工具名出错: {str(e)}")
        return {'CANCELLED'}
```

### 3.5 资源清理

所有 finish/cleanup/cancel 方法必须清理：

```python
def finish(self, context):
    # 清理定时器
    if hasattr(self, '_timer') and self._timer:
        context.window_manager.event_timer_remove(self._timer)
    # 清理绘制句柄
    # ...
    # 清理状态栏
    context.area.header_text_set(None)
    context.workspace.status_text_set(None)
    context.area.tag_redraw()
```

### 3.6 定时器

所有有 2D/3D overlay 绘制的模态工具必须有定时器：

```python
self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
```

modal 中处理：

```python
if event.type == 'TIMER':
    context.area.tag_redraw()
    return {'PASS_THROUGH'}
```

### 3.7 吸附器

统一使用 `RaraSnapper`：

```python
from ..utils.rara_snapper import RaraSnapper

# 初始化
self.snapper = RaraSnapper()
self.snapper.build_cache(context, scope='ALL')  # 所有可见物体
# 或 scope='SELECTED' # 仅选中物体

# 使用（仅顶点）
pt, snap_type = self.snapper.find_nearest(
    context, event,
    snap_vertex=True, snap_half=False, snap_third=False,
    snap_edge=False, snap_face=False
)

# 使用（全吸附）
pt, snap_type = self.snapper.find_nearest(
    context, event,
    snap_vertex=True, snap_half=True, snap_third=True,
    snap_edge=True, snap_face=True
)
```

### 3.8 吸附开关（B 键）

交互式工具推荐提供吸附开关：

```python
self._snap_enabled = True

# modal 中
if event.type == 'B' and event.value == 'PRESS':
    self._snap_enabled = not self._snap_enabled
    self.update_header(context)
    self.report({'INFO'}, f"吸附: {'全吸附' if self._snap_enabled else '仅顶点'}")
    return {'RUNNING_MODAL'}
```

### 3.9 模式检查

invoke 必须检查 context.mode：

```python
def invoke(self, context, event):
    if context.mode != 'EDIT_MESH':
        self.report({'WARNING'}, "请在编辑模式下使用")
        return {'CANCELLED'}
```

### 3.10 导航事件

所有 Modal 必须 PASS_THROUGH 导航事件：

```python
nav_events = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
              'TRACKPADPAN', 'TRACKPADZOOM'}
if event.type in nav_events:
    return {'PASS_THROUGH'}
```

## 四、文件注册

### 4.1 操作符文件末尾

```python
_classes = [RARA_OT_Example1, RARA_OT_Example2]

def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
```

### 4.2 新操作符注册到 ops/__init__.py

```python
from . import new_tool  # 添加 import

_module_list = [
    # ...
    new_tool,  # 添加模块
]
```

### 4.3 UI 面板注册到 ui/panel.py

```python
col.operator("rara.new_tool_id", text="新工具名称")
```

## 五、快捷键约定

| 按键 | 功能 |
|------|------|
| `ESC` | 取消退出（所有交互工具） |
| `B` | 切换吸附模式（全吸附/仅顶点） |
| `X` | 删除/清空（三点工具清控制点） |
| `TAB` | 切换坐标系/模式 |
| `回车` | 确认执行 |
| `Shift+滚轮` | 精细调整参数 |
| `Ctrl+滚轮` | 粗调参数 |
| `Ctrl+Shift+X` | 清空所有 |

## 六、命名约定

| 类型 | 格式 | 示例 |
|------|------|------|
| 操作符类名 | `RARA_OT_Model_FunctionName` | `RARA_OT_Model_VisualAlign` |
| bl_idname | `rara.model_function_name` | `rara.model_visual_align` |
| 属性组类名 | `RARA_Model_FunctionSettings` | `RARA_Model_MeasureSettings` |
| 面板类名 | `RARA_PT_MainPanel` | 固定不变 |
| 清理方法 | `finish()` | 统一用 finish |
| 状态更新 | `update_header()` | 统一用 update_header |
| 绘制回调 | `draw_callback_{name}_3d / 2d` | 或复用 `_callbacks.py` |

## 七、检查清单

新增操作符时请确认：

- [ ] `bl_options` 包含 `'UNDO'`
- [ ] `bl_description` 使用 `【键】说明` 格式
- [ ] invoke 检查 `context.mode`
- [ ] 有 overlay 的 Modal 加了 `event_timer_add`
- [ ] modal 包裹 `try/except`
- [ ] finish 清理 `header_text_set(None)` + `workspace.status_text_set(None)`
- [ ] finish 清理 timer
- [ ] finish 清理 draw handlers
- [ ] 有 `update_header()` 方法，格式为 `【工具名】状态 | 快捷键`
- [ ] 吸附器使用 `RaraSnapper`
- [ ] 注册到 `ops/__init__.py` 的 `_module_list`
- [ ] UI 按钮注册到 `ui/panel.py` 对应模式区域
- [ ] 导航事件 PASS_THROUGH
