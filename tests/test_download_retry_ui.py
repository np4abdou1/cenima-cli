import io
import unittest
from unittest.mock import Mock, patch
from rich.console import Console
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
        self.assertIn('75%', row)
        self.assertIn('Active', row)
        self.assertLessEqual(len(row), 40)

    def test_r_from_optional_diagnostics_retries_same_job(self):
        self.cli.run_fzf = Mock(side_effect=[1, PickerAction('retry', None)])
        with patch('cenima.cli.RichPicker') as picker:
            picker.return_value.pick.side_effect = [self.job, None]
            self.cli.show_live_downloads()
        self.assertEqual(self.cli.run_fzf.call_args.kwargs['title'], 'Transfer failure')
        self.cli._media_store.retry.assert_called_once_with('one')
