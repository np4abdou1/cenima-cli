"""Small provider contract with mirror fallback and health isolation."""

from urllib.parse import urlsplit

from .config import MIRROR_DOMAINS
from .scraper import TopCinemaScraper


class ProviderFallback:
    def __init__(self, mirrors=None):
        self.mirrors = list(mirrors or MIRROR_DOMAINS)
        self._providers = {}
        self.failed = set()
        self.base_url = self.mirrors[0] if self.mirrors else ''

    def __getattr__(self, operation):
        def delegated(*args, **kwargs):
            url = kwargs.pop('url', None)
            if args and isinstance(args[-1], dict):
                url = url or args[-1].get('url')
            return self.call(operation, *args, url=url, **kwargs)
        return delegated

    def _provider(self, base):
        if base not in self._providers:
            self._providers[base] = TopCinemaScraper(base_url=base)
        return self._providers[base]

    def _bases(self, url=None):
        if url:
            host = (urlsplit(url).hostname or '').lower()
            for mirror in self.mirrors:
                mirror_host = (urlsplit(mirror).hostname or '').lower()
                if host == mirror_host:
                    return [mirror] + [item for item in self.mirrors if item != mirror]
        return self.mirrors

    def call(self, operation, *args, url=None, **kwargs):
        last_error = None
        for base in self._bases(url):
            try:
                provider = self._provider(base)
                method = getattr(provider, operation)
                result = method(*args, **kwargs)
                self.failed.discard(base)
                if result:
                    return result
            except Exception as exc:
                last_error = exc
                self.failed.add(base)
        if last_error:
            raise last_error
        return None

    def search(self, query, **kwargs):
        return self.call('search', query, **kwargs)

    def details(self, url):
        return self.call('get_show_details', url)

    def episodes(self, url):
        return self.call('get_seasons', url, url=url)

    def resolve(self, episode_or_server):
        url = episode_or_server.get('embed_url') if isinstance(episode_or_server, dict) else None
        provider = self._provider(self._bases(url)[0])
        return provider.vidtube_processor.extract(url) if url else None
