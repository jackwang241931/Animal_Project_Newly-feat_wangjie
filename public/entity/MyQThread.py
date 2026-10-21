import threading
import traceback
import time
import weakref

from PyQt6.QtCore import QThread, QMutex, QWaitCondition
from loguru import logger

from public.util.native_crash_diagnostics import crash_breadcrumb, native_call

#logger = logger.bind(category="gui_logger")
_registered_qthreads = weakref.WeakSet()
_registered_qthreads_lock = threading.Lock()


def stop_registered_qthreads(timeout_ms=15000):
    """Stop and join GUI MyQThreads before SIP/Qt interpreter teardown.

    Request every stop first so a page with many workers does not consume a
    separate timeout for each worker.  Keep strong references until every
    wait has completed; module-level and hidden-page threads are included.
    """
    with _registered_qthreads_lock:
        threads = list(_registered_qthreads)

    running = []
    failed = []
    for thread in threads:
        try:
            if not thread.isRunning() or QThread.currentThread() == thread:
                continue
            thread.blockSignals(True)
            thread.stop()
            thread.requestInterruption()
            thread.quit()
            running.append(thread)
        except RuntimeError as error:
            logger.warning(f"Qt 退出时跳过已销毁线程: {error}")
        except Exception as error:
            failed.append(getattr(thread, "name", type(thread).__name__))
            logger.exception(f"Qt 退出时请求停止线程失败: {error}")

    deadline = time.monotonic() + timeout_ms / 1000.0
    for thread in running:
        try:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            with native_call("qt_thread", f"{thread.name}.shutdown_wait", timeout_ms=remaining_ms):
                if not thread.wait(remaining_ms):
                    failed.append(thread.name)
        except Exception as error:
            failed.append(getattr(thread, "name", type(thread).__name__))
            logger.exception(f"Qt 退出时等待线程失败: {error}")

    # Catch a worker started during this barrier (for example by a late page
    # callback) instead of reporting success from the initial snapshot alone.
    with _registered_qthreads_lock:
        remaining = list(_registered_qthreads)
    for thread in remaining:
        try:
            if thread.isRunning() and QThread.currentThread() != thread:
                failed.append(thread.name)
        except RuntimeError:
            pass

    if failed:
        failed = sorted(set(failed))
        logger.critical(f"Qt 退出前仍有线程未结束: {failed}")
        crash_breadcrumb("qt_thread", "shutdown_barrier", "TIMEOUT", threads=failed)
        return False
    logger.info(f"Qt 退出线程屏障完成: joined={len(running)}")
    crash_breadcrumb("qt_thread", "shutdown_barrier", "COMPLETED", joined=len(running))
    return True


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
        with _registered_qthreads_lock:
            _registered_qthreads.add(self)

    def start(self, priority=QThread.Priority.InheritPriority):
        # Set these before QThread.start(): a stop immediately after start
        # must not be undone when the worker eventually enters run().
        if not self.isRunning():
            self._stop_requested = False
            self._paused = False
        super().start(priority)

    def run(self):
        self._python_thread_id = threading.get_ident()
        self._native_thread_id = threading.get_native_id()
        logger.warning(
            f"[THREAD_LIFECYCLE] started | name={self.name} | "
            f"python_tid={self._python_thread_id} | "
            f"native_tid={self._native_thread_id} | "
            f"native_tid_hex=0x{self._native_thread_id:X} | daemon=False"
        )
        crash_breadcrumb(
            "qt_thread",
            self.name,
            "STARTED",
            python_tid=self._python_thread_id,
            native_tid=self._native_thread_id,
        )
        self._running = True

        try:
            with native_call("qt_thread", f"{self.name}.before_Runing_work"):
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
                    crash_breadcrumb(
                        "qt_thread",
                        f"{self.name}.dosomething",
                        "PY_EXCEPTION",
                        exception_type=type(e).__name__,
                        exception_message=str(e),
                    )
                    logger.exception(f"{self.name} dosomething error: {e}")
                    break

        except Exception as e:
            crash_breadcrumb(
                "qt_thread",
                f"{self.name}.run",
                "PY_EXCEPTION",
                exception_type=type(e).__name__,
                exception_message=str(e),
            )
            logger.exception(f"{self.name} run() exception: {e}")
        finally:
            self._running = False
            crash_breadcrumb(
                "qt_thread",
                self.name,
                "EXITED",
                python_tid=self._python_thread_id,
                native_tid=self._native_thread_id,
            )
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
        crash_breadcrumb(
            "qt_thread",
            self.name,
            "STOP_REQUESTED",
            target_python_tid=self._python_thread_id,
            target_native_tid=self._native_thread_id,
            caller=threading.current_thread().name,
            caller_native_tid=caller_native_id,
        )
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
                with native_call("qt_thread", f"{self.name}.wait", timeout_ms=5000):
                    if not self.wait(5000):
                        logger.error(f"{self.name}线程退出等待超时")
        except Exception as error:
            logger.exception(f"{self.name}删除线程清理失败: {error}")
        finally:
            # The old override never called Qt's implementation, so the
            # QObject wrapper could remain alive through interpreter teardown.
            if not self.isRunning():
                with native_call("qt_thread", f"{self.name}.deleteLater"):
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
        crash_breadcrumb(
            "python_thread",
            self.name,
            "STARTED",
            python_tid=threading.get_ident(),
            native_tid=native_thread_id,
            daemon=self.daemon,
        )
        self._running = True
        try:
            with native_call("python_thread", f"{self.name}.before_Runing_work"):
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
            crash_breadcrumb(
                "python_thread",
                self.name,
                "EXITED",
                python_tid=threading.get_ident(),
                native_tid=native_thread_id,
                daemon=self.daemon,
            )
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
