"""Atomic local queue and managed media index; catalog metadata only, no CDN tokens."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from .models import canonical_content_url


class MediaStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout=10000')
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA journal_mode=WAL')
        try:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, directory TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS media (
                    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, directory TEXT NOT NULL,
                    path TEXT NOT NULL, bytes INTEGER NOT NULL, updated REAL NOT NULL);
            ''')
            columns = {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}
            if 'progress' not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN progress TEXT NOT NULL DEFAULT ''")
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def worker_lock(self):
        """Only the active worker can recover interrupted jobs or change statuses."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.worker.lock').open('a+b') as handle:
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError('The download queue is active in another session.') from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def enqueue(self, metadata, directory):
        fields = ('catalog_url', 'show_url', 'show_title', 'download_title', 'content_type',
                  'season_number', 'season_label', 'season_part', 'episode_number', 'episode_title', 'manager',
                  'quality', 'speed')
        clean = {key: metadata.get(key) for key in fields}
        directory = str(Path(directory).expanduser().resolve())
        identity = hashlib.sha256((canonical_content_url(clean['catalog_url']) + '\0' + os.path.normcase(directory)).encode()).hexdigest()
        now = time.time()
        with self.connect() as db:
            existing_id = next((row['id'] for row in db.execute('SELECT id,metadata,directory FROM jobs')
                if os.path.normcase(row['directory']) == os.path.normcase(directory) and
                canonical_content_url(json.loads(row['metadata'])['catalog_url']) == canonical_content_url(clean['catalog_url'])), None)
            identity = existing_id or identity
            db.execute('INSERT OR IGNORE INTO jobs (id,metadata,directory,status,error,created,updated) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (identity, json.dumps(clean, ensure_ascii=False), directory, 'pending', '', now, now))
            existing = db.execute('SELECT status FROM jobs WHERE id=?', (identity,)).fetchone()
            if existing['status'] in ('failed', 'cancelled'):
                db.execute("UPDATE jobs SET status='pending',error='',progress='' WHERE id=?", (identity,))
            if existing['status'] == 'completed':
                media = db.execute('SELECT path,bytes FROM media WHERE id=?', (identity,)).fetchone()
                try:
                    intact = media is not None and Path(media['path']).is_file() and Path(media['path']).stat().st_size == media['bytes']
                except OSError:
                    intact = False
                if not intact:
                    db.execute("UPDATE jobs SET status='pending',error='',progress='' WHERE id=?", (identity,))
        return identity

    def jobs(self):
        with self.connect() as db:
            return [dict(row, **json.loads(row['metadata'])) for row in db.execute('SELECT * FROM jobs ORDER BY created, id')]

    def recover(self, retry_failed=False):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='pending', error='', updated=? WHERE status='running'",
                       (time.time(),))
            if retry_failed:
                db.execute("UPDATE jobs SET status='pending', error='', updated=? WHERE status='failed'",
                           (time.time(),))

    def status(self, identity, status, error=''):
        if status not in ('pending', 'running', 'failed', 'completed', 'cancelled', 'delegated'):
            raise ValueError('Invalid download state')
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?, error=?, updated=? WHERE id=?',
                       (status, error, time.time(), identity))

    def claim(self, identity):
        with self.connect() as db:
            return db.execute("UPDATE jobs SET status='running',error='',updated=? WHERE id=? AND status='pending'",
                              (time.time(), identity)).rowcount == 1

    def progress(self, identity, message):
        with self.connect() as db:
            db.execute('UPDATE jobs SET progress=?, updated=? WHERE id=?', (message, time.time(), identity))

    def cancel_pending(self, identity):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='cancelled',progress='Cancelled' WHERE id=? AND status='pending'", (identity,))

    def retry(self, identity=None):
        with self.connect() as db:
            if identity:
                db.execute("UPDATE jobs SET status='pending',error='',progress='' WHERE id=? AND status IN ('failed','cancelled')", (identity,))
            else:
                db.execute("UPDATE jobs SET status='pending',error='',progress='' WHERE status IN ('failed','cancelled')")

    def complete(self, job, path):
        path = Path(path).resolve()
        directory = Path(job['directory']).resolve()
        if path.parent != directory or not path.is_file() or not path.stat().st_size:
            raise ValueError('Completed output must be a nonempty file inside the managed destination')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO media VALUES (?, ?, ?, ?, ?, ?)',
                       (job['id'], job['metadata'], str(directory), str(path), path.stat().st_size, time.time()))
            db.execute("UPDATE jobs SET status='completed', error='', updated=? WHERE id=?",
                       (time.time(), job['id']))

    def media(self):
        with self.connect() as db:
            rows = [dict(row, **json.loads(row['metadata'])) for row in db.execute('SELECT * FROM media ORDER BY updated DESC')]
        for row in rows:
            path = Path(row['path'])
            try:
                row['available'] = (path.resolve().parent == Path(row['directory']).resolve()
                                    and path.is_file() and path.stat().st_size == row['bytes'])
            except OSError:
                row['available'] = False
        return rows
