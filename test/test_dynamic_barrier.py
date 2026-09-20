import threading
import time
import unittest

from public.entity.barrier.DynamicBarrier import DynamicBarrier


class DynamicBarrierTests(unittest.TestCase):
    def test_early_stage_notifications_are_not_lost(self):
        ufc_finished = threading.Semaphore(0)
        ugc_finished = threading.Semaphore(0)
        action_calls = []
        errors = []
        barrier = DynamicBarrier(
            4,
            action=lambda: action_calls.append("done"),
            timeout=0.5,
        )

        def ufc_worker():
            try:
                ufc_finished.release()
                barrier.wait()
            except Exception as exc:
                errors.append(exc)

        def ugc_worker():
            try:
                time.sleep(0.05)
                self.assertTrue(ufc_finished.acquire(timeout=0.2))
                ugc_finished.release()
                barrier.wait()
            except Exception as exc:
                errors.append(exc)

        def zos_worker():
            try:
                time.sleep(0.1)
                self.assertTrue(ugc_finished.acquire(timeout=0.2))
                barrier.wait()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=ufc_worker),
            threading.Thread(target=ugc_worker),
            threading.Thread(target=zos_worker),
            threading.Thread(target=barrier.wait),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(action_calls, ["done"])

    def test_all_participants_are_released_and_action_runs_once(self):
        action_calls = []
        results = []
        errors = []
        barrier = DynamicBarrier(
            4,
            action=lambda: action_calls.append("done"),
            timeout=1,
        )

        def worker():
            try:
                results.append(barrier.wait())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(action_calls, ["done"])
        self.assertEqual(len(results), 4)
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_timeout_breaks_the_generation(self):
        barrier = DynamicBarrier(2, timeout=0.05)

        with self.assertRaises(threading.BrokenBarrierError):
            barrier.wait()

        self.assertTrue(barrier.broken)

    def test_reset_releases_waiters_with_broken_barrier(self):
        barrier = DynamicBarrier(2, timeout=2)
        released = threading.Event()

        def worker():
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                released.set()

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.monotonic() + 1
        while barrier.n_waiting == 0 and time.monotonic() < deadline:
            time.sleep(0.005)

        barrier.reset(parties=1)
        thread.join(timeout=1)

        self.assertTrue(released.is_set())
        self.assertFalse(thread.is_alive())
        self.assertFalse(barrier.broken)

    def test_action_failure_breaks_round_instead_of_silently_continuing(self):
        def fail_action():
            raise RuntimeError("flush failed")

        barrier = DynamicBarrier(1, action=fail_action, timeout=1)

        with self.assertRaises(threading.BrokenBarrierError):
            barrier.wait()

        self.assertTrue(barrier.broken)


if __name__ == "__main__":
    unittest.main()
