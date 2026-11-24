"""Opt-in live transfers. Downloads real media into an explicitly supplied folder.

Run from the checkout: python -X utf8 tests/live_qualification.py --output PATH
The report deliberately excludes signed URLs and request headers.
"""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cenima.download import download_stream, media_title, redact_reason
from cenima.processor import ServerProcessor, StreamURL
from cenima.scraper import TopCinemaScraper


async def run(output, seconds, only='both', episode_numbers=None, show_url=None):
    output.mkdir(parents=True, exist_ok=True)
    report = {"started": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), "items": []}
    scraper = await asyncio.to_thread(TopCinemaScraper)
    for query, kind in [('Dune', 'movie'), ('Yaga', 'series')]:
        if only != 'both' and kind != only:
            continue
        print('Searching', query, flush=True)
        results = [{'url': show_url}] if show_url else await asyncio.to_thread(scraper.search, query)
        if not results:
            report['items'].append({'query': query, 'status': 'search_failed'})
            continue
        details = await asyncio.to_thread(scraper.get_show_details, results[0]['url'])
        if not details:
            report['items'].append({'query': query, 'status': 'details_failed'})
            continue
        if kind == 'series':
            episodes = await asyncio.to_thread(scraper.fetch_season_episodes, details['seasons'][0])
            items = [episode for episode in episodes if str(episode.get('episode_number')) in episode_numbers] if episode_numbers else episodes[:2]
        else:
            items = [details]
        for index, item in enumerate(items):
            number = item.get('episode_number') or index + 1
            entry = {'query': query, 'kind': kind, 'number': number, 'attempts': []}
            report['items'].append(entry)
            servers = item.get('servers') or await asyncio.to_thread(scraper.fetch_episode_servers, item)
            print(query, index + 1, 'servers:', [s.get('name') for s in servers], flush=True)
            for server in servers:
                attempt = {'server': server.get('name')}
                entry['attempts'].append(attempt)
                try:
                    processor = ServerProcessor()
                    url = server.get('video_url') or await asyncio.to_thread(
                        processor.extract, server.get('embed_url'), scraper.base_url)
                    if not url:
                        attempt['status'] = 'unresolved'
                        attempt['reason'] = redact_reason(processor.last_error or 'No supported media URL was found.')
                        continue
                    if not getattr(url, 'referer', ''):
                        url = StreamURL(str(url), referer=server.get('embed_url') or scraper.base_url)
                    title = media_title(f'{query} اختبار عربي', 1 if kind == 'series' else None,
                                        number if kind == 'series' else None)
                    last = [0.0]

                    def progress(status):
                        if time.monotonic() - last[0] > 10:
                            print(query, index + 1, status, flush=True)
                            last[0] = time.monotonic()

                    started = time.monotonic()
                    result = await asyncio.wait_for(download_stream(url, output, title, item['url'], progress), seconds)
                    attempt.update(status='completed', seconds=round(time.monotonic() - started, 1))
                    entry.update(status='completed', filename=result.path.name, bytes=result.path.stat().st_size)
                    probe = await asyncio.to_thread(subprocess.run,
                        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type',
                         '-of', 'json', str(result.path)], capture_output=True, text=True, timeout=60)
                    entry['probe'] = json.loads(probe.stdout) if probe.returncode == 0 else {'failed': True}
                    print('COMPLETED', query, index + 1, entry['bytes'], flush=True)
                    break
                except asyncio.TimeoutError:
                    attempt['status'] = 'timed_out_cancelled'
                    print('TIMEOUT/CANCELLED', query, server.get('name'), flush=True)
                except Exception as exc:
                    # Error types and exit codes suffice; upstream exceptions can contain tokens.
                    attempt.update(status='failed', error_type=type(exc).__name__, reason=redact_reason(str(exc)))
                    print('FAILED', query, server.get('name'), type(exc).__name__, flush=True)
                finally:
                    (output / f'report-{only}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            entry.setdefault('status', 'failed')
            (output / f'report-{only}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    (output / f'report-{only}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=300, help='Timeout per transfer attempt')
    parser.add_argument('--kind', choices=('both', 'movie', 'series'), default='both')
    parser.add_argument('--episodes', default='', help='Optional comma-separated catalog episode numbers')
    parser.add_argument('--show-url', help='Previously verified catalog page, bypassing a transient search failure')
    options = parser.parse_args()
    asyncio.run(run(options.output.resolve(), options.seconds, options.kind,
                    options.episodes.split(',') if options.episodes else None, options.show_url))
