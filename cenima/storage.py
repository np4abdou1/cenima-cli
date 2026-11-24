"""One user-selected media destination and safe consolidation of completed files."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, unquote
from urllib.request import url2pathname

from .download import partial_root
from .models import canonical_content_url


def media_folder(value):
    value = str(value).strip().strip('"').strip("'")
    if value.lower().startswith('file:'):
        parts = urlsplit(value)
        value = url2pathname(parts.path)
        if parts.netloc and parts.netloc != 'localhost':
            value = '//' + parts.netloc + value
    elif '://' in value:
        raise ValueError('Enter a local folder path or file:// link, not a web URL.')
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        raise ValueError('Enter the full absolute folder path.')
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=str(path)):
        pass
    return path


def open_media_folder(directory):
    path = media_folder(directory)
    if os.name == 'nt':
        os.startfile(str(path))
    else:
        subprocess.Popen(['open' if sys_platform() == 'darwin' else 'xdg-open', str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sys_platform():
    import sys
    return sys.platform


def same_file_bytes(one, two):
    if one.stat().st_size != two.stat().st_size:
        return False
    def digest(path):
        value = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                value.update(chunk)
        return value.digest()
    return digest(one) == digest(two)


def rename_managed_title(store, identity, show_title):
    """Rename one verified managed output and keep queue/library metadata aligned.

    Caller owns worker_lock. Titles come from explicit corrections, not language
    stripping; genuine Arabic show names remain valid.
    """
    from .download import media_title
    row = next(row for row in store.media() if row['id'] == identity)
    if not row['available']:
        raise FileNotFoundError('Managed file is missing or has changed')
    metadata = json.loads(row['metadata'])
    metadata['show_title'] = show_title
    metadata['download_title'] = media_title(show_title, metadata.get('season_number'),
        metadata.get('episode_number'), metadata.get('season_part'))
    source = Path(row['path']).resolve()
    destination = source.parent / (metadata['download_title'] + source.suffix)
    if source != destination and destination.exists():
        raise FileExistsError(str(destination))
    root = partial_root(source.parent, metadata['download_title'])
    root.mkdir(parents=True, exist_ok=True)
    owner = root / 'owner.json'
    catalog_id = hashlib.sha256(canonical_content_url(metadata['catalog_url']).encode()).hexdigest()
    if owner.exists() and json.loads(owner.read_text(encoding='utf-8'))['identity'] != catalog_id:
        raise FileExistsError('A different catalog item owns this name')
    if source != destination:
        source.rename(destination)
    try:
        temporary = owner.with_suffix('.tmp')
        temporary.write_text(json.dumps({'identity': catalog_id, 'completed': {
            'name': destination.name, 'bytes': destination.stat().st_size}}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(owner)
        payload = json.dumps(metadata, ensure_ascii=False)
        with store.connect() as db:
            db.execute('UPDATE jobs SET metadata=? WHERE id=?', (payload, identity))
            db.execute('UPDATE media SET metadata=?,path=? WHERE id=?', (payload, str(destination), identity))
    except BaseException:
        if source != destination and destination.exists() and not source.exists():
            destination.rename(source)
        raise
    return destination


def consolidate(store, directory, imports=()):
    """Caller owns worker_lock. Reject conflicts before moving any completed file.

    Imports contain path, filename and metadata for explicitly identified media.
    Existing pending jobs follow the new destination; old partials are retained.
    """
    target = media_folder(directory)
    rows = store.media()
    records = [{'path': row['path'], 'filename': Path(row['path']).name,
                'metadata': json.loads(row['metadata'])} for row in rows if row['available']]
    records.extend(imports)
    planned, duplicates, moved = {}, [], []
    for record in records:
        source = Path(record['path']).resolve()
        filename = record['filename']
        if Path(filename).name != filename or filename in ('.', '..'):
            raise ValueError('Invalid destination filename')
        destination = (target / filename).resolve()
        if destination.parent != target:
            raise ValueError('Media destination escapes the selected folder')
        if source == destination:
            planned[destination] = source
            continue
        if not source.is_file():
            raise FileNotFoundError(str(source))
        existing = destination if destination.exists() else planned.get(destination)
        if existing is not None:
            if not same_file_bytes(source, existing):
                raise FileExistsError(f'Different files share this name: {filename}. Nothing was moved.')
            duplicates.append(source)
        else:
            planned[destination] = source
    try:
        for destination, source in planned.items():
            if source != destination:
                if destination.exists():
                    raise FileExistsError(str(destination))
                shutil.move(str(source), str(destination))
                moved.append((source, destination))
        # Completion ownership stays hidden, while the public folder contains
        # clean media filenames. Rebuild records for renamed/imported outputs.
        for record in records:
            destination = target / record['filename']
            metadata = record['metadata']
            root = partial_root(target, metadata['download_title'])
            root.mkdir(parents=True, exist_ok=True)
            owner = root / 'owner.json'
            identity = hashlib.sha256(canonical_content_url(metadata['catalog_url']).encode()).hexdigest()
            if owner.exists() and json.loads(owner.read_text(encoding='utf-8'))['identity'] != identity:
                raise FileExistsError('A different catalog item owns ' + record['filename'])
            temporary = owner.with_suffix('.tmp')
            temporary.write_text(json.dumps({'identity': identity, 'completed': {
                'name': destination.name, 'bytes': destination.stat().st_size}}, ensure_ascii=False), encoding='utf-8')
            temporary.replace(owner)
        with store.connect() as db:
            for row in rows:
                path = str(target / Path(row['path']).name)
                db.execute('UPDATE media SET directory=?,path=? WHERE id=?', (str(target), path, row['id']))
            db.execute('UPDATE jobs SET directory=?', (str(target),))
            for record in imports:
                metadata = record['metadata']
                payload = json.dumps(metadata, ensure_ascii=False)
                identity = hashlib.sha256((canonical_content_url(metadata['catalog_url']) + '\0' + os.path.normcase(str(target))).encode()).hexdigest()
                existing = next((row for row in db.execute('SELECT id,metadata FROM jobs')
                    if canonical_content_url(json.loads(row['metadata'])['catalog_url']) == canonical_content_url(metadata['catalog_url'])), None)
                identity = existing['id'] if existing else identity
                now = time.time()
                db.execute('INSERT OR IGNORE INTO jobs (id,metadata,directory,status,error,created,updated) VALUES (?,?,?,?,?,?,?)',
                           (identity, payload, str(target), 'completed', '', now, now))
                destination = target / record['filename']
                db.execute('INSERT OR REPLACE INTO media VALUES (?,?,?,?,?,?)',
                    (identity, payload, str(target), str(destination), destination.stat().st_size, now))
                db.execute("UPDATE jobs SET status='completed',progress='100% · Complete',error='' WHERE id=?", (identity,))
    except BaseException:
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                shutil.move(str(destination), str(source))
        raise
    for source in set(duplicates):
        if source not in planned and source.exists():
            source.unlink()
    return {'moved': len(moved), 'deduplicated': len(set(duplicates)), 'folder': str(target)}
