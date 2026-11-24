"""A dedicated asyncio thread keeps transfers alive while the TUI reads keys."""
import asyncio
import threading
import time
from .download import download_catalog_item, redact_reason


class DownloadWorker:
    def __init__(self, store, scraper_factory, notifications_enabled=False, notify=None):
        self.store, self.scraper_factory = store, scraper_factory
        self.notifications_enabled = notifications_enabled
        self.notify = notify
        self._batch_processed = False
        self._batch_notified = False
        self.thread = None
        self.loop = self.active = None
        self.current_id = None
        self.stopping = threading.Event()
        self.cancelled = set()
        self.error = ''
        self._lock = threading.Lock()

    def start(self, retry_failed=False):
        with self._lock:
            if retry_failed:
                self.store.retry()
            if self.thread and self.thread.is_alive():
                return
            self.stopping.clear()
            self.error = ''
            self.thread = threading.Thread(target=self._main, daemon=True, name='cenima-downloads')
            self.thread.start()

    def _main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.error = redact_reason(str(exc))

    async def _run(self):
        self.loop = asyncio.get_running_loop()
        with self.store.worker_lock():
            self.store.recover()
            scraper = None
            while not self.stopping.is_set():
                job = next((j for j in self.store.jobs() if j['status'] == 'pending'), None)
                if job is None:
                    if self._batch_processed and not self._batch_notified:
                        jobs = self.store.jobs()
                        completed = sum(j['status'] == 'completed' for j in jobs)
                        failed = sum(j['status'] == 'failed' for j in jobs)
                        if self.notifications_enabled and self.notify:
                            if completed or failed:
                                self.notify('Cinema CLI downloads', f'{completed} completed · {failed} failed')
                        self._batch_notified = True
                        self._batch_processed = False
                    # Remain available for jobs added while browsing; no network work.
                    await asyncio.sleep(0.2)
                    continue
                self.current_id = job['id']
                if not self.store.claim(job['id']):
                    self.current_id = None
                    continue
                last = [0.0]

                def update(message):
                    if time.monotonic() - last[0] >= 0.5:
                        self.store.progress(job['id'], message)
                        last[0] = time.monotonic()

                try:
                    if scraper is None:
                        # Lazy initialization stays off the input thread.
                        scraper = await asyncio.to_thread(self.scraper_factory)
                    self.active = asyncio.create_task(download_catalog_item(scraper,
                        {'url': job['catalog_url'], 'manager': job.get('manager') or 'builtin'},
                        job['directory'], job['download_title'], update))
                    if self.stopping.is_set() or job['id'] in self.cancelled:
                        self.active.cancel()
                    result = await self.active
                    if result.path is None:
                        self.store.status(job['id'], 'delegated')
                        self.store.progress(job['id'], 'Handed to IDM; completion is managed externally')
                    else:
                        self.store.complete(job, result.path)
                        self.store.progress(job['id'], '100% · Already present' if result.skipped else '100% · Complete')
                except asyncio.CancelledError:
                    self.store.status(job['id'], 'pending' if self.stopping.is_set() else 'cancelled')
                    self.store.progress(job['id'], 'Paused on exit' if self.stopping.is_set() else 'Cancelled; partial files retained')
                except Exception as exc:
                    self.store.status(job['id'], 'failed', redact_reason(str(exc)))
                finally:
                    self._batch_processed = True
                    self._batch_notified = False
                    self.cancelled.discard(job['id'])
                    self.current_id, self.active = None, None
        self.loop = None

    def cancel(self, identity):
        if self.current_id == identity:
            self.cancelled.add(identity)
            if self.loop and self.active:
                self.loop.call_soon_threadsafe(self.active.cancel)
        else:
            self.store.cancel_pending(identity)

    def shutdown(self):
        self.stopping.set()
        if self.loop and self.active:
            self.loop.call_soon_threadsafe(self.active.cancel)
        if self.thread:
            self.thread.join(timeout=10)
