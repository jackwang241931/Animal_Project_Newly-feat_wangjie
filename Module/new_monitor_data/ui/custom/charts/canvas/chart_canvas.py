
import matplotlib
from PyQt6.QtCore import Qt
from loguru import logger
from matplotlib.figure import Figure

matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
class ChartCanvas(FigureCanvas):
    """增强的图表画布，支持鼠标交互"""

    def __init__(self, figure: Figure, ax, parent=None):
        super().__init__(figure)
        self.setParent(parent)
        self.ax = ax
        self.figure = figure
        # 连接鼠标事件到图表
        self.mpl_connect('motion_notify_event', self.on_mouse_move)

        # 添加关键：启用鼠标事件
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _format_time_label(self, timestamp: float) -> str:
        """将时间戳格式化为日期时间标签"""
        try:
            if timestamp is None:
                return ""

            timestamp = float(timestamp)

            # 处理负数时间戳
            if timestamp < 0:
                return "Invalid Time"

            from datetime import datetime
            dt = datetime.fromtimestamp(timestamp)

            # # 根据时间范围选择合适的格式
            # now = datetime.now()
            # time_diff = abs((now - dt).total_seconds())
            #
            # if time_diff < 86400:  # 24小时内，只显示时间
            #     return dt.strftime('%H:%M:%S')
            # elif time_diff < 86400 * 30:  # 30天内，显示月-日 时:分
            #     return dt.strftime('%m-%d %H:%M')
            # else:  # 超过30天，显示完整日期时间
            #     return dt.strftime('%Y-%m-%d %H:%M')
            return dt.strftime('%m-%d %H:%M')
        except Exception as e:
            logger.error(f"格式化日期时间标签出错: {e}")
            return str(timestamp)

    def on_mouse_move(self, event):
        """鼠标移动事件 - 显示离鼠标最近的数据点提示"""
        if event is None or event.inaxes is None or event.inaxes != self.ax:
            self.clear_tooltip()
            return

        if event.xdata is None or event.ydata is None:
            self.clear_tooltip()
            return

        try:
            nearest_line = None
            nearest_index = -1
            min_pixel_dist = float('inf')

            # 用像素距离判断是否命中，阈值可以自己调
            hit_threshold = 10

            for line in self.ax.get_lines():
                xdata = line.get_xdata()
                ydata = line.get_ydata()

                if len(xdata) == 0 or len(ydata) == 0:
                    continue

                for i in range(len(xdata)):
                    if xdata[i] is None or ydata[i] is None:
                        continue

                    try:
                        x_val = float(xdata[i])
                        y_val = float(ydata[i])
                    except (TypeError, ValueError):
                        continue

                    # 把数据坐标转换成屏幕坐标，再算鼠标距离
                    px, py = self.ax.transData.transform((x_val, y_val))
                    dist = ((event.x - px) ** 2 + (event.y - py) ** 2) ** 0.5

                    if dist < min_pixel_dist:
                        min_pixel_dist = dist
                        nearest_line = line
                        nearest_index = i

            if nearest_line is not None and nearest_index >= 0 and min_pixel_dist <= hit_threshold:
                label = nearest_line.get_label()
                x_val = nearest_line.get_xdata()[nearest_index]
                y_val = nearest_line.get_ydata()[nearest_index]

                try:
                    y_str = f"{float(y_val):.2f}"
                except (TypeError, ValueError):
                    y_str = str(y_val)

                tooltip_text = f"{label}\nX: {self._format_time_label(x_val)}\nY: {y_str}"

                # 清除旧的文本
                self.clear_tooltip()

                self.ax.text(
                    event.xdata,
                    event.ydata,
                    tooltip_text,
                    fontsize=9,
                    ha='left',
                    va='bottom',
                    bbox=dict(
                        boxstyle='round',
                        facecolor='wheat',
                        alpha=0.8,
                        edgecolor='gray',
                        linewidth=0.5
                    )
                )
                self.draw_idle()
            else:
                self.clear_tooltip()

            # self.clear_tooltip()
        except Exception as e:
            logger.error(f"鼠标交互出错: {e}")
            self.clear_tooltip()

    def clear_tooltip(self):
        """清除提示"""
        try:
            # 只清除文本注释，不是标题和标签
            if self.ax.texts:
                # 保留第一个文本（通常是标题），只删除后面的提示框
                while len(self.ax.texts) > 0:
                    text = self.ax.texts[-1]
                    # 只删除提示框（有bbox的文本）
                    if hasattr(text, 'get_bbox_patch') and text.get_bbox_patch() is not None:
                        text.remove()
                    else:
                        break
            self.draw_idle()
        except Exception as e:
            logger.error(f"清除提示出错: {e}")