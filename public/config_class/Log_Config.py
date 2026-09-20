from dataclasses import dataclass

@dataclass
class LogConfig:
    log_dir: str = "./log"  # 日志文件的存储目录
    log_level: str = "DEBUG"  # 日志的最低记录级别
    rotation: str = "00:00"  # 日志文件的轮转周期(如 '00:00' 表示每天 0 点轮转)
    retention: str = "30 days"  # 日志文件的保留时间
    format: str = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {process.name} | {thread.name} | {name}:{module}:{line} | {message}"  # 日志的格式化字符串
    enqueue: bool = True  # 是否将日志消息缓存到队列中
    backtrace: bool = True  # 是否记录异常的堆栈信息
    diagnose: bool = True  # 是否记录诊断信息
    enable_console: bool = True  # 是否在控制台输出日志
    console_level: str = "DEBUG"  # 控制台日志的最低记录级别
    exception_log_rotation: str = "daily"  # 异常日志文件的轮转周期(如 'daily' 表示每天轮转)
    exception_log_retention: str = "30 days"  # 异常日志文件的保留时间