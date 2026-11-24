"""Presentation bounds and Windows keyboard behavior without live provider calls."""
from collections import deque
from contextlib import ExitStack
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console, Group
from rich.cells import cell_len

import cenima.picker as picker
from cenima.ui import (PALETTE, app_bar, breadcrumb, details_card, landing,
                       picker_view, result_row, callout)
from cenima.scraper import clean_show_title


DETAILS = {
    'title': 'Dune: Part Two', 'type': 'movie', 'year': 2024, 'imdb_rating': 8.5,
    'quality': '1080p WEB-DL', 'genres': ['Adventure', 'Science fiction'],
    'synopsis': 'Paul Atreides unites with Chani and the Fremen. ' * 12,
}


class CaptureLive:
    frames = []
    options = []

    def __init__(self, *args, **kwargs):
        self.console = kwargs['console']
        self.options.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def update(self, renderable, **kwargs):
        with self.console.capture() as capture:
            self.console.print(renderable)
        self.frames.append(capture.get())


class PresentationTests(unittest.TestCase):
    def render(self, renderable, width, height=24):
        console = Console(file=io.StringIO(), width=width, height=height, color_system=None)
        with console.capture() as capture:
            console.print(renderable)
        output = capture.get()
        self.assertTrue(all(cell_len(line) <= width for line in output.splitlines()))
        return output

    def test_responsive_results_and_detail_cards(self):
        for width in (80, 100, 160):
            with self.subTest(width=width):
                rows = [(i, result_row(DETAILS, DETAILS['title'] + ' 世界' * 10)) for i in range(5)]
                screen = Group(app_bar('0.2'), breadcrumb(['Home', 'Search: "Dune"']),
                    details_card(DETAILS, DETAILS['title'], width),
                    picker_view(rows, 0, 0, 5, 'Search results', 'Choose a title', '', False, set(), False))
                output = self.render(screen, width)
                self.assertLessEqual(len(output.splitlines()), 24)
                for label in ('Title', 'Type', 'Servers', '[Page 1/1]', '[Enter]', '[Esc]'):
                    self.assertIn(label, output)

    def test_metadata_unknowns_and_catalog_text_are_honest_and_literal(self):
        output = self.render(details_card(DETAILS, 'Film [/red] [bold]title', 80), 80)
        self.assertIn('Not listed', output)
        self.assertIn('[/red]', output)
        self.assertEqual(result_row(DETAILS, 'Dune').cells[-1], 'On demand')
        self.assertEqual(result_row({**DETAILS, 'servers': []}, 'Dune').cells[-1], '0')

    def test_landing_and_error_panels_fit(self):
        for width in (60, 80, 120, 160):
            output = self.render(landing('0.2', True, [], width), width)
            self.assertIn('MPV', output)
            self.assertIn('v0.2', output)
            self.assertIn('Direct Terminal Cinema Experience', output)
            self.assertIn('[/d]', output)
            self.assertIn('DISCOVER', output)
            self.assertIn('LIBRARY', output)
            self.assertIn('MANAGE', output)
            self.assertIn('SYSTEM', output)
            custom_tip = self.render(landing('0.2', True, [], 120, tip='Custom tip'), 120)
            self.assertIn('Custom tip', custom_tip)
            self.render(callout('CONNECTION FAILED', 'Timeout. Retry or go back.', 'error'), width)

    def test_arabic_title_cleaning_keeps_title_and_removes_catalog_wrappers(self):
        self.assertEqual(clean_show_title('فيلم رحلة إلى القمر مترجم اون لاين'), 'رحلة إلى القمر')
        self.assertIn('Part Two', clean_show_title('فيلم Dune: Part Two 2024 مترجم'))


class WindowsKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.console = Console(file=io.StringIO(), width=80, height=24, color_system=None)
        CaptureLive.frames = []
        CaptureLive.options = []

    def keyboard(self, codes):
        codes = deque(codes)
        api = SimpleNamespace(kbhit=lambda: bool(codes), getwch=lambda: codes.popleft())
        stack = ExitStack()
        stack.enter_context(patch.object(picker, 'msvcrt', api, create=True))
        stack.enter_context(patch.object(picker, 'read_key', side_effect=lambda *a, **k: picker._read_key_windows(0)))
        stack.enter_context(patch.object(picker, 'raw_terminal'))
        stack.enter_context(patch.object(picker, 'Live', CaptureLive))
        return stack

    def test_windows_arrows_home_end_and_paging(self):
        with self.keyboard(['\xe0', 'O', '\xe0', 'G', '\xe0', 'Q', '\xe0', 'I', '\xe0', 'P', '\r']):
            result = picker.RichPicker(self.console, PALETTE).pick(list(range(40)))
        self.assertEqual(result, 1)
        self.assertTrue(any('[Page 2/' in frame for frame in CaptureLive.frames))
        self.assertFalse(CaptureLive.options[-1]['screen'])

    def test_filter_then_arrow_keeps_original_indices(self):
        with self.keyboard(['/', 'b', '\xe0', 'P', '\r']):
            result = picker.RichPicker(self.console, PALETTE).pick(['alpha', 'beta', 'gamma', 'bravo'])
        self.assertEqual(result, 3)

    def test_escape_clears_filter_then_cancels(self):
        with self.keyboard(['/', 'z', '\x1b', '\x1b']):
            self.assertIsNone(picker.RichPicker(self.console, PALETTE).pick(['alpha']))
        self.assertTrue(any('No matches' in frame for frame in CaptureLive.frames))

    def test_b_and_uppercase_b_go_back_in_single_and_multi_pickers(self):
        for key in ('b', 'B'):
            for multi in (False, True):
                with self.keyboard([key]):
                    view = picker.RichPicker(self.console, PALETTE)
                    self.assertIsNone(view.pick_many(['one']) if multi else view.pick(['one']))

    def test_b_is_text_inside_filters_and_search_or_folder_inputs(self):
        with self.keyboard(['/', 'B', '\r']):
            self.assertEqual(picker.RichPicker(self.console, PALETTE).pick(['alpha', 'bravo']), 1)
        with self.keyboard(list('Bob') + ['\r']):
            self.assertEqual(picker.CustomPrompt(self.console, PALETTE).ask(), 'Bob')
        with self.keyboard(list('Library') + ['\r']):
            self.assertEqual(picker.CustomPrompt(self.console, PALETTE).ask(title='MEDIA FOLDER'), 'Library')

    def test_multi_selection_persists_across_filter(self):
        with self.keyboard(['\t', '/', 'b', '\x1b', '\xe0', 'P', '\t', '\r']):
            selected = picker.RichPicker(self.console, PALETTE).pick_many(['alpha', 'beta', 'gamma'])
        self.assertEqual(selected, [0, 1])

    def test_windows_prompt_editing_and_unicode(self):
        with self.keyboard(list('Dune') + ['\xe0', 'G', '\xe0', 'S', 'ف', '\xe0', 'O', '!', '\r']):
            query = picker.CustomPrompt(self.console, PALETTE).ask()
        self.assertEqual(query, 'فune!')

    def test_history_restores_draft(self):
        with self.keyboard(list('new') + ['\xe0', 'H', '\xe0', 'P', '\r']):
            self.assertEqual(picker.CustomPrompt(self.console, PALETTE).ask(history=['old']), 'new')

    def test_slash_is_typed_at_welcome_prompt_not_treated_as_a_shortcut(self):
        welcome_actions = [
            {'key': '/', 'command': ''},
            {'key': 'd', 'command': '/downloads'},
        ]
        with self.keyboard(['/'] + list('d') + ['\r']):
            result = picker.CustomPrompt(self.console, PALETTE).ask(
                welcome_actions=welcome_actions,
                welcome_state={'selected': 0},
            )
        self.assertEqual(result, '/d')

    def test_control_c_exits_picker_and_prompt(self):
        for operation in (lambda: picker.RichPicker(self.console, PALETTE).pick(['item']),
                          lambda: picker.CustomPrompt(self.console, PALETTE).ask()):
            with self.keyboard(['\x03']), self.assertRaises(KeyboardInterrupt):
                operation()

    def test_resize_preserves_selection_and_keeps_footer_in_bounds(self):
        sizes = [(160, 40), (80, 24), (60, 18), (100, 30)]
        position = [0]
        snapshots = []

        def resize(*args, **kwargs):
            snapshots.append((self.console.width, self.console.height, CaptureLive.frames[-1]))
            position[0] += 1
            if position[0] == len(sizes):
                return '\r'
            self.console.width, self.console.height = sizes[position[0]]
            return None

        self.console.width, self.console.height = sizes[0]
        with patch.object(picker, 'raw_terminal'), patch.object(picker, 'Live', CaptureLive), \
                patch.object(picker, 'read_key', side_effect=resize):
            self.assertEqual(picker.RichPicker(self.console, PALETTE,
                context=lambda: details_card(DETAILS, 'فيلم عربي', self.console.width)).pick(
                    list(range(100)), initial_index=40), 40)
        for width, height, frame in snapshots:
            self.assertLessEqual(len(frame.splitlines()), height)
            self.assertTrue(all(cell_len(line) <= width for line in frame.splitlines()))
            self.assertIn('[Esc]', frame)

    def test_dynamic_download_rows_refresh_while_input_is_open(self):
        count = [0]
        def source():
            count[0] += 1
            return [{'status': 'Complete' if count[0] > 2 else 'Queued'}]
        with self.keyboard(['\xe0', 'P', '\r']):
            selected = picker.RichPicker(self.console, PALETTE).pick(source, lambda job: job['status'])
        self.assertEqual(selected['status'], 'Complete')

    def test_retry_shortcuts_are_case_insensitive_and_preserve_filter_typing(self):
        job = {'id': 'failed-job'}
        for key in ('r', 'R'):
            with self.keyboard([key]):
                result = picker.RichPicker(self.console, PALETTE, shortcuts={'r': 'retry'}).pick(
                    lambda: [job], lambda item: 'Retry example')
            self.assertIsInstance(result, picker.PickerAction)
            self.assertEqual((result.action, result.item), ('retry', job))
        with self.keyboard(['/', 'r', '\r']):
            result = picker.RichPicker(self.console, PALETTE, shortcuts={'r': 'retry'}).pick(
                lambda: [job], lambda item: 'Retry example')
        self.assertIs(result, job)
        with self.keyboard(['R']):
            self.assertEqual(picker.RichPicker(self.console, PALETTE, shortcuts={'r': 0}).pick(
                ['Retry item', 'Back'], initial_index=1), 0)

    def test_fifteen_latest_rows_scroll_inside_an_80_by_24_picker(self):
        rows = [picker.PickerRow((f'Show {i}', 'S01E01', '1080p'), ('Title', 'Episode', 'Quality')) for i in range(15)]
        with self.keyboard(['\xe0', 'Q', '\r']):
            selected = picker.RichPicker(self.console, PALETTE).pick(rows, lambda row: row,
                title='Latest episodes', subtitle='15 distinct shows')
        self.assertGreater(selected, 0)
        self.assertTrue(any('[Page 2/2]' in frame for frame in CaptureLive.frames))
        self.assertTrue(all(len(frame.splitlines()) <= 24 for frame in CaptureLive.frames))
