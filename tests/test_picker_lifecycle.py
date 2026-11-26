"""Regression coverage for scrolling panels and live menu behavior."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console

import cenima.cli as cli
from cenima import picker


class PickerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.console = Console(file=io.StringIO(), force_terminal=True, width=35, height=10,
                               legacy_windows=False, _environ={'TERM': 'xterm-256color'})
        self.menu = picker.RichPicker(self.console, {'primary': 'cyan', 'accent': 'blue'})
        self.raw = patch('cenima.picker.raw_terminal')
        self.raw.start()
        self.addCleanup(self.raw.stop)

    def test_idle_menu_uses_one_transient_display_without_new_screens(self):
        real_live = picker.Live
        with patch('cenima.picker.Live', wraps=real_live) as live, patch(
            'cenima.picker.read_key', side_effect=[None] * 5 + ['\x1b']
        ):
            self.assertIsNone(self.menu.pick(['A very long item ' * 20], str, title='Downloads'))
        self.assertEqual(live.call_count, 1)
        self.assertTrue(live.call_args.kwargs['transient'])
        self.assertFalse(live.call_args.kwargs['screen'])
        self.assertNotIn('\x1b[?1049h', self.console.file.getvalue())

    def test_initial_selection_is_honored(self):
        with patch('cenima.picker.read_key', side_effect=['\r']):
            self.assertEqual(self.menu.pick(['a', 'b', 'c'], str, initial_index=2), 2)

    def test_multi_select_keeps_original_indices_across_filtering(self):
        keys = ['\t', '/', 'b', '\t', '\x1b', '\r']
        with patch('cenima.picker.read_key', side_effect=keys):
            self.assertEqual(self.menu.pick(['alpha', 'beta', 'gamma'], str, multi=True), [0, 1])

    def test_filter_treats_brackets_and_shortcut_letters_as_text(self):
        with patch('cenima.picker.read_key', side_effect=['/', '[', 'r', '\r']):
            self.assertEqual(self.menu.pick(['[r]', 'other'], str, shortcuts={'r': 1}), 0)

    def test_ctrl_c_works_while_filtering(self):
        with patch('cenima.picker.read_key', side_effect=['/', '\x03']):
            with self.assertRaises(KeyboardInterrupt):
                self.menu.pick(['first'], str)

    def test_retry_action_targets_highlighted_object(self):
        job = {'id': 'one'}
        with patch('cenima.picker.read_key', side_effect=['r']):
            result = self.menu.pick([job], str, shortcuts={'r': 'retry'})
        self.assertEqual(result.action, 'retry')
        self.assertIs(result.item, job)

    def test_refresh_updates_progress_and_preserves_identity_after_reorder(self):
        first = [{'id': 'a', 'progress': '10%'}, {'id': 'b', 'progress': '20%'}]
        refreshed = [{'id': 'b', 'progress': '75%'}, {'id': 'a', 'progress': '90%'}]
        # Advance only the menu's refresh clock, leaving Rich's timing alone.
        clock = Mock()
        clock.monotonic.side_effect = [0, 0, 1, 1]
        with patch('cenima.picker.time', clock), patch(
            'cenima.picker.read_key', side_effect=[None, '\r']
        ):
            result = self.menu.pick(first, lambda job: job['progress'], initial_index=1,
                                    refresh_items=lambda: refreshed, item_key=lambda job: job['id'])
        self.assertEqual(result, refreshed[0])
        self.assertIn('75%', self.console.file.getvalue())

    def test_empty_refresh_returns_to_parent(self):
        clock = Mock()
        clock.monotonic.side_effect = [0, 1, 1]
        with patch('cenima.picker.time', clock):
            self.assertIsNone(self.menu.pick(['one'], str, refresh_items=lambda: []))

    def test_column_header_reserves_a_line_in_short_terminal(self):
        frames = []
        real_live = picker.Live

        def capture(*args, **kwargs):
            display = real_live(*args, **kwargs)
            update = display.update

            def record(renderable, **options):
                lines = self.console.render_lines(renderable, self.console.options)
                frames.append(len(lines))
                return update(renderable, **options)

            display.update = record
            return display

        with patch('cenima.picker.Live', side_effect=capture), patch(
            'cenima.picker.read_key', side_effect=[None, '\x1b']
        ):
            self.menu.pick(list(range(50)), str, title='Downloads',
                           subtitle='Esc: back', header=lambda: 'Title  Status  Progress')
        self.assertTrue(frames)
        self.assertTrue(all(height <= self.console.height for height in frames))
        self.assertIn('Title  Status  Progress', self.console.file.getvalue())


class PosixKeyDecodingTests(unittest.TestCase):
    def test_unicode_and_arrow_do_not_consume_the_next_key(self):
        pending = bytearray('عé\x1b[B\r'.encode('utf-8'))

        def read(fd, count):
            result = bytes(pending[:count])
            del pending[:count]
            return result

        with patch.object(picker, '_WINDOWS', False), patch.object(
            picker.os, 'read', side_effect=read
        ), patch.object(picker.select, 'select', side_effect=lambda *args: ([0] if pending else [], [], [])):
            self.assertEqual([picker.read_key(0) for _ in range(4)], ['ع', 'é', '\x1b[B', '\r'])


class SettingsRoundtripTests(unittest.TestCase):
    def test_no_subtitle_preference_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, 'CONFIG_DIR', Path(directory)), patch.object(
            cli, 'CONFIG_FILE', Path(directory) / 'config.json'
        ):
            config = cli.Config()
            config.subtitle_language = ''
            self.assertTrue(config.save())
            self.assertEqual(cli.Config().subtitle_language, '')
