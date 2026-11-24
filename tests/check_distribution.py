"""Run using a fresh environment containing the built wheel, from any directory."""
import configparser
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def main():
    artifacts = Path(sys.argv[1]).resolve()
    wheel, = artifacts.glob('cenima_cli-*.whl')
    source, = artifacts.glob('cenima_cli-*.tar.gz')
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert 'cenima/py.typed' in names
        assert not any('tests/' in n or n.endswith('.bak') or '__pycache__' in n for n in names)
        entrypoints, = (n for n in names if n.endswith('.dist-info/entry_points.txt'))
        config = configparser.ConfigParser()
        config.read_string(archive.read(entrypoints).decode())
        assert dict(config['console_scripts']) == {
            'cenima': 'cenima.cli:main', 'cenima-cli': 'cenima.cli:main',
            'cinema-cli': 'cenima.cli:main'}
    with tarfile.open(source) as archive:
        names = archive.getnames()
        for expected in ['tests/fixtures/episodes.html', 'tests/fixtures/seasons_fallback.html',
                         'tests/test_scraper_fixtures.py', 'README.md', 'requirements.txt']:
            assert any(n.endswith('/' + expected) for n in names), expected
        assert not any('.packaging-check/' in n or n.endswith('.bak') for n in names)
    import cenima
    package = Path(cenima.__file__).resolve()
    assert package.is_relative_to(Path(sys.prefix).resolve()), f'Imported checkout instead of wheel: {package}'
    distribution = importlib.metadata.distribution('cenima-cli')
    entries = {entry.name: entry for entry in distribution.entry_points if entry.group == 'console_scripts'}
    assert set(entries) == {'cenima', 'cenima-cli', 'cinema-cli'}
    for entry in entries.values():
        assert callable(entry.load())
    # Exercise generated launchers while stubbing only the interactive application.
    # An isolated sitecustomize avoids scraping, player startup, or user config writes.
    with tempfile.TemporaryDirectory() as directory:
        location = Path(directory)
        (location / 'sitecustomize.py').write_text(
            'import cenima.cli\n'
            'class SmokeCLI:\n'
            '    async def search_flow(self):\n'
            '        print("ENTRYPOINT_OK")\n'
            'cenima.cli.CinemaCLI = SmokeCLI\n', encoding='utf-8')
        environment = os.environ.copy()
        environment['PYTHONPATH'] = directory
        environment['PYTHONUTF8'] = '1'
        scripts = Path(sys.executable).parent
        for name in entries:
            launcher = scripts / (name + '.exe' if os.name == 'nt' else name)
            result = subprocess.run([str(launcher)], cwd=directory, env=environment,
                                    capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
            assert 'ENTRYPOINT_OK' in result.stdout, (name, result.stdout, result.stderr)
            print(f'{name}: generated launcher passed')
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
    print(f'Installed package: {package}')

    import cenima
    assert Path(cenima.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), cenima.__file__
    distribution = importlib.metadata.distribution('cenima-cli')
    for entry in distribution.entry_points:
        if entry.group == 'console_scripts':
            assert entry.load() is cenima.main

    # Exercise the actual installed launchers while keeping the TUI deterministic.
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, 'sitecustomize.py').write_text(
            'import cenima.cli\n'
            'class SmokeCLI:\n'
            '    async def search_flow(self):\n'
            '        print("ENTRYPOINT_OK")\n'
            'cenima.cli.CinemaCLI = SmokeCLI\n', encoding='utf-8')
        environment = os.environ.copy()
        environment['PYTHONPATH'] = directory
        environment['PYTHONIOENCODING'] = 'utf-8'
        scripts = Path(sys.executable).parent
        for name in ['cenima', 'cenima-cli', 'cinema-cli']:
            executable = scripts / (name + '.exe' if os.name == 'nt' else name)
            result = subprocess.run([str(executable)], cwd=directory, env=environment,
                                    capture_output=True, text=True, encoding='utf-8', timeout=20)
            assert result.returncode == 0, result.stderr
            assert 'ENTRYPOINT_OK' in result.stdout, result.stdout
            print(f'{name}: installed launcher passed')
    print('Wheel/source contents and installed imports passed')


if __name__ == '__main__':
    main()
