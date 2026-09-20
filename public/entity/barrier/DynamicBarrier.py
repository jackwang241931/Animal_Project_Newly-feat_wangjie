import threading
import time


class DynamicBarrier:
    """A reusable barrier whose party count can change between rounds."""

    def __init__(self, parties: int, action=None, timeout: float = 30.0):
        if parties < 1:
            raise ValueError("parties must be greater than zero")
        self._lock = threading.Condition(threading.Lock())
        self._parties = parties
        self._action = action
        self._timeout = timeout
        self._count = 0
        self._generation = 0
        self._generation_results = {}
        self._broken = False

    def _finish_generation(self, succeeded: bool):
        generation = self._generation
        self._generation_results[generation] = succeeded
        self._broken = not succeeded
        self._generation += 1
        self._count = 0
        self._lock.notify_all()

        # Retain enough history for delayed waiters without growing forever.
        oldest_generation = self._generation - 64
        for old_generation in tuple(self._generation_results):
            if old_generation < oldest_generation:
                self._generation_results.pop(old_generation, None)

    def _run_action_and_finish(self):
        try:
            if self._action:
                self._action()
        except Exception as exc:
            self._finish_generation(False)
            raise threading.BrokenBarrierError(
                f"barrier action failed: {exc}"
            ) from exc
        self._finish_generation(True)

    def wait(self, timeout: float = None):
        wait_timeout = self._timeout if timeout is None else timeout
        deadline = None if wait_timeout is None else time.monotonic() + wait_timeout

        with self._lock:
            if self._broken:
                raise threading.BrokenBarrierError("barrier is broken; reset required")

            generation = self._generation
            self._count += 1

            if self._count >= self._parties:
                self._run_action_and_finish()
                return 0

            while self._generation == generation:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._finish_generation(False)
                    raise threading.BrokenBarrierError(
                        f"barrier generation {generation} timed out "
                        f"after {wait_timeout:.1f}s"
                    )
                self._lock.wait(timeout=remaining)

            if not self._generation_results.get(generation, False):
                raise threading.BrokenBarrierError(
                    f"barrier generation {generation} was reset or timed out"
                )
            return 1

    def set_parties(self, parties: int):
        """Change the participant count before the next round starts."""
        if parties < 1:
            raise ValueError("parties must be greater than zero")
        with self._lock:
            self._parties = parties
            if self._broken:
                return
            if self._count >= self._parties:
                self._run_action_and_finish()

    def reset(self, parties: int = None):
        """Release current waiters as failed and begin a fresh generation."""
        if parties is not None and parties < 1:
            raise ValueError("parties must be greater than zero")
        with self._lock:
            if parties is not None:
                self._parties = parties
            generation = self._generation
            self._generation_results[generation] = False
            self._generation += 1
            self._count = 0
            self._broken = False
            self._lock.notify_all()

    @property
    def parties(self):
        return self._parties

    @property
    def n_waiting(self):
        with self._lock:
            return self._count

    @property
    def broken(self):
        with self._lock:
            return self._broken
