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


def test_select_and_play_prompts_for_server_on_episode():
    import asyncio
    from unittest.mock import Mock, patch
    from cenima.cli import CinemaCLI, PlaybackOutcome
    from cenima.health import LIVE

    with patch("cenima.cli.CinemaCLI.check_deps", return_value=None), patch("cenima.cli.ServerHealthChecker") as checker_cls:
        cli = CinemaCLI()
        servers = [
            {"name": "⚡ VideoTube (1080p)", "embed_url": "https://down.vidtube.one/d/abc12345.html"},
            {"name": "⚡ UpDown (1080p)", "embed_url": "https://updown.cam/61mzbhi7if19"},
        ]

        def fake_probe_all(items, *args, **kwargs):
            return [
                {**items[0], "health": LIVE, "video_url": "https://cdn.example/1.mp4"},
                {**items[1], "health": LIVE, "video_url": "https://gamescdn.online/2.mp4"},
            ]

        cli.run_fzf_objects = Mock(return_value={
            "name": "⚡ UpDown (1080p)",
            "embed_url": "https://updown.cam/61mzbhi7if19",
            "health": LIVE,
            "video_url": "https://gamescdn.online/2.mp4",
        })
        cli.play_video = Mock(return_value=PlaybackOutcome.FINISHED)
        checker_cls.return_value.probe_all.side_effect = fake_probe_all
        outcome = asyncio.run(
            cli.select_and_play(servers, "Test Show · Episode 1", is_episode=True, season_num=1, ep_num="1")
        )

    assert outcome == PlaybackOutcome.FINISHED
    cli.run_fzf_objects.assert_called_once()
    cli.play_video.assert_called_once()
    assert cli.play_video.call_args.args[0] == "https://gamescdn.online/2.mp4"
