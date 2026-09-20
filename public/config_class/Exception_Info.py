from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class ExceptionInfo:
    """异常信息数据类"""
    process_id: str  # 发生异常的进程标识符
    pid: int  # 发生异常的进程 PID
    timestamp: datetime  # 异常发生的时间
    exception_type: str  # 异常类型
    exception_message: str  # 异常信息
    traceback_info: str  # 异常的堆栈信息
    function_name: str  # 发生异常的函数名
    line_number: int  # 发生异常的行号
    severity: str = "ERROR"  # 异常的严重级别(如 'ERROR', 'CRITICAL' 等)