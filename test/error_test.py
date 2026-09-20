import multiprocessing
import logging
import sys
import os
import traceback
from typing import Callable

from loguru import logger
import json
from datetime import datetime

import multiprocessing
import traceback
import sys
import signal
import logging
from typing import Any, Callable, Optional, Dict
import pickle
import queue


class SubprocessResult:
    """子进程执行结果封装"""

    def __init__(self, success: bool, result: Any = None, error: str = None, traceback_str: str = None):
        self.success = success
        self.result = result
        self.error = error
        self.traceback_str = traceback_str


def safe_subprocess_wrapper(func: Callable, result_queue: multiprocessing.Queue, *args, **kwargs):
    """安全的子进程包装器"""

    def signal_handler(signum, frame):
        """信号处理器"""
        error_msg = f"Process terminated by signal {signum}"
        result = SubprocessResult(False, error=error_msg)
        try:
            result_queue.put(result)
        except:
            pass  # 如果队列已关闭，忽略错误
        sys.exit(1)

    # 设置信号处理器
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # 设置子进程的异常处理
        def handle_exception(exc_type, exc_value, exc_traceback):
            """全局异常处理器"""
            if issubclass(exc_type, KeyboardInterrupt):
                return

            error_msg = str(exc_value)
            traceback_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))

            result = SubprocessResult(
                success=False,
                error=error_msg,
                traceback_str=traceback_str
            )

            try:
                result_queue.put(result)
            except:
                # 如果无法放入队列，至少打印错误
                print(f"Subprocess fatal error: {error_msg}", file=sys.stderr)
                print(traceback_str, file=sys.stderr)

        # 设置全局异常处理器
        sys.excepthook = handle_exception

        # 执行实际函数
        result = func(*args, **kwargs)

        # 成功执行，返回结果
        success_result = SubprocessResult(success=True, result=result)
        result_queue.put(success_result)

    except Exception as e:
        # 捕获所有异常
        error_msg = str(e)
        traceback_str = traceback.format_exc()

        error_result = SubprocessResult(
            success=False,
            error=error_msg,
            traceback_str=traceback_str
        )

        try:
            result_queue.put(error_result)
        except Exception as queue_error:
            # 如果连队列都无法使用，输出到stderr
            print(f"Subprocess error: {error_msg}", file=sys.stderr)
            print(f"Queue error: {queue_error}", file=sys.stderr)
            print(traceback_str, file=sys.stderr)
class SubprocessLogger:
    """子进程专用日志器"""

    def __init__(self, log_file: str = "subprocess.log"):
        self.log_file = log_file
        self.setup_logger()

    def setup_logger(self):
        """设置子进程日志"""
        # 移除默认handler避免冲突
        logger.remove()

        # 添加文件日志
        logger.add(
            self.log_file,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | PID:{process} | {level} | {message}",
            level="DEBUG",
            rotation="10 MB",
            retention="7 days",
            enqueue=True  # 线程安全
        )

        # 添加控制台输出
        logger.add(
            sys.stderr,
            format="<green>{time:HH:mm:ss.SSS}</green> | PID:{process} | <level>{level}</level> | <level>{message}</level>",
            level="INFO",
            colorize=True
        )


def logged_subprocess_wrapper(func: Callable, result_queue: multiprocessing.Queue, log_file: str, *args, **kwargs):
    """带日志的子进程包装器"""

    # 设置子进程日志
    subprocess_logger = SubprocessLogger(log_file)

    process_id = os.getpid()
    logger.info(f"Subprocess {process_id} started, executing {func.__name__}")

    def exception_handler(exc_type, exc_value, exc_traceback):
        """异常处理器"""
        if issubclass(exc_type, KeyboardInterrupt):
            logger.info(f"Subprocess {process_id} interrupted")
            return

        error_msg = str(exc_value)
        traceback_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))

        logger.error(f"Unhandled exception in subprocess {process_id}: {error_msg}")
        logger.error(f"Traceback:\n{traceback_str}")

        result = SubprocessResult(
            success=False,
            error=error_msg,
            traceback_str=traceback_str
        )

        try:
            result_queue.put(result)
        except Exception as e:
            logger.critical(f"Failed to report error via queue: {e}")

    sys.excepthook = exception_handler

    try:
        logger.debug(f"Executing function {func.__name__} with args={args}, kwargs={kwargs}")

        result = func(*args, **kwargs)

        logger.info(f"Function {func.__name__} completed successfully")
        logger.debug(f"Function result: {result}")

        success_result = SubprocessResult(success=True, result=result)
        result_queue.put(success_result)

    except Exception as e:
        error_msg = str(e)
        traceback_str = traceback.format_exc()

        logger.error(f"Exception in function {func.__name__}: {error_msg}")
        logger.debug(f"Exception traceback:\n{traceback_str}")

        error_result = SubprocessResult(
            success=False,
            error=error_msg,
            traceback_str=traceback_str
        )

        try:
            result_queue.put(error_result)
        except Exception as queue_error:
            logger.critical(f"Failed to report error via queue: {queue_error}")

    finally:
        logger.info(f"Subprocess {process_id} finished")


class AdvancedSubprocessExecutor:
    """高级子进程执行器"""

    def __init__(self, timeout: Optional[float] = None, log_file: str = "subprocess.log"):
        self.timeout = timeout
        self.log_file = log_file

        # 设置主进程日志
        self.setup_main_logger()

    def setup_main_logger(self):
        """设置主进程日志"""
        logger.add(
            "main_process.log",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | MAIN | {level} | {message}",
            level="DEBUG",
            rotation="10 MB",
            retention="7 days"
        )

    def execute(self, func: Callable, *args, **kwargs) -> SubprocessResult:
        """执行函数"""

        logger.info(f"Starting subprocess execution of {func.__name__}")

        result_queue = multiprocessing.Queue()

        process = multiprocessing.Process(
            target=logged_subprocess_wrapper,
            args=(func, result_queue, self.log_file) + args,
            kwargs=kwargs
        )

        try:
            process.start()
            logger.debug(f"Subprocess {process.pid} started")

            try:
                result = result_queue.get(timeout=self.timeout)
                process.join(timeout=1.0)

                if result.success:
                    logger.info(f"Subprocess {process.pid} completed successfully")
                else:
                    logger.error(f"Subprocess {process.pid} failed: {result.error}")

                return result

            except queue.Empty:
                logger.warning(f"Subprocess {process.pid} timeout after {self.timeout} seconds")

                process.terminate()
                process.join(timeout=5.0)

                if process.is_alive():
                    logger.error(f"Force killing subprocess {process.pid}")
                    process.kill()
                    process.join()

                return SubprocessResult(
                    success=False,
                    error=f"Process timeout after {self.timeout} seconds"
                )

        except Exception as e:
            logger.error(f"Failed to start subprocess: {str(e)}")
            return SubprocessResult(
                success=False,
                error=f"Failed to start process: {str(e)}"
            )

        finally:
            if process.is_alive():
                logger.warning(f"Cleaning up subprocess {process.pid}")
                process.terminate()
                process.join(timeout=5.0)
                if process.is_alive():
                    process.kill()
                    process.join()


# 使用示例
def risky_operation(data_size: int):
    """可能出错的操作"""
    logger.info(f"Processing {data_size} items")

    if data_size > 1000:
        raise ValueError(f"Data size too large: {data_size}")

    # 模拟处理
    result = []
    for i in range(data_size):
        if i % 100 == 0:
            logger.debug(f"Processed {i} items")
        result.append(i * 2)

    return sum(result)


def main_advanced():
    executor = AdvancedSubprocessExecutor(timeout=10.0, log_file="worker.log")

    # 测试不同的数据大小
    test_sizes = [100, 500, 1500]  # 最后一个会触发异常

    for size in test_sizes:
        print(f"\n=== 测试数据大小: {size} ===")
        result = executor.execute(risky_operation, size)

        if result.success:
            print(f"处理成功，结果: {result.result}")
        else:
            print(f"处理失败: {result.error}")
            if result.traceback_str:
                print("详细错误信息:")
                print(result.traceback_str)


if __name__ == "__main__":
    main_advanced()
    # print(str(None))