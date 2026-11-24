import os
import shutil
import subprocess
import time
import unittest
from cenima.mpv import MpvIpcClient, MpvWatcher


@unittest.skipUnless(os.name == "nt" and shutil.which("mpv"), "Requires Windows mpv")
class WindowsMpvTests(unittest.TestCase):
    def test_real_named_pipe_commands_watcher_and_shutdown(self):
        path = "\\\\.\\pipe\\cenima-test-" + str(time.time_ns())
        proc = subprocess.Popen(
            ["mpv", "--no-config", "--idle=yes", "--vo=null", "--ao=null",
             "--no-terminal", "--input-ipc-server=" + path],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        watcher = MpvWatcher(path, poll_interval=0.02)
        client = None
        try:
            deadline = time.monotonic() + 10
            while client is None:
                try:
                    client = MpvIpcClient(path)
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            self.assertEqual(client.command(["set_property", "pause", True])["error"], "success")
            self.assertIs(client.get_property("pause"), True)
            watcher.start()
            deadline = time.monotonic() + 5
            while watcher.state.paused is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIs(watcher.state.paused, True)
            watcher.stop()
            watcher.join(timeout=3)
            self.assertFalse(watcher.is_alive())
        finally:
            watcher.stop()
            if watcher.ident is not None:
                watcher.join(timeout=3)
            if client:
                client.command(["quit"])
                client.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
