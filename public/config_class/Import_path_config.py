
import sys
import os


def setup_project_paths():
    """设置项目路径"""

    # 获取项目根目录
    current_file = os.path.abspath(__file__)
    project_root = os.path.dirname(os.path.dirname(current_file))

    # 要添加的路径
    paths = [
        project_root,  # 项目根目录
        os.path.join(project_root, 'Module'),  # Module目录
    ]

    # 添加路径
    for path in paths:
        if os.path.exists(path) and path not in sys.path:
            sys.path.insert(0, path)

    return paths