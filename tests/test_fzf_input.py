"""Exercise the real fzf pipe, including legacy Windows locale encoding."""
import io
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console

from cenima.cli import CinemaCLI, main


class FzfInputTests(unittest.TestCase):
    def test_fzf_shortcut_and_initial_position_are_applied(self):
        app = object.__new__(CinemaCLI)
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.returncode = 0
        process.communicate.return_value = ('r\n2. second\n', None)
        with patch('cenima.cli.CONSOLE', Console(file=io.StringIO())), patch(
            'cenima.cli.shutil.which', return_value='fzf'
        ), patch('cenima.cli.subprocess.Popen', return_value=process) as launch:
            self.assertEqual(app.run_fzf(['first', 'second'], initial_index=1, shortcuts={'r': 0}), 0)
        command = launch.call_args.args[0]
        self.assertIn('--bind=load:pos(2)', command)
        self.assertIn('--expect=r', command)

    def test_enter_with_expect_keeps_selected_index(self):
        app = object.__new__(CinemaCLI)
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.returncode = 0
        process.communicate.return_value = ('\n2. second\n', None)
        with patch('cenima.cli.CONSOLE', Console(file=io.StringIO())), patch(
            'cenima.cli.shutil.which', return_value='fzf'
        ), patch('cenima.cli.subprocess.Popen', return_value=process):
            self.assertEqual(app.run_fzf(['first', 'second'], shortcuts={'r': 0}), 1)

    def test_object_menu_maps_action_to_original_object(self):
        from cenima.picker import PickerAction
        app = object.__new__(CinemaCLI)
        item = {'title': 'one'}
        app.run_fzf = Mock(return_value=PickerAction('retry', 'one', 0))
        result = app.run_fzf_objects([item], lambda row: row['title'])
        self.assertIs(result.item, item)
        self.assertEqual(result.action, 'retry')

    def test_captured_output_explains_direct_terminal_launch(self):
        console = Console(file=io.StringIO(), force_terminal=False)
        with patch('cenima.cli.CONSOLE', console), patch('cenima.cli.CinemaCLI') as app, patch(
            'sys.stderr', new_callable=io.StringIO
        ) as errors:
            with self.assertRaises(SystemExit) as stopped:
                main([])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn('Run cinema-cli directly', errors.getvalue())
        app.assert_not_called()

    @unittest.skipUnless(shutil.which('fzf'), 'Requires fzf')
    def test_real_fzf_receives_unicode_under_legacy_locale(self):
        # Test a fresh interpreter: reconfiguring stdout does not change
        # subprocess text-pipe defaults when Python UTF-8 mode is disabled.
        program = '''
import io
import subprocess
from unittest.mock import patch
from rich.console import Console
from cenima.cli import CinemaCLI
app = object.__new__(CinemaCLI)
app.config = type('Config', (), {'get_theme': lambda self: {'primary': 'cyan', 'accent': 'blue'}})()
original_popen = subprocess.Popen
def filtered_popen(command, **kwargs):
    return original_popen(command + ['--filter=Hijack'], **kwargs)
with patch('cenima.cli.CONSOLE', Console(file=io.StringIO())), patch('cenima.cli.subprocess.Popen', side_effect=filtered_popen):
    result = app.run_fzf(['Hijack 🎬 مسلسل', 'Other'], title='Results')
    assert result == 0, result
'''
        environment = {key: value for key, value in os.environ.items() if not key.startswith('FZF_')}
        environment['PYTHONUTF8'] = '0'
        result = subprocess.run(
            [sys.executable, '-X', 'utf8=0', '-c', program],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))

    def test_failed_fzf_is_reaped_before_fallback(self):
        app = object.__new__(CinemaCLI)
        app.config = Mock()
        app.config.get_theme.return_value = {'primary': 'cyan', 'accent': 'blue'}
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.communicate.side_effect = [OSError('pipe failed'), ('', '')]
        process.poll.return_value = None
        with patch('cenima.cli.CONSOLE', Console(file=io.StringIO())), patch(
            'cenima.cli.shutil.which', return_value='fzf'
        ), patch('cenima.cli.subprocess.Popen', return_value=process), patch(
            'cenima.cli.RichPicker'
        ) as fallback, self.assertLogs('cenima.cli', level='WARNING'):
            fallback.return_value.pick.return_value = 1
            self.assertEqual(app.run_fzf(['first', 'second']), 1)
            process.kill.assert_called_once()
            self.assertEqual(process.communicate.call_count, 2)
            fallback.return_value.pick.assert_called_once()
