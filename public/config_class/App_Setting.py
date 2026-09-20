import json
from pathlib import Path

from loguru import logger

from public.component.Guide_tutorial_interface.Tutorial_Manager import TutorialManager
from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import Tutorial_Type


class AppSettings:
    """应用程序设置管理器"""

    def __init__(self):
        # 获取应用程序数据目录
        self.app_data_dir = Path.home() / f".{global_setting.get_setting('configer').get('basic').get('name')}" / "app_setting"
        self.settings_file = self.app_data_dir / "settings.json"
        self.ensure_data_dir()
        self.settings = self.load_settings()

    def ensure_data_dir(self):
        """确保数据目录存在"""
        self.app_data_dir.mkdir(parents=True,exist_ok=True)

    def load_settings(self):
        """加载设置"""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载app_settings设置失败: {e}，返回默认配置")

        # 返回默认设置
        return {
            "first_run": True,
            "tutorial_completed": False,
        }

    def save_settings(self):
        """保存设置"""
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存设置失败: {e}|{self.settings}")

    def is_first_run(self):
        """检查是否是第一次运行"""
        return self.settings.get("first_run", True)

    def mark_first_run_completed(self):
        """标记首次运行已完成"""
        self.settings["first_run"] = False
        self.save_settings()
    def reback_first_visit(self,page_name):
        self.settings[f"first_visit_{page_name}"] = True
        self.save_settings()
    def is_first_visit(self, page_name):
        """检查是否是第一次访问指定页面"""
        return self.settings.get(f"first_visit_{page_name}", True)

    def mark_page_visited(self, page_name):
        """标记页面已访问"""
        self.settings[f"first_visit_{page_name}"] = False
        self.save_settings()

    def set_tutorial_completed(self, completed=True):
        """设置教程完成状态"""
        self.settings["tutorial_completed"] = completed
        self.save_settings()

    def is_tutorial_completed(self):
        """检查教程是否已完成"""
        return self.settings.get("tutorial_completed", False)

    def get_guide_type(self):
        """获取引导类型"""
        return self.settings.get("guide_type", Tutorial_Type.ARROW_GUIDE)

    def set_guide_type(self, guide_type):
        """设置引导类型"""
        self.settings["guide_type"] = guide_type
        self.save_settings()

    def get_all_visited_pages(self):
        """获取所有已访问的页面列表"""
        visited_pages = []
        for key in self.settings.keys():
            if key.startswith("first_visit_") and not self.settings[key]:
                page_name = key.replace("first_visit_", "")
                visited_pages.append(page_name)
        return visited_pages

    def reset_page_visit_status(self, page_name):
        """重置指定页面的访问状态"""
        self.settings[f"first_visit_{page_name}"] = True
        self.save_settings()

    def reset_all_page_visit_status(self):
        """重置所有页面的访问状态"""
        for key in list(self.settings.keys()):
            if key.startswith("first_visit_"):
                self.settings[key] = True
        self.save_settings()

    def get_page_visit_count(self):
        """获取已访问页面的数量"""
        count = 0
        for key in self.settings.keys():
            if key.startswith("first_visit_") and not self.settings[key]:
                count += 1
        return count