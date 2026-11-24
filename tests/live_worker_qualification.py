"""Opt-in real background queue qualification; stores media only under --output."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cenima.download import media_title, redact_reason
from cenima.library import MediaStore
from cenima.scraper import TopCinemaScraper
from cenima.worker import DownloadWorker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--episodes', default='2,3')
    parser.add_argument('--seconds', type=int, default=600)
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    scraper = TopCinemaScraper()
    show_url = 'https://topcinemaa.one/series/مسلسل-yaga-مترجم/'
    details = scraper.get_show_details(show_url)
    if not details:
        raise RuntimeError(redact_reason(getattr(scraper, 'last_error', 'No show details')))
    season = details['seasons'][0]
    episodes = scraper.fetch_season_episodes(season)
    selected = [ep for ep in episodes if str(ep.get('episode_number')) in options.episodes.split(',')]
    if len(selected) != len(options.episodes.split(',')):
        raise RuntimeError('Not all requested catalog episodes were found')
    store = MediaStore(output / 'qualification.sqlite3')
    for episode in selected:
        number = str(episode['episode_number'])
        store.enqueue({'catalog_url': episode['url'], 'show_url': show_url,
            'show_title': 'Yaga', 'download_title': media_title('Yaga', 1, number),
            'content_type': 'series', 'season_number': 1, 'episode_number': number, 'manager': 'builtin'}, output)
    worker = DownloadWorker(store, TopCinemaScraper)
    started, last_print = time.monotonic(), 0
    try:
        worker.start(retry_failed=True)
        while time.monotonic() - started < options.seconds:
            jobs = store.jobs()
            if time.monotonic() - last_print > 10:
                for job in jobs:
                    print(job['download_title'], job['status'], job['progress'], job['error'], flush=True)
                last_print = time.monotonic()
            if all(job['status'] in ('completed', 'failed', 'cancelled', 'delegated') for job in jobs):
                break
            if worker.error:
                raise RuntimeError(worker.error)
            time.sleep(0.3)
    finally:
        worker.shutdown()
        report = {'jobs': [{key: job.get(key) for key in
            ('episode_number', 'download_title', 'status', 'progress', 'error')} for job in store.jobs()],
            'media': [{key: media.get(key) for key in ('download_title', 'path', 'bytes', 'available')}
                      for media in store.media()]}
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
