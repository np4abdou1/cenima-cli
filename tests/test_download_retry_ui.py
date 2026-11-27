import io
import unittest
from unittest.mock import Mock, patch
from rich.console import Console
from rich.text import Text
from rich.cells import cell_len
from cenima.cli import CinemaCLI
from cenima.picker import PickerAction


class RetryUiTests(unittest.TestCase):
    def setUp(self):
        self.cli = object.__new__(CinemaCLI)
        self.cli.config = Mock()
        self.cli.config.get_theme.return_value = {}
        self.cli._breadcrumbs = ['Home', 'Downloads']
        self.cli._media_store = Mock()
        self.cli._download_worker = Mock()
        self.job = {'id': 'one', 'download_title': 'Show S01E06', 'status': 'failed',
                    'error': 'HTTP Error 403: Forbidden', 'progress': ''}
        self.cli._media_store.jobs.return_value = [self.job]
        self.console = patch('cenima.cli.CONSOLE', Console(file=io.StringIO()))
        self.console.start()
        self.addCleanup(self.console.stop)

    def test_selected_failure_immediately_offers_retry_without_forced_error_page(self):
        self.cli.run_fzf = Mock(return_value=0)
        with patch('cenima.cli.RichPicker') as picker:
            picker.return_value.pick.side_effect = [self.job, None]
            self.cli.show_live_downloads()
        self.assertEqual(self.cli.run_fzf.call_args.args[0], ['Retry item', 'View error', 'Back'])
        self.assertEqual(self.cli.run_fzf.call_args.kwargs['shortcuts'], {'r': 0})
        self.cli._media_store.retry.assert_called_once_with('one')
        self.cli._download_worker.start.assert_called_once()

    def test_r_on_highlighted_job_retries_directly_without_opening_details(self):
        self.cli.run_fzf = Mock()
        with patch('cenima.cli.RichPicker') as picker:
            picker.return_value.pick.side_effect = [PickerAction('retry', self.job), None]
            self.cli.show_live_downloads()
        self.cli.run_fzf.assert_not_called()
        self.cli._media_store.retry.assert_called_once_with('one')
        self.cli._download_worker.start.assert_called_once()

    def test_retry_checks_current_state_and_never_requeues_active_or_completed_jobs(self):
        for status in ('running', 'pending', 'completed', 'delegated'):
            self.job['status'] = status
            self.assertFalse(self.cli._retry_download('one'))
        self.cli._media_store.retry.assert_not_called()
        self.cli._download_worker.start.assert_not_called()

    def test_actions_use_latest_status_after_picker_refresh(self):
        completed = dict(self.job, status='completed', error='')
        self.cli._media_store.jobs.side_effect = [[self.job], [completed], [completed]]
        self.cli.run_fzf = Mock(return_value=None)
        with patch('cenima.cli.RichPicker') as picker:
            picker.return_value.pick.side_effect = [self.job, None]
            self.cli.show_live_downloads()
        self.assertEqual(self.cli.run_fzf.call_args.args[0], ['Back'])
        self.cli._media_store.retry.assert_not_called()

    def test_live_downloads_supply_refresh_and_retry_to_picker(self):
        with patch('cenima.cli.RichPicker') as picker:
            picker.return_value.pick.return_value = None
            self.cli.show_live_downloads()
        arguments = picker.return_value.pick.call_args.kwargs
        self.assertEqual(arguments['shortcuts'], {'r': 'retry'})
        self.assertEqual(arguments['refresh_items'], self.cli._media_store.jobs)

    def test_narrow_download_row_keeps_status_and_percentage_visible(self):
        job = dict(self.job, download_title='A very long episode title ' * 10,
                   status='running', progress='75% 2MB/s ETA 01:00')
        with patch('cenima.cli.CONSOLE', Console(file=io.StringIO(), width=40)):
            row = self.cli._download_row(job)
        self.assertIn('75.0%', row)
        self.assertIn('Active', row)
        self.assertLessEqual(len(row), 40)

    def test_columns_align_and_percentage_is_not_repeated(self):
        with patch('cenima.cli.CONSOLE', Console(file=io.StringIO(), width=120)):
            rows = [Text.from_markup(self.cli._download_row(dict(
                self.job, download_title=title, status='running',
                progress='13.5% 297.72KiB/s ETA 53:22'))).plain
                for title in ('Short', 'A long episode title S01E05', 'فيلم 🎬 [red]')]
            header = Text.from_markup(self.cli._download_row()).plain
        offsets = [cell_len(row.partition('Active')[0]) for row in rows]
        self.assertEqual(len(set(offsets)), 1)
        self.assertEqual(offsets[0], cell_len(header.partition('Status')[0]))
        for row in rows:
            self.assertEqual(row.count('%'), 1)
            self.assertIn('297.72KiB/s', row)
            self.assertIn('ETA 53:22', row)
            self.assertLessEqual(cell_len(row), 112)

    def test_completed_row_keeps_one_percentage_and_no_solid_blocks(self):
        with patch('cenima.cli.CONSOLE', Console(file=io.StringIO(), width=110)):
            row = Text.from_markup(self.cli._download_row(dict(
                self.job, status='completed', progress='100% · Complete'))).plain
        self.assertEqual(row.count('%'), 1)
        self.assertIn('Complete', row)
        self.assertNotIn('█', row)

    def test_live_view_initially_selects_active_job_after_completed_items(self):
        self.cli._media_store.jobs.return_value = [
            dict(self.job, id='done', status='completed'),
            dict(self.job, id='active', status='running')]
        with patch('cenima.cli.RichPicker') as picker:
            picker.return_value.pick.return_value = None
            self.cli.show_live_downloads()
        self.assertEqual(picker.return_value.pick.call_args.kwargs['initial_index'], 1)
        self.assertTrue(callable(picker.return_value.pick.call_args.kwargs['header']))

    def test_r_from_optional_diagnostics_retries_same_job(self):
        self.cli.run_fzf = Mock(side_effect=[1, PickerAction('retry', None)])
        with patch('cenima.cli.RichPicker') as picker:
            picker.return_value.pick.side_effect = [self.job, None]
            self.cli.show_live_downloads()
        self.assertEqual(self.cli.run_fzf.call_args.kwargs['title'], 'Transfer failure')
        self.cli._media_store.retry.assert_called_once_with('one')
