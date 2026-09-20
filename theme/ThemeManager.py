from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal, pyqtProperty
from loguru import logger

from public.config_class.global_setting import global_setting

#logger = logger.bind(category="gui_logger")
# 图表样式名称枚举类
class Charts_Style_Name(Enum):
    NORMAL = 'normal'


class ThemeManager(QObject):
    _instance = None
    theme_changed = pyqtSignal()

    # def __new__(cls):
    #     if not cls._instance:
    #         cls._instance = super().__new__(cls)
    #         cls._init_themes(cls._instance)
    #     return cls._instance
    def __init__(self):
        super().__init__()
        self._init_themes(self)

    @pyqtProperty(str)
    def current_theme(self):
        return self._current_theme

    @current_theme.setter
    def current_theme(self, theme_name):
        if theme_name in self._themes:
            self._current_theme = theme_name
            self.theme_changed.emit()

    @classmethod
    def _init_themes(cls, instance):

        # 定义主题配置
        cls._themes = {
            "dark": {
                "--primary": "#283041",
                "--secondary": "#1B2431",
                "--text": "#E1E1E1",
                "--text_disabled": "#969696",
                "--text_hover": "#C3C3C3",
                "--highlight": "#1B2431",
                "--selected": "#000000",
                "--disabled": "#283041",
                "--border": "#555555"
            },
            "light": {
                "--primary": "#F0F0F0",
                "--secondary": "#dcdcdc",
                "--text": "#333333",
                "--text_disabled": "#969696",
                "--text_hover": "#000000",
                "--highlight": "#E1E1E1",
                "--selected": "#D7D7D7",
                "--disabled": "#dcdcdc",
                "--border": "#CCCCCC"
            }
        }
        instance._current_theme = global_setting.get_setting("style")
        logger.info("ThemeManger:instance._current_theme:  " + instance._current_theme)

    # 获取图表样式集
    def get_charts_style(self):
        theme = self._themes[self._current_theme]
        themes = {
            "dark": {
                Charts_Style_Name.NORMAL.value: {
                    'background_color': theme['--secondary'],
                    'chart': {
                        'chart_background_color': theme['--secondary'],
                        'plot_area_color': theme['--secondary'],
                        'title_font_color': theme['--text']
                    },
                    'series': {
                        'series_color': '#E1E1E1'
                    },
                    'axis': {
                        'axis_label_color': '#E1E1E1',
                        'axis_grid_line_color': '#C3C3C3'
                    },
                    'legend': {
                        'legend_font_color': '#E1E1E1'
                    }
                },

            },
            "light": {
                Charts_Style_Name.NORMAL.value: {
                    'background_color': theme['--secondary'],
                    'chart': {
                        'chart_background_color': theme['--secondary'],
                        'plot_area_color': theme['--secondary'],
                        'title_font_color': theme['--text']
                    },
                    'series': {
                        'series_color': '#333333'
                    },
                    'axis': {
                        'axis_label_color': '#333333',
                        'axis_grid_line_color': '#000000'
                    },
                    'legend': {
                        'legend_font_color': '#333333'
                    }
                },
            }
        }
        # 获得背景色的强对比度颜色
        return themes[self._current_theme]
        pass

    def hex_to_rgb(self, hex_color):
        """将16进制颜色转为RGB元组"""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    def rgb_to_hex(self, rgb):
        """将RGB元组转为16进制颜色"""
        return '#{:02x}{:02x}{:02x}'.format(*rgb)

    def luminance(self, rgb):
        """计算颜色的亮度"""
        r, g, b = rgb
        return 0.299 * r + 0.587 * g + 0.114 * b

    # 获得强对比度颜色
    def get_contrast_color(self, colorHex: str = "", color_delta_start: int = -30, color_delta_end: int = 30,
                           color_nums: int = 5):
        """根据亮度计算对比度颜色（黑色或白色）"""
        rgb = self.hex_to_rgb(colorHex)
        contrast_color = '#ffffff' if self.luminance(rgb) < 128 else '#000000'
        return self.get_neighbor_color(colorHex=contrast_color, color_delta_start=color_delta_start,
                                       color_delta_end=color_delta_start + color_delta_end,
                                       color_nums=color_nums) if contrast_color == '#ffffff' else self.get_neighbor_color(
            colorHex=contrast_color, color_delta_start=color_delta_start + color_delta_end,
            color_delta_end=color_delta_end,
            color_nums=color_nums)
        pass

    # 获得邻近渐变颜色
    def get_neighbor_color(self, colorHex: str = "", color_delta_start: int = -30, color_delta_end: int = 30,
                           color_nums: int = 5):
        """生成邻近颜色"""
        rgb = self.hex_to_rgb(colorHex)
        adj_colors = []
        for delta in range(color_delta_start, color_delta_end,
                           (color_delta_end - color_delta_start) // color_nums):  # 微调RGB值
            adj_color = tuple(max(0, min(255, c + delta)) for c in rgb)
            adj_colors.append(self.rgb_to_hex(adj_color))
        return adj_colors
        pass

    # 获取图表style
    def get_button_style(self, isSelected=False):
        theme = self._themes[self._current_theme]
        if isSelected:
            return f"""
                QPushButton{{
                    background-color: {theme['--selected']};
                    color:{theme['--text']};
                   padding:10px;
                    border-radius: 4px;
                    font-size:13px;
                    border-bottom:2px solid {theme['--border']};
                    border-right:2px solid {theme['--border']};
                }}
                QPushButton:hover{{
                    background:{theme['--highlight']};
                    color:{theme['--text_hover']};
                }}
                QPushButton:disabled {{
                    background-color: {theme['--selected']};
                    color:{theme['--text_disabled']};
                }}
                QPushButton:pressed {{
                    background:{theme['--selected']};    
                    color:{theme['--text_hover']};
                }}
            """
        else:
            return f"""
                QPushButton{{
                    background-color: {theme['--primary']};
                    color:{theme['--text']};
                    padding: 10px;
                    border-radius: 4px;
                    font-size:13px;
                    border-bottom:2px solid {theme['--border']};
                    border-right:2px solid {theme['--border']};
                }}
                QPushButton:hover{{
                    background:{theme['--highlight']};
                    color:{theme['--text_hover']};
                }}
                QPushButton:disabled {{
                    background-color: {theme['--primary']};
                    color:{theme['--text_disabled']};
                }}
                QPushButton:pressed {{
                    background:{theme['--selected']};  
                    color:{theme['--text_hover']}  ;
                }}
                    """

    def get_style_sheet(self):
        theme = self._themes[self._current_theme]
        style_sheet = f"""
   
            
            
            QTabBar::tab {{
                background: {theme['--primary']};         /* 标签按钮的背景颜色 */
                padding: 10px;               /* 标签按钮的内边距 */
                margin: 5px;                 /* 标签按钮的外边距 */
                border: 1px solid transparent; /* 标签按钮的边框 */
            }}
           QTabBar::tab:selected {{
                background: {theme['--secondary']};         /* 选中标签的背景颜色 */
                border-bottom-color: {theme['--border']}; /* 选中标签的底边框颜色 */
            }}
             QTabBar::tab:hover {{
                background: {theme['--highlight']};         /* 鼠标悬停时的背景颜色 */
            }}
            QLineEdit {{
                background-color: {theme['--secondary']};
                border: 2px solid {theme['--border']};
                padding: 5px;
            }}
            QGroupBox {{
                border: 2px solid {theme['--border']};
            
            }}
            QMenu {{
                background-color:{theme['--secondary']};
                color:{theme['--text']};
                 border: 1px solid {theme['--border']};
            }}
            QMenu::item:disabled {{
                background-color: {theme['--disabled']};  /* 禁用状态背景颜色 */
               color:{theme['--text_disabled']};                  /* 禁用状态文字颜色 */
            }}
            QMenuBar {{
                background-color: {theme['--secondary']};
                color: {theme['--text']};
                border-bottom: 1px solid {theme['--border']};
            }}
             QMenuBar::item {{
                background-color:{theme['--secondary']};
                padding: 5px;
                
            }}
            QMenuBar::item:hover {{
               background-color: {theme['--highlight']};
                 color: {theme['--text_hover']};
                
            }}
            QMenuBar::item:selected {{
                background-color: {theme['--secondary']};
                color: {theme['--text_hover']};
            }}
            QToolBar {{
                font-size: 17px;
                padding: 10px;
                background-color: {theme['--primary']};
                color: {theme['--text']};
                border: 2px solid {theme['--border']};
            }}
            QToolButton {{
                padding: 5px;
                background-color:{theme['--primary']};
            }}
            QToolButton:hover {{
                background-color: {theme['--highlight']};
                 color: {theme['--text_hover']};
            }}
            QToolButton:disabled {{
                 color: {theme['--text_disabled']};
            }}
            QTreeView {{
                background-color: {theme['--primary']};  /* 背景色 */
                border: 1px solid {theme['--border']};      /* 边框颜色 */
                font-size: 14px;              /* 字体大小 */
            }}
            
            QTreeView::item {{
      
                padding: 10px;                /* 内边距 */
            }}
            
            QTreeView::item:selected {{

                background-color: {theme['--secondary']};     /* 选中项的背景色 */
                color:{theme['--text_hover']};                  /* 选中项的文本颜色 */
            }}
            
            QListView::item {{
                padding: 10px;               /* 内边距 */
            }}
            
            QDockWidget {{
                background-color: {theme['--primary']};
                border: 1px solid {theme['--border']};
            }}
          
    
        """ + self.get_button_style(isSelected=False)
        # logger_diy.log.info("ThemeManager的get_style_sheet：" + style_sheet)
        return style_sheet

    # 返回相应主题的颜色值 并都转成16进制颜色
    def get_themes_color(self, mode=0):
        # mode=0是直接返回颜色rgb字符串 mode=1 返回rgb数值的列表[r,g,b]
        if mode == 0:
            return self._themes[self._current_theme]
        else:
            themes = self._themes[self._current_theme]
            for key in themes:
                old_values = themes[key]
                new_values = self.from_rgb_to_16x(old_values)
                themes[key] = new_values
                pass
            return themes

    def get_rgb_numbers(self, rgb_str: str = "rgb(0,0,0)"):

        rgb_str_new = rgb_str.replace(" ", "")
        # 如果是16进制颜色str值则不处理
        if rgb_str_new[0] == "#":
            return rgb_str
        # 提取核心部分：去掉前4字符（rgb(）和末尾的 )
        content = rgb_str_new[4:-1]
        # 分割并转换
        r, g, b = map(int, content.split(','))
        return [r, g, b]

    # 将rgb转换成16进制
    def from_rgb_to_16x(self, rgb_str: str = "rgb(0,0,0)"):
        rgb_str_new = rgb_str.replace(" ", "")
        # 如果是16进制颜色str值则不处理
        if rgb_str_new[0] == "#":
            return rgb_str
        rgb_list = self.get_rgb_numbers(rgb_str_new)
        # 确保 RGB 值在 0-255 范围内
        r = max(0, min(rgb_list[0], 255))
        g = max(0, min(rgb_list[1], 255))
        b = max(0, min(rgb_list[2], 255))
        # 转换为两位十六进制并拼接（参考网页1[1](@ref)、网页6[6](@ref)）
        return "#{:02X}{:02X}{:02X}".format(r, g, b)
