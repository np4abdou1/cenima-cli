"""Run in a fresh environment with the built wheel installed, outside the checkout."""
import configparser
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile


def main():
    artifacts = Path(sys.argv[1]).resolve()
    wheel, = artifacts.glob('cenima_cli-*.whl')
    source, = artifacts.glob('cenima_cli-*.tar.gz')
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert 'cenima/py.typed' in names
        assert 'cenima/welcome.py' in names
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
                         'tests/test_scraper_fixtures.py', 'README.md', 'requirements.txt',
                         'tools/windows_terminal_smoke.py']:
            assert any(n.endswith('/' + expected) for n in names), expected
        assert not any('.packaging-check/' in n or n.endswith('.bak') for n in names)

    import cenima
    from cenima.welcome import WelcomeScreen
    assert callable(WelcomeScreen)
    package = Path(cenima.__file__).resolve()
    assert package.is_relative_to(Path(sys.prefix).resolve()), f'Imported checkout instead of wheel: {package}'
    distribution = importlib.metadata.distribution('cenima-cli')
    entries = {entry.name: entry for entry in distribution.entry_points if entry.group == 'console_scripts'}
    assert set(entries) == {'cenima', 'cenima-cli', 'cinema-cli'}
    scripts = Path(sys.executable).parent
    for name, entry in entries.items():
        assert callable(entry.load())
        launcher = scripts / (name + '.exe' if os.name == 'nt' else name)
        for flag, expected in [('--help', 'Browse, play and download'), ('--version', 'cenima-cli 0.2')]:
            result = subprocess.run([str(launcher), flag], cwd=artifacts,
                                    capture_output=True, encoding='utf-8', timeout=15)
            assert result.returncode == 0, (name, flag, result.stderr)
            assert expected in result.stdout, (name, flag, result.stdout)
        print(f'{name}: installed help and version passed')
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
    print(f'Installed package: {package}')
    print('Wheel/source contents and installed launchers passed')


if __name__ == '__main__':
    main()
