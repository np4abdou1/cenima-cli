"""Remove the explicit artificial Yaga qualification label from its two records."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cenima.config import CONFIG_DIR
from cenima.library import MediaStore
from cenima.storage import rename_managed_title

store = MediaStore(CONFIG_DIR / 'media.sqlite3')
updated = []
with store.worker_lock():
    for row in store.media():
        if row['show_title'] == 'Yaga مسلسل عربي' and row.get('episode_number') in ('2', '3'):
            path = rename_managed_title(store, row['id'], 'Yaga')
            updated.append((row['catalog_url'], str(path)))
            print(path)
qualification = MediaStore(Path(__file__).resolve().parents[1] / '.packaging-check/live/background/مسار-عربي/qualification.sqlite3')
with qualification.worker_lock(), qualification.connect() as db:
    for row in db.execute('SELECT id,metadata FROM media').fetchall():
        metadata = json.loads(row['metadata'])
        found = next((path for url, path in updated if url == metadata['catalog_url']), None)
        if found:
            metadata.update(show_title='Yaga', download_title=Path(found).stem)
            payload = json.dumps(metadata, ensure_ascii=False)
            db.execute('UPDATE media SET metadata=?,path=? WHERE id=?', (payload, found, row['id']))
            db.execute('UPDATE jobs SET metadata=? WHERE id=?', (payload, row['id']))
