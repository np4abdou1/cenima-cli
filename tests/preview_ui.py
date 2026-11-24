"""Offline visual preview: python tests/preview_ui.py [--interactive]."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rich.console import Console, Group
from cenima.config import __version__
from cenima.picker import RichPicker, CustomPrompt
from cenima.ui import (PALETTE, app_bar, breadcrumb, details_card, landing,
                       picker_view, result_row)

DETAILS = {'title': 'Dune: Part Two', 'type': 'movie', 'year': 2024,
           'quality': '1080p WEB-DL', 'imdb_rating': 8.5,
           'genres': ['Adventure', 'Science fiction'],
           'synopsis': 'Paul Atreides unites with Chani and the Fremen while seeking revenge '
                       'against the conspirators who destroyed his family.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--output', default='.packaging-check/ui')
    args = parser.parse_args()
    if args.interactive:
        console = Console()
        rows = [result_row({**DETAILS, 'year': 2024 - i}, f'Dune · Edition {i + 1}') for i in range(30)]
        selected = RichPicker(console, PALETTE, breadcrumbs=['Home', 'Search: "Dune"'],
                              context=details_card(DETAILS, DETAILS['title'], console.width)).pick(rows,
                                                                                               title='Search results')
        query = CustomPrompt(console, PALETTE).ask(history=['Dune'])
        print(f'WINDOWS_UI_OK selected={selected} query={query}')
        return
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    for width in (80, 160):
        console = Console(width=width, record=True, color_system='truecolor')
        console.print(landing(__version__, True, [], width))
        console.save_svg(str(directory / f'landing-{width}.svg'), title='CINEMA / Discover')
        rows = [(i, result_row({**DETAILS, 'year': 2024 - i}, f'Dune · Edition {i + 1}')) for i in range(5)]
        console.print(Group(app_bar(__version__), breadcrumb(['Home', 'Search: "Dune"']),
            details_card(DETAILS, DETAILS['title'], width),
            picker_view(rows, 1, 0, 5, 'Search results', 'Choose a title to explore', '', False, set(), False)))
        console.save_svg(str(directory / f'results-{width}.svg'), title='CINEMA / Search results')


if __name__ == '__main__':
    main()
