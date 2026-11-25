"""Platform input contracts shared by the prompt and fallback picker."""
import io
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from cenima import picker


class WindowsInputTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch.object(picker, '_WINDOWS', True)
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.console_input = Mock()
        self.console_input.kbhit.return_value = True
        self.backend = patch.object(picker, 'msvcrt', self.console_input, create=True)
        self.backend.start()
        self.addCleanup(self.backend.stop)

    def test_raw_terminal_preserves_body_exception_without_posix_calls(self):
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            with picker.raw_terminal(0) as fd:
                self.assertEqual(fd, 0)
                raise RuntimeError('cancelled')

    def test_unicode_and_control_characters_are_preserved(self):
        for key in ('é', 'ع', '/', '\r', '\b', '\x1b', '\x03'):
            with self.subTest(key=key):
                self.console_input.getwch.return_value = key
                self.assertEqual(picker.read_key(0), key)

    def test_navigation_keys_use_the_existing_picker_sequences(self):
        for prefix in ('\x00', '\xe0'):
            for code, expected in (
                ('H', '\x1b[A'), ('P', '\x1b[B'),
                ('K', '\x1b[D'), ('M', '\x1b[C'),
                ('I', '\x1b[5~'), ('Q', '\x1b[6~'),
                ('G', '\x1b[H'), ('O', '\x1b[F'),
                ('S', '\x1b[3~'),
            ):
                with self.subTest(prefix=prefix, code=code):
                    self.console_input.getwch.side_effect = [prefix, code]
                    self.assertEqual(picker.read_key(0), expected)

    def test_unknown_extended_key_is_consumed(self):
        self.console_input.getwch.side_effect = ['\x00', ';', 'x']
        self.assertIsNone(picker.read_key(0))
        self.assertEqual(picker.read_key(0), 'x')

    def test_idle_input_times_out_without_a_blocking_read(self):
        self.console_input.kbhit.return_value = False
        with patch.object(picker.time, 'monotonic', side_effect=[1.0, 1.0, 1.02]), patch.object(
            picker.time, 'sleep'
        ) as sleep:
            self.assertIsNone(picker.read_key(0, timeout=0.02))
        self.console_input.getwch.assert_not_called()
        sleep.assert_called_once()

    def test_prompt_accepts_unicode_backspace_and_history_navigation(self):
        self.console_input.getwch.side_effect = ['\xe0', 'H', '\b', 'é', 'ع', '\r']
        prompt = picker.CustomPrompt(Console(file=io.StringIO()), {'primary': 'cyan'})
        self.assertEqual(prompt.ask(history=['ab']), 'aéع')

    def test_fallback_picker_navigates_and_selects(self):
        self.console_input.getwch.side_effect = ['\xe0', 'P', '\r']
        menu = picker.RichPicker(Console(file=io.StringIO()), {'primary': 'cyan', 'accent': 'blue'})
        self.assertEqual(menu.pick(['first', 'second'], str), 1)

    def test_fallback_picker_cancels_with_escape(self):
        self.console_input.getwch.side_effect = ['\x1b']
        menu = picker.RichPicker(Console(file=io.StringIO()), {'primary': 'cyan', 'accent': 'blue'})
        self.assertIsNone(menu.pick(['first'], str))


@unittest.skipIf(picker._WINDOWS, 'Requires POSIX terminal modules')
class PosixInputTests(unittest.TestCase):
    def test_terminal_settings_are_restored_after_an_exception(self):
        with patch.object(picker.termios, 'tcgetattr', return_value=['original']), patch.object(
            picker.termios, 'tcsetattr'
        ) as restore, patch.object(picker.tty, 'setraw') as setraw:
            with self.assertRaises(RuntimeError):
                with picker.raw_terminal(7):
                    raise RuntimeError('cancelled')
            setraw.assert_called_once_with(7)
            restore.assert_called_once_with(7, picker.termios.TCSADRAIN, ['original'])


if __name__ == '__main__':
    unittest.main()
