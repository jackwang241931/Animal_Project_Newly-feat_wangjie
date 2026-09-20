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

    def run(self):
        logger.warning(f"{self.name} thread {threading.get_ident()} has been started！")
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
            logger.warning(f"{self.name} thread run() ended")

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
        logger.warning(f"{self.name} thread stop() called")
        self.mutex.lock()
        self._stop_requested = True
        self._running = False
        self._paused = False
        self.condition.wakeAll()
        self.mutex.unlock()

    def deleteLater(self):
        """更安全的删除方法"""
        logger.warning(f"开始删除线程 {self.name}")

        try:
            # # 1. 设置停止标志
            # self.stop()

            # 2. 请求中断
            self.requestInterruption()

            # 3. 强制唤醒多次
            for _ in range(5):
                self.mutex.lock()
                self.condition.wakeAll()
                self.mutex.unlock()
                QThread.msleep(20)

            # 4. 退出事件循环
            self.quit()

            # 5. 等待线程结束
            wait_count = 0
            while self.isRunning() and wait_count < 50:  # 最多等待5秒 (50 * 100ms)
                QThread.msleep(100)
                wait_count += 1

                # 每秒再次唤醒一次
                if wait_count % 10 == 0:
                    self.mutex.lock()
                    self.condition.wakeAll()
                    self.mutex.unlock()

            if self.isRunning():
                logger.warning(f"{self.name}线程等待超时，强制终止")
                # self.terminate()
                self.wait(1000)
            else:
                logger.warning(f"{self.name}线程正常结束")

        except Exception as e:
            logger.error(f"{self.name}删除线程异常: {e}")
            try:
                # self.terminate()
                self.wait(1000)
            except Exception as e2:
                logger.error(f"{self.name}强制终止失败: {e2}")
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
        logger.warning(f"{self.name} thread {threading.get_ident()} has been started！")
        self._running = True
        self.before_Runing_work()
        while self._running:
            self.mutex.lock()
            if self._paused:
                self.condition.wait(self.mutex)  # 等待条件变量
            self.mutex.unlock()

            # 执行一些工作（替代为你需要的任务）
            self.dosomething()
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
