"""Exercise Live downloads in a real terminal with fixture jobs, without network.

Windows requires pywinpty in the harness interpreter. POSIX uses stdlib pty.
--python selects the application interpreter independently of the harness.
"""
import argparse
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
STEPS = [('10.0%', None), ('75.0%', '\x1b'), ('DOWNLOADS_EXITED', '')]


def posix(python):
    import fcntl
    import pty
    import select
    import struct
    import termios

    master, slave = pty.openpty()
    process = None
    started = time.monotonic()
    transcript = ''
    pending = list(STEPS)
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 12, 70, 0, 0))
        process = subprocess.Popen([python, str(ROOT / 'tools/windows_terminal_smoke.py'),
            '--child', 'downloads'], stdin=slave, stdout=slave, stderr=slave,
            cwd=str(ROOT), start_new_session=True, env={**os.environ, 'TERM': 'xterm-256color'})
        os.close(slave)
        slave = None
        while time.monotonic() - started < 25:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                transcript = (transcript + chunk.decode('utf-8', errors='replace'))[-40000:]
            plain = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', transcript)
            while pending and pending[0][0] in plain:
                _, keys = pending.pop(0)
                if keys:
                    os.write(master, keys.encode('utf-8'))
                elif keys == '':
                    return {'scenario': 'downloads', 'passed': True,
                            'seconds': round(time.monotonic() - started, 2)}
            if process.poll() is not None:
                break
        raise RuntimeError('Live download terminal scenario failed: ' + repr(transcript[-2000:]))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        os.close(master)
        if slave is not None:
            os.close(slave)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    if os.name == 'nt':
        harness = runpy.run_path(str(ROOT / 'tools/windows_terminal_smoke.py'))
        result = harness['exercise'](args.python, 'downloads', STEPS)
    else:
        result = posix(args.python)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
