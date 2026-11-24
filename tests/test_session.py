import io
import unittest
from unittest.mock import patch
from rich.console import Console
from cenima.cli import main


class SessionTests(unittest.TestCase):
    def test_control_c_restores_screen_and_shuts_down_background_worker(self):
        from unittest.mock import Mock
        output = io.StringIO()
        console = Console(file=output, force_terminal=True, legacy_windows=False,
                          _environ={'TERM': 'xterm-256color'})
        worker = Mock()
        class App:
            _download_worker = worker
            async def search_flow(self):
                raise KeyboardInterrupt()
            def goodbye(self):
                pass
        with patch('cenima.cli.CONSOLE', console), patch('cenima.cli.CinemaCLI', App):
            main([])
        self.assertIn('\x1b[?1049l', output.getvalue())
        worker.shutdown.assert_called_once()
    def test_session_owns_one_alternate_buffer_and_restores_on_error(self):
        for fail in (False, True):
            output = io.StringIO()
            console = Console(file=output, force_terminal=True, legacy_windows=False,
                              _environ={"TERM": "xterm-256color"})

            class App:
                async def search_flow(self):
                    console.print('Current screen')
                    if fail:
                        raise RuntimeError('test')

            with patch('cenima.cli.CONSOLE', console), patch('cenima.cli.CinemaCLI', App):
                if fail:
                    with self.assertRaises(RuntimeError):
                        main([])
                else:
                    main([])
            self.assertEqual(output.getvalue().count('\x1b[?1049h'), 1)
            self.assertEqual(output.getvalue().count('\x1b[?1049l'), 1)

    def test_help_and_version_do_not_construct_application(self):
        for flag in ('--help', '--version'):
            with patch('cenima.cli.CinemaCLI') as app, patch('sys.stdout', io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    main([flag])
                self.assertEqual(stopped.exception.code, 0)
                app.assert_not_called()
