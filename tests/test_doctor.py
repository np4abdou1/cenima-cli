import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from cenima.doctor import collect_doctor, run_doctor
from cenima.transfer_safety import (DISK_RESERVE, LocalTransferError, check_space,
                                     TransferWatchdog)


class DoctorTests(unittest.TestCase):
    def test_missing_tools_report_exact_fixes_without_installing(self):
        with patch('cenima.doctor.shutil.which', return_value=None), \
                patch('cenima.doctor.importlib.util.find_spec', return_value=None), \
                tempfile.TemporaryDirectory() as directory:
            config = type('Config', (), {'download_dir': directory})()
            results = collect_doctor(config)
        names = {result['name']: result for result in results}
        self.assertIn('yt-dlp', names)
        self.assertFalse(names['mpv']['ok'])
        self.assertFalse(names['ffmpeg']['ok'])
        self.assertIn('pip install', names['yt-dlp']['fix'])
        self.assertTrue(names['aria2c']['optional'])
    def test_doctor_returns_false_when_required_tool_is_missing(self):
        output = Console()
        with patch('cenima.doctor.collect_doctor', return_value=[
            {'name': 'mpv', 'ok': False, 'detail': 'Not found', 'fix': 'Install mpv'}]):
            self.assertFalse(run_doctor(output))

    def test_disk_headroom_requires_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('cenima.transfer_safety.shutil.disk_usage',
                       return_value=type('Usage', (), {'free': DISK_RESERVE - 1})()):
                with self.assertRaisesRegex(LocalTransferError, 'disk space'):
                    check_space(Path(directory))

    def test_watchdog_distinguishes_stall_from_remux(self):
        watchdog = TransferWatchdog(stall_timeout=10, remux_timeout=20, now=100)
        watchdog.bytes_progress(100, now=105)
        self.assertRaisesRegex(Exception, 'Network transfer stalled',
                               watchdog.check, now=116)
        watchdog.processing(now=200)
        self.assertRaisesRegex(Exception, 'not a network stall',
                               watchdog.check, now=221)