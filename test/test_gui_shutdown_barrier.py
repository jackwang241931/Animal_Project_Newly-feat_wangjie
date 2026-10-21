"""Regression checks for joining Qt workers before interpreter teardown."""

import threading
import unittest

from PyQt6.QtCore import QCoreApplication

from public.entity.MyQThread import MyQThread, stop_registered_qthreads


class _WaitWorker(MyQThread):
    def __init__(self, name, release):
        super().__init__(name)
        self.release = release
        self.entered = threading.Event()

    def dosomething(self):
        self.entered.set()
        self.release.wait(1)


class GuiShutdownBarrierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_joins_all_started_workers(self):
        release = threading.Event()
        workers = [_WaitWorker(f"test_shutdown_{i}", release) for i in range(3)]
        try:
            for worker in workers:
                worker.start()
                self.assertTrue(worker.entered.wait(2))
            release.set()
            self.assertTrue(stop_registered_qthreads(timeout_ms=3000))
            self.assertTrue(all(not worker.isRunning() for worker in workers))
        finally:
            release.set()
            stop_registered_qthreads(timeout_ms=3000)

    def test_reports_timeout_without_destroying_running_worker(self):
        release = threading.Event()
        worker = _WaitWorker("test_shutdown_timeout", release)
        try:
            worker.start()
            self.assertTrue(worker.entered.wait(2))
            self.assertFalse(stop_registered_qthreads(timeout_ms=10))
            self.assertTrue(worker.isRunning())
        finally:
            release.set()
            self.assertTrue(stop_registered_qthreads(timeout_ms=3000))
            self.assertFalse(worker.isRunning())

    def test_stop_immediately_after_start_is_not_reset_by_run(self):
        release = threading.Event()
        worker = _WaitWorker("test_shutdown_start_race", release)
        try:
            worker.start()
            worker.stop()
            release.set()
            self.assertTrue(stop_registered_qthreads(timeout_ms=3000))
            self.assertFalse(worker.isRunning())
        finally:
            release.set()
            stop_registered_qthreads(timeout_ms=3000)


if __name__ == "__main__":
    unittest.main()
