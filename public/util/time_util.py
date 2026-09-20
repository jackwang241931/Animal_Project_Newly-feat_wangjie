import os
import re
import time
from datetime import datetime, timedelta


class time_util():
    """
    时间工具类
    """

    def __init__(self):
        pass
    @classmethod
    # 定义一个函数，用于解析时间字符串
    def parse_time_format_timedelta_string(cls,time_str):
        # 匹配天、时、分、秒的正则表达式
        pattern = re.compile(
            r'(?P<days>-?\d+)天\s*(?P<hours>\d{1,2})时\s*(?P<minutes>\d{1,2})分\s*(?P<seconds>\d{1,2})秒')
        match = pattern.match(time_str)

        if not match:
            return timedelta(days=0, hours=0, minutes=0, seconds=0)

        # 提取天、小时、分钟和秒
        days = int(match.group('days'))
        hours = int(match.group('hours'))
        minutes = int(match.group('minutes'))
        seconds = int(match.group('seconds'))

        # 将解析后的数据转换为 timedelta 对象
        return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    @classmethod
    # 定义一个函数，用于将 timedelta 对象转换回时间字符串
    def timedelta_to_format_timedelta_string(cls,td:timedelta, signed: bool = False, zero_pad: bool = False):
        """

        :param td:
        :signed: 若 True，则保留负号（如 "-1天 ..."），否则总是返回绝对值
        zero_pad: 若 True，则小时/分钟/秒使用两位零填充，例如 "1天 03时 04分 05秒"
        :return:
        """
        total_seconds = int(abs(td.total_seconds()))
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if zero_pad:
            h = f"{hours:02d}"
            m = f"{minutes:02d}"
            s = f"{seconds:02d}"
        else:
            h = str(hours)
            m = str(minutes)
            s = str(seconds)

        sign = "-" if (td.total_seconds() < 0 and signed) else ""
        return f"{sign}{days}天 {h}时 {m}分 {s}秒"

    @classmethod
    def operator_timedelta_str(cls, a: str, b: str,operator:str="+", signed: bool = False, zero_pad: bool = False) -> str:
        """
        计算 a - b 的时间差，并返回形如 "Xd Yh Zm Ws" 的字符串（中文：天 时 分 秒）。
        参数:
          a, b: "2天 03时 04分 05秒" 或 "-0天 00时 00分 05秒" 字符串\
          operator:操作符+-
          signed: 若 True，则保留负号（如 "-1天 ..."），否则总是返回绝对值
          zero_pad: 若 True，则小时/分钟/秒使用两位零填充，例如 "1天 03时 04分 05秒"
        返回:
          字符串，例如 "2天 03时 04分 05秒" 或 "-0天 00时 00分 05秒"
        """
        if not isinstance(a, str) or not isinstance(b, str):
            raise TypeError("a 和 b 必须为 str.str 对象")
        td1 = cls.parse_time_format_timedelta_string(a)
        td2 = cls.parse_time_format_timedelta_string(b)
        # 解析时间字符串
        match operator:
            case "+":
                return cls.timedelta_to_format_timedelta_string(td=td1+td2, signed=signed, zero_pad=zero_pad)
            case "-":
                return cls.timedelta_to_format_timedelta_string(td=td1-td2, signed=signed, zero_pad=zero_pad)
            case "_":
                return "时间格式错误"

    @classmethod
    def format_timedelta(cls,a: datetime, b: datetime, signed: bool = False, zero_pad: bool = False) -> str:
        """
        计算 a - b 的时间差，并返回形如 "Xd Yh Zm Ws" 的字符串（中文：天 时 分 秒）。
        参数:
          a, b: datetime 对象
          signed: 若 True，则保留负号（如 "-1天 ..."），否则总是返回绝对值
          zero_pad: 若 True，则小时/分钟/秒使用两位零填充，例如 "1天 03时 04分 05秒"
        返回:
          字符串，例如 "2天 03时 04分 05秒" 或 "-0天 00时 00分 05秒"
        """
        if not isinstance(a, datetime) or not isinstance(b, datetime):
            raise TypeError("a 和 b 必须为 datetime.datetime 对象")

        delta: timedelta = a - b
        return  cls.timedelta_to_format_timedelta_string(td=delta, signed=signed, zero_pad=zero_pad)
    @classmethod
    def get_file_creation_time_as_string(cls,file_path):
        """
        # 获取文件创建时间的时间戳
        :param file_path:
        :return:
        """
        # 获取文件创建时间的时间戳
        creation_time = os.path.getctime(file_path)
        # 将时间戳转换为结构化时间
        struct_time = time.localtime(creation_time)
        # 格式化为指定模式的字符串
        formatted_time = time.strftime("%Y_%m_%d_%H_%M_%S", struct_time)
        return formatted_time
    @classmethod
    def get_current_times_info(cls):
        """
        获取日期的年份 月 日 时 分 秒
        :param times datetime类型
        :return: year,month,day,hour,minute,second
        """
        # 获取当前日期和时间
        now = datetime.now()

        # 提取年份、月份、日、时、分、秒
        year = now.year
        month = now.month
        day = now.day
        hour = now.hour
        minute = now.minute
        second = now.second

        return year, month, day, hour, minute, second
    @classmethod
    def get_times_info(cls, times: datetime = datetime.now()):
        """
        获取日期的年份 月 日 时 分 秒
        :param times datetime类型
        :return: year,month,day,hour,minute,second
        """

        # 提取年份、月份、日、时、分、秒
        year = times.year
        month = times.month
        day = times.day
        hour = times.hour
        minute = times.minute
        second = times.second
        return year,month,day,hour,minute,second
    @classmethod
    def get_current_week_info(cls):
        """
        获取当前日期的年份 所属第几周 这周的第几天
        :return: year, week_number, weekday
        """
        # 获取当前日期
        now = datetime.now()
        # 获取 ISO 日历信息
        year, week_number, weekday = now.isocalendar()
        # weekday: 1=Monday, 2=Tuesday, ..., 7=Sunday
        return year, week_number, weekday

    @classmethod
    def get_times_week_info(cls, times: datetime = datetime.now()):
        """
        获取日期的年份 所属第几周 这周的第几天
        :param times datetime类型
        :return: year, week_number, weekday
        """
        # 获取 ISO 日历信息
        year, week_number, weekday = times.isocalendar()
        # weekday: 1=Monday, 2=Tuesday, ..., 7=Sunday
        return year, week_number, weekday

    @classmethod
    def get_times_before_days(cls, times: datetime = datetime.today(), before_days: float = 1):
        """
        获取times的几天前的日期信息
        :param times datetime类型
        :param before_days 几天前
        :return: days_ago (int)日期的int值 和 格式化的日期字符串days_ago.strftime("%Y-%m-%d")
        """
        days_ago = (times - timedelta(days=before_days)).timestamp()
        return days_ago, datetime.fromtimestamp(days_ago).strftime("%Y-%m-%d")

    @classmethod
    def get_times_before_hours(cls, times: datetime = datetime.now(), before_hours: float = 1):
        """
        获取times的几小时前的日期信息
        :param times datetime类型
        :param before_hours 几小时前
        :return: days_ago (int)日期的int值 和 格式化的日期字符串days_ago.strftime("%Y-%m-%d")
        """
        days_ago = (times - timedelta(hours=before_hours)).timestamp()
        return days_ago, datetime.fromtimestamp(days_ago).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    @classmethod
    def get_times_before_minutes(cls, times: datetime = datetime.now(), before_minutes: float = 1):
        """
        获取times的几分钟前的日期信息
        :param times datetime类型
        :param before_minutes 几分钟前
        :return: days_ago (int)日期的int值 和 格式化的日期字符串days_ago.strftime("%Y-%m-%d")
        """
        days_ago = (times - timedelta(minutes=before_minutes)).timestamp()
        return days_ago, datetime.fromtimestamp(days_ago).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    @classmethod
    def get_times_before_seconds(cls, times: datetime = datetime.now(), before_seconds: float = 1):
        """
        获取times的几秒前的日期信息
        :param times datetime类型
        :param before_seconds 几秒前
        :return: days_ago (int)日期的int值 和 格式化的日期字符串days_ago.strftime("%Y-%m-%d")
        """
        days_ago = (times - timedelta(seconds=before_seconds)).timestamp()
        return days_ago, datetime.fromtimestamp(days_ago).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    @classmethod
    def get_format_from_time(cls, time_vir=time.time()):
        formatted_time = datetime.fromtimestamp(time_vir).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return formatted_time
        pass

    @classmethod
    def get_format_minute_from_time(cls, time_vir=time.time()):
        formatted_time = datetime.fromtimestamp(time_vir).strftime("%M分%S秒%f")[:-3] + "毫秒"
        return formatted_time
        pass

    @classmethod
    def get_format_file_from_time(cls, time_vir=time.time()):
        formatted_time = datetime.fromtimestamp(time_vir).strftime("%Y_%m_%d_%H_%M_%S_%f")[:-3]
        return formatted_time
        pass
