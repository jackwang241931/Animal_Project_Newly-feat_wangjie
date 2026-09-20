import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from public.util.video_program_instance_lock import VideoProgramInstanceLock


def _start_lock_owner(lock_path: Path, mode: str):
    code = "\n".join(
        [
            "import pathlib",
            "import sys",
            "import time",
            "from public.util.video_program_instance_lock import VideoProgramInstanceLock",
            "lock = VideoProgramInstanceLock(sys.argv[2], pathlib.Path(sys.argv[1]))",
            "print('READY' if lock.acquire() else 'FAILED', flush=True)",
            "time.sleep(30)",
        ]
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(lock_path), mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdout.readline().strip() == "READY"
    return process


class VideoProgramInstanceLockTest(unittest.TestCase):
    def test_both_launch_orders_release_and_recover(self):
        launch_orders = (
            ("host", "standalone"),
            ("standalone", "host"),
        )
        for owner_mode, contender_mode in launch_orders:
            with self.subTest(owner=owner_mode, contender=contender_mode):
                with tempfile.TemporaryDirectory(prefix="video-lock-test-") as temp_dir:
                    lock_path = Path(temp_dir) / "video_program.lock"
                    process = _start_lock_owner(lock_path, owner_mode)

                    try:
                        contender = VideoProgramInstanceLock(contender_mode, lock_path)
                        self.assertFalse(contender.acquire())
                        self.assertEqual(contender.read_owner()["mode"], owner_mode)
                    finally:
                        process.kill()
                        process.wait(timeout=5)
                        process.stdout.close()

                    time.sleep(0.1)
                    recovered_lock = VideoProgramInstanceLock(contender_mode, lock_path)
                    self.assertTrue(recovered_lock.acquire())
                    recovered_lock.release()


if __name__ == "__main__":
    unittest.main()
