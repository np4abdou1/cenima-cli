"""Move the explicitly identified completed qualification videos to the user's folder."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cenima.cli import CinemaCLI, Config
from cenima.library import MediaStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder')
    options = parser.parse_args()
    workspace = Path(__file__).resolve().parents[1]
    staging = (workspace / '.packaging-check/live').resolve()
    episode_store = MediaStore(staging / 'background' / 'مسار-عربي' / 'qualification.sqlite3')
    episodes = episode_store.media()
    records = [{'path': row['path'], 'filename': Path(row['path']).name,
                'metadata': json.loads(row['metadata'])} for row in episodes if row['available']]
    movie = staging / 'مسار-عربي' / 'Dune Part Two 2024.mp4'
    movie_url = 'https://topcinemaa.one/فيلم-dune-part-two-2024-مترجم-اون-لاين/'
    if movie.exists():
        records.append({'path': str(movie), 'filename': movie.name, 'metadata': {
            'catalog_url': movie_url, 'show_url': movie_url, 'show_title': 'Dune Part Two 2024',
            'download_title': 'Dune Part Two 2024', 'content_type': 'movie', 'manager': 'builtin'}})
    duplicate = staging / 'مسار-عربي' / 'media-اختبار عربي Yaga 2-34236c0aebc97c45.mp4'
    episode_two = next((row for row in episodes if row['episode_number'] == '2'), None)
    if duplicate.exists() and episode_two:
        records.append({'path': str(duplicate), 'filename': Path(episode_two['path']).name,
                        'metadata': json.loads(episode_two['metadata'])})
    for record in records:
        if not Path(record['path']).resolve().is_relative_to(staging):
            raise ValueError('Qualification source escapes the verified staging directory')
    cli = object.__new__(CinemaCLI)
    cli.config = Config()
    result = cli.set_download_folder(options.folder, records)
    # Keep the isolated qualification index consistent with the moved videos.
    from cenima.storage import consolidate
    with episode_store.worker_lock():
        consolidate(episode_store, options.folder)
    print(json.dumps(result, ensure_ascii=False))
    for row in cli.media_store.media():
        print(row['path'], row['bytes'], 'AVAILABLE' if row['available'] else 'MISSING')


if __name__ == '__main__':
    main()
