import os
import shutil
import subprocess
import time
import unittest
from cenima.mpv import MpvIpcClient, MpvWatcher, run_mpv_monitored


@unittest.skipUnless(os.name == "nt" and shutil.which("mpv"), "Requires Windows mpv")
class WindowsMpvTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'Requires ffmpeg')
    def test_generated_local_video_plays_headlessly(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'local café.mp4'
            subprocess.run(
                ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                 '-i', 'color=c=black:s=64x64:r=5', '-t', '0.4', '-an',
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)],
                check=True, capture_output=True, timeout=15,
            )
            result, _ = run_mpv_monitored(
                str(path), local_file=True,
                extra_args=['--no-config', '--vo=null', '--ao=null', '--really-quiet'],
            )
            self.assertEqual(result, 0)

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
