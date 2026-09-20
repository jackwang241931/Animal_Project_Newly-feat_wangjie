# import re
# import pandas as pd
# import matplotlib.pyplot as plt
# import seaborn as sns
# from datetime import datetime, timedelta
# import numpy as np
# from collections import Counter
# import matplotlib.dates as mdates
# from matplotlib.patches import Rectangle
#
# # 设置中文字体
# plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
# plt.rcParams['axes.unicode_minus'] = False
#
#
# class AdvancedModbusLogAnalyzer:
#     def __init__(self, log_file_path):
#         self.log_file_path = log_file_path
#         self.data = []
#         self.df = None
#
#     def parse_modbus_frame(self, hex_data):
#         """解析Modbus帧结构"""
#         if len(hex_data) < 4:
#             return {}
#
#         # 基本结构：设备地址(1字节) + 功能码(1字节) + 数据 + CRC(2字节)
#         device_addr = hex_data[:2]
#         func_code = hex_data[2:4]
#
#         result = {
#             'device_addr': device_addr,
#             'func_code': func_code,
#             'frame_type': f"{device_addr}{func_code}"
#         }
#
#         # 根据功能码解析具体内容
#         if func_code == '03':  # 读保持寄存器
#             if len(hex_data) >= 12:
#                 start_addr = hex_data[4:8]
#                 reg_count = hex_data[8:12]
#                 result.update({
#                     'start_addr': start_addr,
#                     'reg_count': reg_count,
#                     'operation': '读保持寄存器'
#                 })
#         elif func_code == '04':  # 读输入寄存器
#             if len(hex_data) >= 12:
#                 start_addr = hex_data[4:8]
#                 reg_count = hex_data[8:12]
#                 result.update({
#                     'start_addr': start_addr,
#                     'reg_count': reg_count,
#                     'operation': '读输入寄存器'
#                 })
#         elif func_code == '01':  # 读线圈状态
#             if len(hex_data) >= 12:
#                 start_addr = hex_data[4:8]
#                 coil_count = hex_data[8:12]
#                 result.update({
#                     'start_addr': start_addr,
#                     'coil_count': coil_count,
#                     'operation': '读线圈状态'
#                 })
#         elif func_code == '05':  # 写单个线圈
#             if len(hex_data) >= 12:
#                 coil_addr = hex_data[4:8]
#                 coil_value = hex_data[8:12]
#                 result.update({
#                     'coil_addr': coil_addr,
#                     'coil_value': coil_value,
#                     'operation': '写单个线圈'
#                 })
#         elif func_code == '06':  # 写单个寄存器
#             if len(hex_data) >= 12:
#                 reg_addr = hex_data[4:8]
#                 reg_value = hex_data[8:12]
#                 result.update({
#                     'reg_addr': reg_addr,
#                     'reg_value': reg_value,
#                     'operation': '写单个寄存器'
#                 })
#
#         return result
#
#     def parse_log_file(self):
#         """解析日志文件"""
#         pattern = r'行 (\d+): (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| ERROR \| p_monitor_data \| New_Mod_Bus:_log_error:\d+ \| (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)-(COM\d+)-请求报文([0-9a-fA-F]+)-响应报文([0-9a-fA-F]+)-Time OUT(\d+)-数据错误，CRC验证失败'
#
#         try:
#             with open(self.log_file_path, 'r', encoding='utf-8') as file:
#                 content = file.read()
#         except:
#             try:
#                 with open(self.log_file_path, 'r', encoding='gbk') as file:
#                     content = file.read()
#             except:
#                 print("无法读取文件，请检查文件路径和编码")
#                 return None
#
#         matches = re.findall(pattern, content)
#
#         for match in matches:
#             line_num, log_time, detail_time, com_port, request, response, timeout = match
#
#             # 解析时间
#             log_datetime = datetime.strptime(log_time, '%Y-%m-%d %H:%M:%S')
#             detail_datetime = datetime.strptime(detail_time.split('.')[0], '%Y-%m-%d %H:%M:%S')
#
#             # 解析Modbus帧
#             request_info = self.parse_modbus_frame(request)
#             response_info = self.parse_modbus_frame(response)
#
#             # 创建更详细的时间维度
#             data_entry = {
#                 'line_number': int(line_num),
#                 'log_time': log_datetime,
#                 'detail_time': detail_datetime,
#                 'com_port': com_port,
#                 'request_hex': request,
#                 'response_hex': response,
#                 'timeout': int(timeout),
#                 'request_length': len(request),
#                 'response_length': len(response),
#
#                 # 时间维度
#                 'hour': log_datetime.hour,
#                 'minute': log_datetime.minute,
#                 'date': log_datetime.date(),
#                 'weekday': log_datetime.weekday(),
#                 'time_period': self.get_time_period(log_datetime.hour),
#                 'minute_in_hour': log_datetime.minute,
#                 'second': log_datetime.second,
#
#                 # Modbus解析信息
#                 'device_addr': request_info.get('device_addr', ''),
#                 'func_code': request_info.get('func_code', ''),
#                 'frame_type': request_info.get('frame_type', ''),
#                 'operation': request_info.get('operation', '未知操作'),
#                 'start_addr': request_info.get('start_addr', ''),
#                 'reg_count': request_info.get('reg_count', ''),
#
#                 # 响应信息
#                 'resp_device_addr': response_info.get('device_addr', ''),
#                 'resp_func_code': response_info.get('func_code', ''),
#                 'resp_frame_type': response_info.get('frame_type', ''),
#             }
#
#             self.data.append(data_entry)
#
#         self.df = pd.DataFrame(self.data)
#         print(f"成功解析 {len(self.data)} 条错误记录")
#         return self.df
#
#     def get_time_period(self, hour):
#         """将小时转换为时间段"""
#         if 6 <= hour < 12:
#             return '上午'
#         elif 12 <= hour < 18:
#             return '下午'
#         elif 18 <= hour < 24:
#             return '晚上'
#         else:
#             return '深夜'
#
#     def analyze_detailed_time_patterns(self):
#         """详细时间模式分析"""
#         fig = plt.figure(figsize=(24, 20))
#
#         # 1. 按分钟级别的时间分布（热力图）
#         plt.subplot(4, 4, 1)
#         # 创建小时-分钟的热力图
#         hour_minute_data = self.df.groupby(['hour', 'minute_in_hour']).size().unstack(fill_value=0)
#         if not hour_minute_data.empty:
#             sns.heatmap(hour_minute_data, cmap='YlOrRd', cbar_kws={'label': '错误次数'})
#             plt.title('错误分布热力图（小时-分钟）', fontsize=12)
#             plt.xlabel('分钟')
#             plt.ylabel('小时')
#
#         # 2. 按时间段统计
#         plt.subplot(4, 4, 2)
#         period_counts = self.df['time_period'].value_counts()
#         colors = ['lightblue', 'lightgreen', 'orange', 'lightcoral']
#         bars = plt.bar(period_counts.index, period_counts.values, color=colors)
#         plt.title('时间段错误分布', fontsize=12)
#         plt.ylabel('错误次数')
#         for bar in bars:
#             height = bar.get_height()
#             plt.text(bar.get_x() + bar.get_width() / 2., height,
#                      f'{int(height)}', ha='center', va='bottom')
#
#         # 3. 星期分布
#         plt.subplot(4, 4, 3)
#         weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
#         weekday_counts = self.df['weekday'].value_counts().sort_index()
#         weekday_labels = [weekday_names[i] for i in weekday_counts.index]
#         plt.bar(weekday_labels, weekday_counts.values, color='lightsteelblue')
#         plt.title('星期分布', fontsize=12)
#         plt.ylabel('错误次数')
#         plt.xticks(rotation=45)
#
#         # 4. 每小时内的分钟分布
#         plt.subplot(4, 4, 4)
#         minute_counts = self.df['minute_in_hour'].value_counts().sort_index()
#         plt.plot(minute_counts.index, minute_counts.values, marker='o', linewidth=2)
#         plt.title('每小时内分钟分布', fontsize=12)
#         plt.xlabel('分钟')
#         plt.ylabel('错误次数')
#         plt.grid(alpha=0.3)
#
#         # 5. 设备地址分布
#         plt.subplot(4, 4, 5)
#         device_counts = self.df['device_addr'].value_counts().head(10)
#         plt.bar(device_counts.index, device_counts.values, color='lightcyan')
#         plt.title('设备地址错误分布（Top10）', fontsize=12)
#         plt.xlabel('设备地址')
#         plt.ylabel('错误次数')
#         plt.xticks(rotation=45)
#
#         # 6. 功能码详细对比
#         plt.subplot(4, 4, 6)
#         func_codes = self.df['func_code'].value_counts()
#         colors_func = plt.cm.Set3(np.linspace(0, 1, len(func_codes)))
#         wedges, texts, autotexts = plt.pie(func_codes.values, labels=[f'功能码{code}' for code in func_codes.index],
#                                            autopct='%1.1f%%', colors=colors_func)
#         plt.title('功能码分布详情', fontsize=12)
#
#         # 7. 帧类型分布（设备地址+功能码）
#         plt.subplot(4, 4, 7)
#         frame_type_counts = self.df['frame_type'].value_counts().head(10)
#         plt.barh(range(len(frame_type_counts)), frame_type_counts.values, color='lightpink')
#         plt.yticks(range(len(frame_type_counts)), frame_type_counts.index)
#         plt.title('帧类型分布（Top10）', fontsize=12)
#         plt.xlabel('错误次数')
#
#         # 8. 操作类型分布
#         plt.subplot(4, 4, 8)
#         operation_counts = self.df['operation'].value_counts()
#         plt.bar(range(len(operation_counts)), operation_counts.values, color='lightyellow')
#         plt.xticks(range(len(operation_counts)), operation_counts.index, rotation=45, ha='right')
#         plt.title('操作类型分布', fontsize=12)
#         plt.ylabel('错误次数')
#
#         # 9. 功能码按时间分布
#         plt.subplot(4, 4, 9)
#         func_time_pivot = self.df.pivot_table(
#             index='hour',
#             columns='func_code',
#             values='line_number',
#             aggfunc='count',
#             fill_value=0
#         )
#         func_time_pivot.plot(kind='area', stacked=True, alpha=0.7, ax=plt.gca())
#         plt.title('功能码时间分布（堆叠图）', fontsize=12)
#         plt.xlabel('小时')
#         plt.ylabel('错误次数')
#         plt.legend(title='功能码', bbox_to_anchor=(1.05, 1), loc='upper left')
#
#         # 10. COM端口 vs 功能码热力图
#         plt.subplot(4, 4, 10)
#         com_func_pivot = self.df.pivot_table(
#             index='com_port',
#             columns='func_code',
#             values='line_number',
#             aggfunc='count',
#             fill_value=0
#         )
#         sns.heatmap(com_func_pivot, annot=True, fmt='d', cmap='Blues')
#         plt.title('COM端口 vs 功能码', fontsize=12)
#
#         # 11. 寄存器地址分析（对于03和04功能码）
#         plt.subplot(4, 4, 11)
#         reg_data = self.df[self.df['func_code'].isin(['03', '04']) & (self.df['start_addr'] != '')]
#         if not reg_data.empty:
#             start_addr_counts = reg_data['start_addr'].value_counts().head(10)
#             plt.bar(start_addr_counts.index, start_addr_counts.values, color='lightgreen')
#             plt.title('寄存器起始地址分布', fontsize=12)
#             plt.xlabel('起始地址')
#             plt.ylabel('错误次数')
#             plt.xticks(rotation=45)
#         else:
#             plt.text(0.5, 0.5, '无寄存器地址数据', ha='center', va='center', transform=plt.gca().transAxes)
#             plt.title('寄存器起始地址分布', fontsize=12)
#
#         # 12. 超时次数 vs 功能码
#         plt.subplot(4, 4, 12)
#         timeout_func_pivot = self.df.pivot_table(
#             index='timeout',
#             columns='func_code',
#             values='line_number',
#             aggfunc='count',
#             fill_value=0
#         )
#         timeout_func_pivot.plot(kind='bar', ax=plt.gca())
#         plt.title('超时次数 vs 功能码', fontsize=12)
#         plt.xlabel('超时次数')
#         plt.ylabel('错误次数')
#         plt.legend(title='功能码')
#         plt.xticks(rotation=0)
#
#         # 13. 响应长度分布
#         plt.subplot(4, 4, 13)
#         plt.hist(self.df['response_length'], bins=20, color='lightblue', alpha=0.7, edgecolor='black')
#         plt.title('响应数据长度分布', fontsize=12)
#         plt.xlabel('响应长度（字符）')
#         plt.ylabel('频次')
#         plt.grid(axis='y', alpha=0.3)
#
#         # 14. 错误间隔时间分析
#         plt.subplot(4, 4, 14)
#         if len(self.df) > 1:
#             time_diffs = self.df['log_time'].diff().dropna()
#             time_diffs_seconds = [td.total_seconds() for td in time_diffs if pd.notna(td)]
#             if time_diffs_seconds:
#                 plt.hist(time_diffs_seconds, bins=20, color='orange', alpha=0.7, edgecolor='black')
#                 plt.title('错误间隔时间分布', fontsize=12)
#                 plt.xlabel('间隔时间（秒）')
#                 plt.ylabel('频次')
#                 plt.grid(axis='y', alpha=0.3)
#
#         # 15. 设备地址 vs 功能码矩阵
#         plt.subplot(4, 4, 15)
#         if not self.df['device_addr'].empty:
#             device_func_pivot = self.df.pivot_table(
#                 index='device_addr',
#                 columns='func_code',
#                 values='line_number',
#                 aggfunc='count',
#                 fill_value=0
#             )
#             sns.heatmap(device_func_pivot, annot=True, fmt='d', cmap='Greens', cbar_kws={'label': '错误次数'})
#             plt.title('设备地址 vs 功能码矩阵', fontsize=12)
#
#         # 16. 每日错误趋势详情
#         plt.subplot(4, 4, 16)
#         daily_detailed = self.df.groupby(['date', 'func_code']).size().unstack(fill_value=0)
#         daily_detailed.plot(kind='line', marker='o', ax=plt.gca())
#         plt.title('每日错误趋势（按功能码）', fontsize=12)
#         plt.xlabel('日期')
#         plt.ylabel('错误次数')
#         plt.legend(title='功能码', bbox_to_anchor=(1.05, 1), loc='upper left')
#         plt.xticks(rotation=45)
#
#         plt.tight_layout()
#         plt.show()
#
#     def analyze_message_types(self):
#         """具体报文类型分析"""
#         print("\n" + "=" * 80)
#         print("报文类型详细分析")
#         print("=" * 80)
#
#         # 统计各种报文类型
#         frame_types = self.df['frame_type'].value_counts()
#
#         print(f"\n1. 报文类型统计（设备地址+功能码）:")
#         for frame_type, count in frame_types.head(15).items():
#             device_addr = frame_type[:2] if len(frame_type) >= 2 else ''
#             func_code = frame_type[2:4] if len(frame_type) >= 4 else ''
#             percentage = count / len(self.df) * 100
#
#             # 解释功能码
#             func_desc = self.get_function_code_description(func_code)
#
#             print(f"   {frame_type} (设备{device_addr}-{func_desc}): {count}次 ({percentage:.1f}%)")
#
#         # 功能码模块分析
#         print(f"\n2. 功能码模块分析:")
#         func_modules = {
#             '01': '离散输入模块',
#             '02': '离散输出模块',
#             '03': '保持寄存器模块',
#             '04': '输入寄存器模块',
#             '05': '单线圈写入模块',
#             '06': '单寄存器写入模块',
#             '15': '多线圈写入模块',
#             '16': '多寄存器写入模块'
#         }
#
#         func_counts = self.df['func_code'].value_counts()
#         for func_code, count in func_counts.items():
#             module_name = func_modules.get(func_code, f'未知模块({func_code})')
#             percentage = count / len(self.df) * 100
#             print(f"   {func_code} - {module_name}: {count}次 ({percentage:.1f}%)")
#
#         # 设备分析
#         print(f"\n3. 设备地址分析:")
#         device_counts = self.df['device_addr'].value_counts()
#         for device_addr, count in device_counts.head(10).items():
#             percentage = count / len(self.df) * 100
#             print(f"   设备{device_addr}: {count}次 ({percentage:.1f}%)")
#
#         # 寄存器地址分析
#         print(f"\n4. 寄存器地址分析（03/04功能码）:")
#         reg_data = self.df[self.df['func_code'].isin(['03', '04']) & (self.df['start_addr'] != '')]
#         if not reg_data.empty:
#             start_addr_counts = reg_data['start_addr'].value_counts()
#             for addr, count in start_addr_counts.head(10).items():
#                 percentage = count / len(reg_data) * 100
#                 print(f"   起始地址{addr}: {count}次 ({percentage:.1f}%)")
#
#         # 操作类型分析
#         print(f"\n5. 操作类型分析:")
#         operation_counts = self.df['operation'].value_counts()
#         for operation, count in operation_counts.items():
#             percentage = count / len(self.df) * 100
#             print(f"   {operation}: {count}次 ({percentage:.1f}%)")
#
#     def get_function_code_description(self, func_code):
#         """获取功能码描述"""
#         descriptions = {
#             '01': '读线圈状态',
#             '02': '读离散输入',
#             '03': '读保持寄存器',
#             '04': '读输入寄存器',
#             '05': '写单个线圈',
#             '06': '写单个寄存器',
#             '15': '写多个线圈',
#             '16': '写多个寄存器'
#         }
#         return descriptions.get(func_code, f'未知功能码({func_code})')
#
#     def analyze_time_correlation(self):
#         """时间相关性分析"""
#         print(f"\n6. 时间相关性分析:")
#
#         # 错误集中时间段
#         hour_counts = self.df['hour'].value_counts().sort_index()
#         peak_hours = hour_counts.nlargest(3)
#         print(f"   错误高峰时段:")
#         for hour, count in peak_hours.items():
#             percentage = count / len(self.df) * 100
#             print(f"     {hour}:00-{hour}:59: {count}次 ({percentage:.1f}%)")
#
#         # 连续错误分析
#         if len(self.df) > 1:
#             time_diffs = self.df['log_time'].diff().dropna()
#             quick_errors = sum(1 for td in time_diffs if td.total_seconds() < 60)
#             print(f"   1分钟内连续错误: {quick_errors}次")
#
#             avg_interval = time_diffs.mean()
#             print(f"   平均错误间隔: {avg_interval}")
#
#         # 功能码时间模式
#         print(f"   功能码时间模式:")
#         for func_code in self.df['func_code'].unique():
#             func_data = self.df[self.df['func_code'] == func_code]
#             peak_hour = func_data['hour'].mode().iloc[0] if not func_data['hour'].mode().empty else 'N/A'
#             func_desc = self.get_function_code_description(func_code)
#             print(f"     {func_code}({func_desc}): 高峰时段 {peak_hour}点")
#
#     def generate_summary_report(self):
#         """生成汇总报告"""
#         print("\n" + "=" * 80)
#         print("错误汇总报告")
#         print("=" * 80)
#
#         total_errors = len(self.df)
#         date_range = f"{self.df['date'].min()} 到 {self.df['date'].max()}"
#
#         print(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
#         print(f"分析数据范围: {date_range}")
#         print(f"总错误次数: {total_errors}")
#         print(f"影响的COM端口: {', '.join(sorted(self.df['com_port'].unique()))}")
#         print(f"涉及的设备地址: {', '.join(sorted(self.df['device_addr'].unique()))}")
#         print(f"涉及的功能码: {', '.join(sorted(self.df['func_code'].unique()))}")
#
#         # 主要问题总结
#         most_problematic_device = self.df['device_addr'].value_counts().index[0]
#         most_problematic_func = self.df['func_code'].value_counts().index[0]
#         most_problematic_hour = self.df['hour'].value_counts().index[0]
#
#         print(f"\n问题焦点:")
#         print(f"  最问题严重的设备: {most_problematic_device}")
#         print(f"  最问题严重的功能: {self.get_function_code_description(most_problematic_func)}")
#         print(f"  最问题严重的时段: {most_problematic_hour}点")
#
#         # 建议
#         print(f"\n优化建议:")
#         print(f"  1. 重点检查设备{most_problematic_device}的硬件连接和配置")
#         print(f"  2. 优化{self.get_function_code_description(most_problematic_func)}操作的通信参数")
#         print(f"  3. 在{most_problematic_hour}点时段加强监控和维护")
#         print(f"  4. 检查CRC校验算法和数据传输质量")
#         print(f"  5. 考虑调整通信超时和重试策略")
#
#
# def main():
#
#     # 使用分析器
#     analyzer = AdvancedModbusLogAnalyzer('crc校验错误1023_1027.txt')  # 替换为你的实际文件路径
#     df = analyzer.parse_log_file()
#
#     if df is not None and not df.empty:
#         # 详细时间模式分析
#         analyzer.analyze_detailed_time_patterns()
#
#         # 报文类型分析
#         analyzer.analyze_message_types()
#
#         # 时间相关性分析
#         analyzer.analyze_time_correlation()
#
#         # 生成汇总报告
#         analyzer.generate_summary_report()
#     else:
#         print("无法解析日志文件或文件为空")
#
#
# if __name__ == "__main__":
#     main()
import re
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta
import numpy as np
from collections import Counter
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False


class AdvancedModbusLogAnalyzer:
    def __init__(self, log_file_path):
        self.log_file_path = log_file_path
        self.data = []
        self.df = None

    def parse_modbus_frame(self, hex_data):
        """解析Modbus帧结构"""
        if len(hex_data) < 4:
            return {}

        # 基本结构：设备地址(1字节) + 功能码(1字节) + 数据 + CRC(2字节)
        device_addr = hex_data[:2]
        func_code = hex_data[2:4]

        result = {
            'device_addr': device_addr,
            'func_code': func_code,
            'frame_type': f"{device_addr}{func_code}",
            'full_frame_type': f"{device_addr}{func_code}"  # 用于更详细的分类
        }

        # 根据功能码解析具体内容
        if func_code == '03':  # 读保持寄存器
            if len(hex_data) >= 12:
                start_addr = hex_data[4:8]
                reg_count = hex_data[8:12]
                result.update({
                    'start_addr': start_addr,
                    'reg_count': reg_count,
                    'operation': '读保持寄存器'
                })
        elif func_code == '04':  # 读输入寄存器
            if len(hex_data) >= 12:
                start_addr = hex_data[4:8]
                reg_count = hex_data[8:12]
                result.update({
                    'start_addr': start_addr,
                    'reg_count': reg_count,
                    'operation': '读输入寄存器'
                })
        elif func_code == '01':  # 读线圈状态
            if len(hex_data) >= 12:
                start_addr = hex_data[4:8]
                coil_count = hex_data[8:12]
                result.update({
                    'start_addr': start_addr,
                    'coil_count': coil_count,
                    'operation': '读线圈状态'
                })
        elif func_code == '05':  # 写单个线圈
            if len(hex_data) >= 12:
                coil_addr = hex_data[4:8]
                coil_value = hex_data[8:12]
                result.update({
                    'coil_addr': coil_addr,
                    'coil_value': coil_value,
                    'operation': '写单个线圈'
                })
        elif func_code == '06':  # 写单个寄存器
            if len(hex_data) >= 12:
                reg_addr = hex_data[4:8]
                reg_value = hex_data[8:12]
                result.update({
                    'reg_addr': reg_addr,
                    'reg_value': reg_value,
                    'operation': '写单个寄存器'
                })

        return result

    def parse_log_file(self):
        """解析日志文件"""
        pattern = r'行 (\d+): (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| ERROR \| p_monitor_data \| New_Mod_Bus:_log_error:\d+ \| (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)-(COM\d+)-请求报文([0-9a-fA-F]+)-响应报文([0-9a-fA-F]+)-Time OUT(\d+)-数据错误，CRC验证失败'

        try:
            with open(self.log_file_path, 'r', encoding='utf-8') as file:
                content = file.read()
        except:
            try:
                with open(self.log_file_path, 'r', encoding='gbk') as file:
                    content = file.read()
            except:
                print("无法读取文件，请检查文件路径和编码")
                return None

        matches = re.findall(pattern, content)

        for match in matches:
            line_num, log_time, detail_time, com_port, request, response, timeout = match

            # 解析时间 - 使用详细时间
            log_datetime = datetime.strptime(log_time, '%Y-%m-%d %H:%M:%S')
            # 解析毫秒级别的详细时间
            detail_datetime = datetime.strptime(detail_time, '%Y-%m-%d %H:%M:%S.%f')

            # 解析Modbus帧
            request_info = self.parse_modbus_frame(request)
            response_info = self.parse_modbus_frame(response)

            # 创建更详细的时间维度
            data_entry = {
                'line_number': int(line_num),
                'log_time': log_datetime,
                'detail_time': detail_datetime,  # 精确到毫秒的时间
                'com_port': com_port,
                'request_hex': request,
                'response_hex': response,
                'timeout': int(timeout),
                'request_length': len(request),
                'response_length': len(response),

                # 时间维度
                'hour': detail_datetime.hour,  # 使用更精确的时间
                'minute': detail_datetime.minute,
                'date': detail_datetime.date(),
                'weekday': detail_datetime.weekday(),
                'time_period': self.get_time_period(detail_datetime.hour),
                'minute_in_hour': detail_datetime.minute,
                'second': detail_datetime.second,

                # Modbus解析信息
                'device_addr': request_info.get('device_addr', ''),
                'func_code': request_info.get('func_code', ''),
                'frame_type': request_info.get('frame_type', ''),
                'full_frame_type': request_info.get('full_frame_type', ''),
                'operation': request_info.get('operation', '未知操作'),
                'start_addr': request_info.get('start_addr', ''),
                'reg_count': request_info.get('reg_count', ''),

                # 响应信息
                'resp_device_addr': response_info.get('device_addr', ''),
                'resp_func_code': response_info.get('func_code', ''),
                'resp_frame_type': response_info.get('frame_type', ''),
            }

            self.data.append(data_entry)

        self.df = pd.DataFrame(self.data)
        # 按时间排序
        self.df = self.df.sort_values('detail_time')
        print(f"成功解析 {len(self.data)} 条错误记录")
        return self.df

    def get_time_period(self, hour):
        """将小时转换为时间段"""
        if 6 <= hour < 12:
            return '上午'
        elif 12 <= hour < 18:
            return '下午'
        elif 18 <= hour < 24:
            return '晚上'
        else:
            return '深夜'

    def create_time_series_plot(self):
        """创建时间序列折线图"""
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(20, 12))

        # 获取主要的报文类型（出现频率最高的几种）
        main_frame_types = self.df['full_frame_type'].value_counts().head(8).index.tolist()

        # 设置颜色
        colors = plt.cm.tab10(np.linspace(0, 1, len(main_frame_types)))

        # 第一个子图：累积错误次数
        ax1.set_title('错误累积趋势 - 按报文类型', fontsize=16, fontweight='bold')

        cumulative_counts = {}
        for frame_type in main_frame_types:
            frame_data = self.df[self.df['full_frame_type'] == frame_type].copy()
            frame_data = frame_data.sort_values('detail_time')

            # 计算累积计数
            frame_data['cumulative_count'] = range(1, len(frame_data) + 1)
            cumulative_counts[frame_type] = frame_data

            ax1.plot(frame_data['detail_time'],
                     frame_data['cumulative_count'],
                     label=f'{frame_type} ({len(frame_data)}次)',
                     linewidth=2,
                     marker='o',
                     markersize=4,
                     alpha=0.8)

        ax1.set_xlabel('时间', fontsize=12)
        ax1.set_ylabel('累积错误次数', fontsize=12)
        ax1.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)

        # 格式化x轴时间显示
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        ax1.xaxis.set_major_locator(mdates.HourLocator(interval=6))

        # 第二个子图：时间窗口内的错误频率
        ax2.set_title('错误频率趋势 - 按报文类型（30分钟滑动窗口）', fontsize=16, fontweight='bold')

        # 创建30分钟的时间窗口
        time_range = pd.date_range(start=self.df['detail_time'].min(),
                                   end=self.df['detail_time'].max(),
                                   freq='30T')  # 30分钟间隔

        for i, frame_type in enumerate(main_frame_types):
            frame_data = self.df[self.df['full_frame_type'] == frame_type]

            # 计算每个时间窗口的错误数量
            window_counts = []
            window_times = []

            for window_start in time_range[:-1]:
                window_end = window_start + timedelta(minutes=30)
                count = len(frame_data[(frame_data['detail_time'] >= window_start) &
                                       (frame_data['detail_time'] < window_end)])
                window_counts.append(count)
                window_times.append(window_start + timedelta(minutes=15))  # 窗口中点

            if any(c > 0 for c in window_counts):  # 只显示有数据的线
                ax2.plot(window_times,
                         window_counts,
                         label=f'{frame_type}',
                         linewidth=2,
                         marker='s',
                         markersize=4,
                         color=colors[i],
                         alpha=0.8)

        ax2.set_xlabel('时间', fontsize=12)
        ax2.set_ylabel('30分钟内错误次数', fontsize=12)
        ax2.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)

        # 格式化x轴时间显示
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        ax2.xaxis.set_major_locator(mdates.HourLocator(interval=6))

        plt.tight_layout()
        plt.show()

    def create_detailed_time_series(self):
        """创建更详细的时间序列分析图"""
        # 创建一个大的图形包含多个子图
        fig = plt.figure(figsize=(24, 16))

        # 获取主要报文类型
        main_frame_types = self.df['full_frame_type'].value_counts().head(6).index.tolist()
        colors = plt.cm.Set1(np.linspace(0, 1, len(main_frame_types)))

        # 1. 精确时间点错误分布
        ax1 = plt.subplot(3, 2, 1)
        for i, frame_type in enumerate(main_frame_types):
            frame_data = self.df[self.df['full_frame_type'] == frame_type]
            # 使用散点图显示精确时间点
            ax1.scatter(frame_data['detail_time'],
                        [i] * len(frame_data),
                        label=f'{frame_type} ({len(frame_data)}次)',
                        alpha=0.6,
                        s=30,
                        color=colors[i])

        ax1.set_title('错误发生时间点分布', fontsize=14)
        ax1.set_xlabel('时间')
        ax1.set_ylabel('报文类型')
        ax1.set_yticks(range(len(main_frame_types)))
        ax1.set_yticklabels(main_frame_types)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)

        # 2. 5分钟间隔的错误频率
        ax2 = plt.subplot(3, 2, 2)
        time_range_5min = pd.date_range(start=self.df['detail_time'].min(),
                                        end=self.df['detail_time'].max(),
                                        freq='5T')

        for i, frame_type in enumerate(main_frame_types):
            frame_data = self.df[self.df['full_frame_type'] == frame_type]

            counts_5min = []
            times_5min = []

            for window_start in time_range_5min[:-1]:
                window_end = window_start + timedelta(minutes=5)
                count = len(frame_data[(frame_data['detail_time'] >= window_start) &
                                       (frame_data['detail_time'] < window_end)])
                counts_5min.append(count)
                times_5min.append(window_start + timedelta(minutes=2.5))

            if any(c > 0 for c in counts_5min):
                ax2.plot(times_5min, counts_5min,
                         label=f'{frame_type}',
                         linewidth=2,
                         marker='o',
                         markersize=3,
                         color=colors[i])

        ax2.set_title('5分钟间隔错误频率', fontsize=14)
        ax2.set_xlabel('时间')
        ax2.set_ylabel('5分钟内错误次数')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)

        # 3. 累积错误对比
        ax3 = plt.subplot(3, 2, 3)
        for i, frame_type in enumerate(main_frame_types):
            frame_data = self.df[self.df['full_frame_type'] == frame_type].sort_values('detail_time')
            if len(frame_data) > 0:
                cumulative = range(1, len(frame_data) + 1)
                ax3.plot(frame_data['detail_time'], cumulative,
                         label=f'{frame_type}',
                         linewidth=3,
                         color=colors[i])

        ax3.set_title('累积错误次数对比', fontsize=14)
        ax3.set_xlabel('时间')
        ax3.set_ylabel('累积错误次数')
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        ax3.tick_params(axis='x', rotation=45)

        # 4. 错误密度热力图
        ax4 = plt.subplot(3, 2, 4)

        # 创建时间-报文类型的错误密度矩阵
        time_range_hour = pd.date_range(start=self.df['detail_time'].min(),
                                        end=self.df['detail_time'].max(),
                                        freq='1H')

        density_matrix = []
        time_labels = []

        for window_start in time_range_hour[:-1]:
            window_end = window_start + timedelta(hours=1)
            row = []
            for frame_type in main_frame_types:
                frame_data = self.df[self.df['full_frame_type'] == frame_type]
                count = len(frame_data[(frame_data['detail_time'] >= window_start) &
                                       (frame_data['detail_time'] < window_end)])
                row.append(count)
            density_matrix.append(row)
            time_labels.append(window_start.strftime('%m-%d %H:%M'))

        if density_matrix:
            sns.heatmap(np.array(density_matrix).T,
                        xticklabels=time_labels[::max(1, len(time_labels) // 10)],
                        yticklabels=main_frame_types,
                        cmap='YlOrRd',
                        annot=False,
                        ax=ax4,
                        cbar_kws={'label': '错误次数'})
            ax4.set_title('错误密度热力图（小时级）', fontsize=14)
            ax4.set_xlabel('时间')
            ax4.set_ylabel('报文类型')

        # 5. 错误间隔分析
        ax5 = plt.subplot(3, 2, 5)
        for i, frame_type in enumerate(main_frame_types):
            frame_data = self.df[self.df['full_frame_type'] == frame_type].sort_values('detail_time')
            if len(frame_data) > 1:
                intervals = frame_data['detail_time'].diff().dropna()
                interval_seconds = [td.total_seconds() for td in intervals]

                if interval_seconds:
                    ax5.hist(interval_seconds,
                             bins=20,
                             alpha=0.6,
                             label=f'{frame_type}',
                             color=colors[i])

        ax5.set_title('错误间隔时间分布', fontsize=14)
        ax5.set_xlabel('间隔时间（秒）')
        ax5.set_ylabel('频次')
        ax5.legend()
        ax5.grid(True, alpha=0.3)
        ax5.set_yscale('log')  # 使用对数刻度

        # 6. 实时错误率
        ax6 = plt.subplot(3, 2, 6)

        # 计算滑动平均错误率
        window_size = timedelta(minutes=10)

        for i, frame_type in enumerate(main_frame_types):
            frame_data = self.df[self.df['full_frame_type'] == frame_type].sort_values('detail_time')

            if len(frame_data) > 5:  # 至少需要5个数据点
                rates = []
                times = []

                for j in range(len(frame_data)):
                    current_time = frame_data.iloc[j]['detail_time']
                    window_start = current_time - window_size

                    # 计算窗口内的错误数
                    window_errors = len(frame_data[
                                            (frame_data['detail_time'] >= window_start) &
                                            (frame_data['detail_time'] <= current_time)
                                            ])

                    rate = window_errors / (window_size.total_seconds() / 60)  # 每分钟错误数
                    rates.append(rate)
                    times.append(current_time)

                ax6.plot(times, rates,
                         label=f'{frame_type}',
                         linewidth=2,
                         color=colors[i])

        ax6.set_title('实时错误率（10分钟滑动窗口）', fontsize=14)
        ax6.set_xlabel('时间')
        ax6.set_ylabel('错误率（次/分钟）')
        ax6.legend()
        ax6.grid(True, alpha=0.3)
        ax6.tick_params(axis='x', rotation=45)

        plt.tight_layout()
        plt.show()

    def analyze_detailed_time_patterns(self):
        """详细时间模式分析"""
        fig = plt.figure(figsize=(24, 20))

        # ... 保持原有的分析代码 ...
        # （这里包含之前的16个图表分析）

        # 1. 按分钟级别的时间分布（热力图）
        plt.subplot(4, 4, 1)
        hour_minute_data = self.df.groupby(['hour', 'minute_in_hour']).size().unstack(fill_value=0)
        if not hour_minute_data.empty:
            sns.heatmap(hour_minute_data, cmap='YlOrRd', cbar_kws={'label': '错误次数'})
            plt.title('错误分布热力图（小时-分钟）', fontsize=12)
            plt.xlabel('分钟')
            plt.ylabel('小时')

        # ... 其他原有图表代码 ...

        plt.tight_layout()
        plt.show()

    def analyze_message_types(self):
        """具体报文类型分析"""
        print("\n" + "=" * 80)
        print("报文类型详细分析")
        print("=" * 80)

        # 统计各种报文类型
        frame_types = self.df['full_frame_type'].value_counts()

        print(f"\n1. 报文类型统计（设备地址+功能码）:")
        for frame_type, count in frame_types.head(15).items():
            device_addr = frame_type[:2] if len(frame_type) >= 2 else ''
            func_code = frame_type[2:4] if len(frame_type) >= 4 else ''
            percentage = count / len(self.df) * 100

            # 解释功能码
            func_desc = self.get_function_code_description(func_code)

            print(f"   {frame_type} (设备{device_addr}-{func_desc}): {count}次 ({percentage:.1f}%)")

        # 时间分布分析
        print(f"\n2. 时间分布详情:")
        print(f"   时间范围: {self.df['detail_time'].min()} 到 {self.df['detail_time'].max()}")
        print(f"   总时长: {self.df['detail_time'].max() - self.df['detail_time'].min()}")

        # 各报文类型的时间分布
        for frame_type in frame_types.head(5).index:
            frame_data = self.df[self.df['full_frame_type'] == frame_type]
            print(f"   {frame_type}: 首次 {frame_data['detail_time'].min()}, "
                  f"末次 {frame_data['detail_time'].max()}")

    def get_function_code_description(self, func_code):
        """获取功能码描述"""
        descriptions = {
            '01': '读线圈状态',
            '02': '读离散输入',
            '03': '读保持寄存器',
            '04': '读输入寄存器',
            '05': '写单个线圈',
            '06': '写单个寄存器',
            '15': '写多个线圈',
            '16': '写多个寄存器'
        }
        return descriptions.get(func_code, f'未知功能码({func_code})')


def main():



    # 使用分析器
    analyzer = AdvancedModbusLogAnalyzer('crc校验错误1023_1027.txt')  # 替换为你的实际文件路径
    df = analyzer.parse_log_file()

    if df is not None and not df.empty:
        # 创建时间序列图表
        print("生成时间序列折线图...")
        analyzer.create_time_series_plot()

        # 创建详细的时间序列分析
        print("生成详细时间序列分析...")
        analyzer.create_detailed_time_series()

        # 报文类型分析
        analyzer.analyze_message_types()
    else:
        print("无法解析日志文件或文件为空")


if __name__ == "__main__":
    main()