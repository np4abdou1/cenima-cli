"""Real Windows ConPTY checks; run with a Python that has optional pywinpty.

Example: python tools/windows_terminal_smoke.py --python C:/Python314/python.exe
The application interpreter needs the project dependencies, not pywinpty.
Scenarios cover prompt input, fzf and built-in menus, batch selection, saved
selection, retry shortcuts, live download refresh, and a fixture search flow.
All state is temporary and there are no provider requests or real transfers.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def child(mode):
    sys.path.insert(0, str(ROOT))
    import cenima.cli as cli
    from cenima.history import HistoryManager

    cli.configure_utf8_terminal()
    if mode == 'prompt':
        value = cli.CustomPrompt(cli.CONSOLE, cli.THEMES['blue']).ask('Input')
        print('PROMPT_RESULT=' + json.dumps(value), flush=True)
        time.sleep(0.5)
        return
    if mode in ('fzf', 'fallback', 'fzf_initial', 'fzf_retry', 'multi'):
        app = object.__new__(cli.CinemaCLI)
        app.config = type('Config', (), {'get_theme': lambda self: cli.THEMES['blue']})()
        options = {'initial_index': 1} if mode == 'fzf_initial' else {'shortcuts': {'r': 1}} if mode == 'fzf_retry' else {'multi': True} if mode == 'multi' else {}
        with patch('cenima.cli.shutil.which', return_value=None) if mode in ('fallback', 'multi') else ExitStack():
            result = app.run_fzf(['First 🎬 عربي', 'Second français'], title='SMOKE_MENU', **options)
        print('MENU_RESULT=' + str(result), flush=True)
        time.sleep(0.5)
        return
    if mode == 'downloads':
        app = object.__new__(cli.CinemaCLI)
        app.config = type('Config', (), {'get_theme': lambda self: cli.THEMES['blue']})()
        started = time.monotonic()

        class Store:
            def jobs(self):
                percentage = '10%' if time.monotonic() - started < 2 else '75%'
                return [{'id': str(i), 'download_title': 'A long episode title ' * 5 + str(i),
                         'status': 'running', 'error': '', 'progress': percentage}
                        for i in range(20)]

        app._media_store = Store()
        app.show_live_downloads()
        print('DOWNLOADS_EXITED', flush=True)
        time.sleep(0.5)
        return

    class FixtureCatalogue:
        def search(self, query):
            return [
                {'title': 'Hijack عربي', 'type': 'series', 'url': 'https://fixture.test/one', 'metadata': {}},
                {'title': 'Hijack français', 'type': 'movie', 'url': 'https://fixture.test/two', 'metadata': {}},
                {'title': 'Hijack English', 'type': 'series', 'url': 'https://fixture.test/three', 'metadata': {}},
            ]

    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        directory = Path(directory)
        stack.enter_context(patch.object(cli, 'CONFIG_DIR', directory))
        stack.enter_context(patch.object(cli, 'CONFIG_FILE', directory / 'config.json'))
        stack.enter_context(patch.object(cli, 'HistoryManager', lambda: HistoryManager(directory / 'history.json')))
        stack.enter_context(patch.object(cli, 'get_github_stars', return_value=0))
        stack.enter_context(patch.object(cli, 'scraper', FixtureCatalogue()))
        try:
            cli.main([])
        except SystemExit as stopped:
            if stopped.code not in (0, None):
                raise
        print('FLOW_EXITED', flush=True)
        time.sleep(0.5)


def exercise(python, mode, steps):
    import winpty

    columns, rows = (70, 12) if mode == 'downloads' else (35, 10) if mode == 'welcome' else (110, 35)
    terminal = winpty.PTY(columns, rows, backend=winpty.Backend.ConPTY)
    arguments = subprocess.list2cmdline([str(Path(__file__).resolve()), '--child', mode])
    environment = {key: value for key, value in os.environ.items() if key != 'TERM'}
    with patch.dict(os.environ, environment, clear=True):
        terminal.spawn(python, arguments, cwd=str(ROOT))
    output = ''
    transcript = ''
    win32_input = False
    pending = list(steps)
    started = time.monotonic()
    deadline = started + 30
    try:
        while time.monotonic() < deadline:
            chunk = terminal.read(False)
            if chunk:
                output = (output + chunk)[-20000:]
                transcript = (transcript + chunk)[-40000:]
                win32_input = win32_input or '\x1b[?9001h' in transcript
            plain = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
            if pending and pending[0][0] in plain:
                marker, keys = pending.pop(0)
                output = ''
                if keys is None:
                    continue
                elif keys:
                    time.sleep(0.2)
                    # Separate key events so startup rendering cannot consume
                    # the following Enter before the child is ready.
                    tokens = re.findall(r'\x1b\[[A-D]|.', keys, flags=re.DOTALL)
                    for token in tokens:
                        if win32_input:
                            # ConPTY requests Win32 input records. These are
                            # needed when a child enables VT console input.
                            special = {'\x1b[A': (38, 72, 0), '\x1b[B': (40, 80, 0),
                                       '\r': (13, 28, 13), '\b': (8, 14, 8), '\x1b': (27, 1, 27)}
                            if token in special:
                                vk, scan, char = special[token]
                            else:
                                vk = ord(token.upper()) if len(token.upper()) == 1 else 0
                                scan, char = 0, ord(token)
                            terminal.write(f'\x1b[{vk};{scan};{char};1;0;1_')
                            terminal.write(f'\x1b[{vk};{scan};{char};0;0;1_')
                        else:
                            terminal.write(token)
                        time.sleep(0.03)
                else:
                    return {'scenario': mode, 'passed': True,
                            'seconds': round(time.monotonic() - started, 2)}
            if not terminal.isalive():
                break
            if not chunk:
                time.sleep(0.005)
        raise RuntimeError('Unreached step: ' + repr(pending[0] if pending else None) + '\n' + repr(transcript[-3000:]))
    finally:
        if terminal.isalive():
            terminal.write('\x03')
            time.sleep(0.15)
        if terminal.isalive():
            subprocess.run(['taskkill', '/PID', str(terminal.pid), '/T', '/F'], capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--child', choices=['prompt', 'fzf', 'fallback', 'fzf_initial', 'fzf_retry', 'multi', 'downloads', 'flow', 'welcome'])
    args = parser.parse_args()
    if args.child:
        child(args.child)
        return
    if os.name != 'nt':
        parser.error('ConPTY checks require Windows')
    scenarios = {
        'prompt': [('Input', 'caféع\b\r'), ('PROMPT_RESULT="caf\\u00e9"', '')],
        'fzf': [('Second', 'Second\r'), ('MENU_RESULT=1', '')],
        'fallback': [('Second', '\x1b[B\r'), ('MENU_RESULT=1', '')],
        'fzf_initial': [('Second', '\r'), ('MENU_RESULT=1', '')],
        'fzf_retry': [('Second', 'r'), ('MENU_RESULT=1', '')],
        'multi': [('Second', '\t\x1b[B\t\r'), ('MENU_RESULT=[0, 1]', '')],
        'downloads': [('10.0%', None), ('75.0%', '\x1b'), ('DOWNLOADS_EXITED', '')],
        'flow': [('Search', 'hija\r'), ('Hijack', '\x1b'), ('Search', 'exit\r')],
        'welcome': [('Search', 'x' * 45), ('xxxxx', '\x1b'), ('Search', 'exit\r'), ('FLOW_EXITED', '')],
    }
    for mode, steps in scenarios.items():
        if mode == 'flow':
            # The application exits via SystemExit, so use a visible goodbye
            # marker as the final assertion rather than a fixture return value.
            steps.append(('FLOW_EXITED', ''))
        print(json.dumps(exercise(args.python, mode, steps)), flush=True)


if __name__ == '__main__':
    main()
