"""
PeriodicTimer 可复用类（可注入任务）
- interval_ms: 间隔（毫秒）
- max_duration_ms: 最大运行时长（毫秒），None 表示无限制
- task: 每次触发时要执行的可调用对象，可以接收 0 或 1 个参数(elapsed_ms)
- run_in_thread: 若 True，则把 task 提交到 ThreadPoolExecutor 执行（避免阻塞主线程）
- task_done_callback: 当任务完成时调用的回调，签名为 callback(result, elapsed_ms)
- timer_finished_callback 定时器结束的回调签名为 callback(result, elapsed_ms)
- run_immediately: start 时是否立即执行一次 task
"""

import time
import threading
import traceback
from typing import Optional, Callable, Any
from concurrent.futures import ThreadPoolExecutor
from loguru import logger


class PeriodicTimer:
    """
    可注入任务的周期性定时器（不依赖PyQt）。
    """

    def __init__(
        self,
        interval_ms: int,
        max_duration_ms: Optional[int] = None,
        task: Optional[Callable[..., Any]] = None,
        run_in_thread: bool = False,
        task_done_callback: Optional[Callable[[Any, int], None]] = None,
        timer_finished_callback:Optional[Callable[[Any, int], None]] = None,
        run_immediately: bool = False
    ):
        self.interval_ms = int(interval_ms)
        self.max_duration_ms = None if max_duration_ms is None else int(max_duration_ms)
        self._task = task
        self.run_in_thread = bool(run_in_thread)
        self._task_done_callback = task_done_callback
        self._timer_finished_callback = timer_finished_callback
        self.run_immediately = bool(run_immediately)

        self._timer_thread = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._start_time = None
        self._paused_duration = 0
        self._pause_start_time = None
        self._is_paused = False
        self._is_active = False

        # 线程池
        self._executor = ThreadPoolExecutor()

        # 用于同步回调的锁
        self._callback_lock = threading.Lock()
        #用于定时器结束同步回调的锁
        self._timer_finished_callback_lock = threading.Lock()

    def set_task(self, task: Callable[..., Any]):
        """在运行前或运行时设置/替换任务 callable"""
        self._task = task

    def start(self):
        """启动定时器"""
        if self._is_active:
            return

        self._stop_event.clear()
        self._pause_event.set()  # 初始状态为非暂停
        self._start_time = time.time()
        self._paused_duration = 0
        self._is_paused = False
        self._is_active = True

        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()

    def stop(self):
        """停止定时器"""
        if not self._is_active:
            return

        self._is_active = False
        self._stop_event.set()
        self._pause_event.set()  # 确保线程不会被暂停阻塞

        # if self._timer_thread and self._timer_thread.is_alive():
        #     # self._timer_thread.join(timeout=0.1)  # 给线程 0.1 秒的时间退出
        #     if self._timer_thread.is_alive():
        #         # 如果线程仍然存在,则强制结束
        #         self._timer_thread._stop()

        # 关闭线程池
        self._executor.shutdown(wait=False)

        elapsed = self.get_elapsed_ms()
        self._call_finished_callback(elapsed)

    def pause(self):
        """暂停定时器"""
        if not self._is_active or self._is_paused:
            return

        self._is_paused = True
        self._pause_start_time = time.time()
        self._pause_event.clear()

    def resume(self):
        """恢复定时器"""
        if not self._is_active or not self._is_paused:
            return

        if self._pause_start_time:
            self._paused_duration += time.time() - self._pause_start_time

        self._is_paused = False
        self._pause_event.set()

    def is_active(self) -> bool:
        """返回定时器是否处于活跃状态"""
        return self._is_active and not self._is_paused

    def get_elapsed_ms(self) -> int:
        """获取已经过的时间（毫秒）"""
        if not self._start_time:
            return 0

        current_time = time.time()
        if self._is_paused and self._pause_start_time:
            # 如果当前暂停，计算到暂停开始的时间
            elapsed = (self._pause_start_time - self._start_time) - self._paused_duration
        else:
            # 如果未暂停，计算到当前时间
            elapsed = (current_time - self._start_time) - self._paused_duration

        return int(elapsed * 1000)

    def _timer_loop(self):
        """定时器主循环"""
        if self.run_immediately:
            self._execute_task()

        while not self._stop_event.is_set():
            # 等待暂停事件（如果暂停了会阻塞在这里）
            self._pause_event.wait()

            if self._stop_event.is_set():
                break

            # 检查是否超过最大运行时间
            elapsed = self.get_elapsed_ms()
            if self.max_duration_ms is not None and elapsed >= self.max_duration_ms:
                self.stop()
                break

            # 等待间隔时间
            if self._stop_event.wait(timeout=self.interval_ms / 1000.0):
                break  # 收到停止信号

            # 再次检查暂停状态
            self._pause_event.wait()

            if self._stop_event.is_set():
                break

            # 执行任务
            self._execute_task()

    def _execute_task(self):
        """执行任务"""
        if not self._task:
            return

        elapsed = self.get_elapsed_ms()

        if self.run_in_thread:
            # 在线程池中执行任务
            future = self._executor.submit(self._run_task_with_callback, elapsed)
        else:
            # 在当前线程执行任务
            self._run_task_with_callback(elapsed)

    def _run_task_with_callback(self, elapsed_ms: int):
        """运行任务并调用回调"""
        result = None
        try:
            try:
                result = self._task(elapsed_ms)
            except TypeError:
                # 任务不接受参数
                result = self._task()
        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            traceback.print_exc()
            result = None

        # 调用完成回调 - 支持不同的参数签名
        if self._task_done_callback:
            try:
                with self._callback_lock:
                    # 尝试不同的参数组合
                    import inspect
                    sig = inspect.signature(self._task_done_callback)
                    param_count = len(sig.parameters)

                    if param_count == 0:
                        self._task_done_callback()
                    elif param_count == 1:
                        self._task_done_callback(result)
                    elif param_count == 2:
                        self._task_done_callback(result, elapsed_ms)
                    else:
                        # 默认传递所有参数
                        self._task_done_callback(result, elapsed_ms)

            except Exception as e:
                logger.error(f"Task done callback failed: {e}")
                traceback.print_exc()

        return result

    def _call_finished_callback(self, elapsed_ms: int):
        """调用结束回调（可以被子类重写）"""
        logger.info(f"PeriodicTimer finished after {elapsed_ms}ms")
        # 定时器结束回调 - 支持不同的参数签名
        if self._timer_finished_callback:
            try:
                with self._timer_finished_callback_lock:
                    # 尝试不同的参数组合
                    import inspect
                    sig = inspect.signature(self._timer_finished_callback)
                    param_count = len(sig.parameters)

                    if param_count == 0:
                        self._timer_finished_callback()
                    else:
                        # 默认传递所有参数
                        self._task_done_callback( elapsed_ms)

            except Exception as e:
                logger.error(f"PeriodicTimer finished after {elapsed_ms}ms callback failed: {e}")
                traceback.print_exc()

    def __enter__(self):
        """上下文管理器支持"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器支持"""
        self.stop()


# 使用示例
if __name__ == "__main__":
    def my_task(elapsed_ms):
        print(f"执行任务，已运行: {elapsed_ms}ms")
        time.sleep(0.1)  # 模拟耗时操作
        return f"result_{elapsed_ms}"

    def task_done(result, elapsed_ms):
        print(f"任务完成: {result}, 耗时: {elapsed_ms}ms")

    # 创建定时器
    timer = PeriodicTimer(
        interval_ms=1000,
        max_duration_ms=5000,
        task=my_task,
        run_in_thread=True,
        task_done_callback=task_done,
        run_immediately=True
    )

    # 使用上下文管理器
    with timer:
        time.sleep(6)  # 让定时器运行一段时间

    print("定时器已停止")