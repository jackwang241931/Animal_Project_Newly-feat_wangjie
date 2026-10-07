import threading
import traceback

from PyQt6.QtCore import QThread, QMutex, QWaitCondition
from loguru import logger

#logger = logger.bind(category="gui_logger")
class MyQThread(QThread):
    def __init__(self, name):
        super().__init__()
        super().setObjectName(name)
        self.name = name
        self.mutex = QMutex()
        self.condition = QWaitCondition()
        self._running = False
        self._paused = False
        self._stop_requested = False
        self._python_thread_id = None
        self._native_thread_id = None

    def run(self):
        self._python_thread_id = threading.get_ident()
        self._native_thread_id = threading.get_native_id()
        logger.warning(
            f"[THREAD_LIFECYCLE] started | name={self.name} | "
            f"python_tid={self._python_thread_id} | "
            f"native_tid={self._native_thread_id} | "
            f"native_tid_hex=0x{self._native_thread_id:X} | daemon=False"
        )
        self._running = True
        self._stop_requested = False
        self._paused = False  # 启动时重置状态

        try:
            self.before_Runing_work()

            while not self._stop_requested and not self.isInterruptionRequested():
                self.mutex.lock()

                # 如果被暂停，就等待
                while self._paused and not self._stop_requested and not self.isInterruptionRequested():
                    self.condition.wait(self.mutex, 1000)  # 1秒超时

                # 检查是否需要退出
                should_exit = self._stop_requested or self.isInterruptionRequested()
                self.mutex.unlock()

                if should_exit:
                    break

                # 执行实际工作
                try:
                    self.dosomething()
                except Exception as e:
                    error_msg = [f"{self.name} dosomething error: {e}"]
                    # 错误处理代码...
                    logger.error("\n".join(error_msg))
                    break

        except Exception as e:
            logger.error(f"{self.name} run() exception: {e}")
        finally:
            self._running = False
            logger.warning(
                f"[THREAD_LIFECYCLE] exited | name={self.name} | "
                f"python_tid={self._python_thread_id} | "
                f"native_tid={self._native_thread_id} | "
                f"native_tid_hex=0x{self._native_thread_id:X} | daemon=False"
            )

    def pause(self):
        if not self.isRunning():
            logger.warning(f"{self.name} thread not running, pause ignored")
            return

        self.mutex.lock()
        if not self._stop_requested:
            self._paused = True
            logger.warning(f"{self.name} thread has been paused！")
        self.mutex.unlock()

    def resume(self):
        if not self.isRunning():
            logger.warning(f"{self.name} thread not running, resume ignored")
            return

        self.mutex.lock()
        if self._paused:
            self._paused = False
            self.condition.wakeAll()
            logger.warning(f"{self.name} thread has been resumed！")
        self.mutex.unlock()

    def stop(self):
        caller_native_id = threading.get_native_id()
        logger.warning(
            f"[THREAD_LIFECYCLE] stop_requested | name={self.name} | "
            f"target_python_tid={self._python_thread_id} | "
            f"target_native_tid={self._native_thread_id} | "
            f"caller={threading.current_thread().name} | "
            f"caller_native_tid={caller_native_id} | "
            f"caller_native_tid_hex=0x{caller_native_id:X}"
        )
        self.mutex.lock()
        self._stop_requested = True
        self._running = False
        self._paused = False
        self.condition.wakeAll()
        self.mutex.unlock()

    def deleteLater(self):
        """Stop the worker, then preserve Qt's deferred QObject deletion."""
        logger.warning(f"开始删除线程 {self.name}")
        try:
            self.stop()
            self.requestInterruption()
            self.condition.wakeAll()
            self.quit()

            # Waiting from inside the same QThread would deadlock.
            if QThread.currentThread() != self and self.isRunning():
                if not self.wait(5000):
                    logger.error(f"{self.name}线程退出等待超时")
        except Exception as error:
            logger.exception(f"{self.name}删除线程清理失败: {error}")
        finally:
            # The old override never called Qt's implementation, so the
            # QObject wrapper could remain alive through interpreter teardown.
            if not self.isRunning():
                super().deleteLater()
            else:
                # Never schedule QObject destruction while native code is
                # still executing in the worker thread.
                logger.error(f"{self.name}仍在运行，跳过延迟销毁")
    def isStart(self):
        return self._running and not self._stop_requested

    def isPaused(self):
        return self._paused and self._running and not self._stop_requested

    def move_work_to_thread(self, work):
        self.dosomething = work

    def before_Runing_work(self):
        pass

    def dosomething(self):
        pass

    def __del__(self):
        try:
            name = self.__dict__.get("name", self.__class__.__name__)
            logger.debug(f"Thread {name} deleted")
        except Exception:
            pass

class MyThread(threading.Thread):

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.mutex = QMutex()
        self.condition = QWaitCondition()
        self._running = False
        self._paused = False

    def isRunning(self):  # 添加的函数，与QThread保持一致
        """检查线程是否正在运行"""
        return self._running and self.is_alive()
    def isStart(self):
        return self._running
    def isPaused(self):
        return self._paused and self._running
    def run(self):
        native_thread_id = threading.get_native_id()
        logger.warning(
            f"[THREAD_LIFECYCLE] started | name={self.name} | "
            f"python_tid={threading.get_ident()} | "
            f"native_tid={native_thread_id} | "
            f"native_tid_hex=0x{native_thread_id:X} | daemon={self.daemon}"
        )
        self._running = True
        try:
            self.before_Runing_work()
            while self._running:
                self.mutex.lock()
                if self._paused:
                    self.condition.wait(self.mutex)  # 等待条件变量
                self.mutex.unlock()

                # 执行一些工作（替代为你需要的任务）
                self.dosomething()
        finally:
            self._running = False
            logger.warning(
                f"[THREAD_LIFECYCLE] exited | name={self.name} | "
                f"python_tid={threading.get_ident()} | "
                f"native_tid={native_thread_id} | "
                f"native_tid_hex=0x{native_thread_id:X} | daemon={self.daemon}"
            )
    def move_work_to_thread(self,work):
        self.dosomething=work
    def before_Runing_work(self):
        #执行前的一些工作
        pass
    def dosomething(self):
        # 执行一些工作（替代为你需要的任务）
        pass

    def pause(self):
        # 暂停线程
        self.mutex.lock()
        self._paused = True
        self.mutex.unlock()
        logger.warning(f"{self.name} thread {threading.get_ident()} has been paused！")

    def resume(self):
        self.mutex.lock()
        self._paused = False
        self.condition.wakeAll()  # 唤醒线程
        self.mutex.unlock()
        logger.warning(f"{self.name} thread {threading.get_ident()} has been resumed！")

    def stop(self):
        logger.warning(f"{self.name} thread {threading.get_ident()} has been stopped！")
        self.mutex.lock()
        self._running = False
        self._paused = False  # 确保在停止前取消暂停
        self.condition.wakeAll()  # 可能需要唤醒线程以便其能正常退出
        self.mutex.unlock()
        # self.terminate()

    def __del__(self):
        logger.debug(f"线程{self.name}被销毁!")
