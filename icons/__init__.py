import bpy
import bpy.utils.previews
from pathlib import Path

previews_icons = {}

pcoll_name = "big_mesh_tools"
FALLBACK_NAME = "NOT_FOUND"


def get_icon(name=None):
    pcoll = previews_icons.get(pcoll_name)
    if pcoll is None:
        return 0
    if not name or name not in pcoll:
        name = FALLBACK_NAME
    return pcoll[name].icon_id

def load_icons():
    global previews_icons
    if pcoll_name in previews_icons:
        unload_icons()  # 清空之前的图标数据

    previews_icons[pcoll_name] = bpy.utils.previews.new()  # 用于存所有的缩略图
    folder_path = Path(__file__).parent #/ 'icons'  # 修改这里，指向 icons 文件夹

    # 检查 icons 文件夹是否存在
    if not folder_path.exists():
        print(f"Icons folder not found at path: {folder_path}")
        return

    # 遍历目录中的所有文件
    for file_path in folder_path.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in ['.png', '.jpg']:
            previews_icons[pcoll_name].load(
                file_path.stem,  # 使用文件名（无后缀）作为键名
                str(file_path),  # 转换为字符串路径以供 `load` 使用
                'IMAGE')

    # 确保兜底图标存在（文件名固定为 NOT_FOUND.png）
    if FALLBACK_NAME not in previews_icons[pcoll_name]:
        fallback_path = folder_path / (FALLBACK_NAME + '.png')
        if fallback_path.exists():
            previews_icons[pcoll_name].load(FALLBACK_NAME, str(fallback_path), 'IMAGE')

def unload_icons():
    global previews_icons
    for pcoll in previews_icons.values():
        bpy.utils.previews.remove(pcoll)
    previews_icons.clear()  

#def register():
#    load_icons()

#def unregister():
#    unload_icons()


# how to use 
# from ..icons import get_icon
# row.operator("rara.model_circle_edges", text="圆化边线",icon_value=get_icon("circle_edges"))


