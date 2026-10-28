import re
import math
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from curl_cffi import requests

class StreamURL(str):
    """String subclass that preserves stream-specific headers and referer."""
    referer: str = ""
    headers: dict = {}

    def __new__(cls, content: str, referer: str = "", headers: dict = None):
        obj = super().__new__(cls, content)
        obj.referer = referer or ""
        obj.headers = headers or {}
        return obj

class ServerProcessor:
    """Universal multi-server video extractor supporting VidTube, UpDown, Mdiaload, BowFile, Ddownload, 1CloudFile, etc."""
    
    def __init__(self, session=None, referer=None):
        self.session = session or requests.Session(impersonate="chrome120")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            self.headers["Referer"] = referer
            self.headers["Origin"] = referer.rstrip('/')

    def _unpack(self, p, a, c, k):
        def to_base(n, b):
            if n == 0: return "0"
            digits = "0123456789abcdefghijklmnopqrstuvwxyz"
            result = ""
            while n:
                result = digits[n % b] + result
                n //= b
            return result

        for i in range(c - 1, -1, -1):
            if i < len(k) and k[i]:
                token = to_base(i, a)
                value = k[i]
                pattern = r'\b' + re.escape(token) + r'\b'
                p = re.sub(pattern, value, p)
        return p

    def _handle_vidtube_one(self, html, referer=None):
        packer_regex = r"return\s+p}\s*\(\s*(['\"])(.*?)\1\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(['\"])(.*?)\5\.split\(\s*['\"]\|['\"]\s*\)\s*\)"
        match = re.search(packer_regex, html, re.DOTALL)
        
        if not match:
            return None
            
        payload = match.group(2)
        radix = int(match.group(3))
        count = int(match.group(4))
        keywords = match.group(6).split('|')
        
        unpacked_code = self._unpack(payload, radix, count, keywords)
        
        mp4_regex = r"file\s*:\s*[\"'](https?://[^\"']+\.mp4[^\"']*)[\"']"
        m3u8_regex = r"file\s*:\s*[\"'](https?://[^\"']+\.m3u8[^\"']*)[\"']"
        
        if "gamescdn" in unpacked_code or (referer and "updown" in referer):
            ref_host = "https://updown.cam/"
        elif (referer and "uqload" in referer) or "uqload" in unpacked_code:
            ref_host = "https://uqload.net/"
        elif referer:
            ref_host = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}/"
        else:
            ref_host = "https://down.vidtube.one/"

        vid_headers = {
            "Referer": ref_host,
            "User-Agent": self.headers.get("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        }

        mp4_match = re.search(mp4_regex, unpacked_code)
        if mp4_match:
            return StreamURL(mp4_match.group(1), referer=ref_host, headers=vid_headers)
            
        m3u8_match = re.search(m3u8_regex, unpacked_code)
        if m3u8_match:
            return StreamURL(m3u8_match.group(1), referer=ref_host, headers=vid_headers)
            
        return None

    def _extract_file_code(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            path = parsed.path
            match = re.search(r"/(?:embed-|d/|v/)?([a-zA-Z0-9]{8,32})(?:_[a-z0-9]+)?(?:\.html)?", path)
            if match:
                return match.group(1)
            return None
        except Exception:
            return None

    def _handle_vidtube_pro(self, target_url, html, req_headers):
        soup = BeautifulSoup(html, "html.parser")
        
        priorities = [
            ('_x', '1080p'),
            ('_h', '720p'),
            ('_n', '480p'),
            ('_l', '240p')
        ]
        
        next_path = None
        links = soup.find_all('a', href=True)
        
        for suffix, label in priorities:
            for link in links:
                href = link['href']
                if href.endswith(suffix):
                    next_path = href
                    break
            if next_path:
                break
                
        if not next_path:
            path_only = urlparse(target_url).path
            for link in links:
                href = link['href']
                if '/d/' in href and href != path_only:
                    next_path = href
                    break
        
        if not next_path:
            return None
            
        next_url = urljoin(target_url, next_path)
        
        try:
            resp = self.session.get(next_url, headers=req_headers, timeout=10)
            if resp.status_code != 200:
                return None
            html2 = resp.text
        except Exception:
            return None
        
        soup2 = BeautifulSoup(html2, "html.parser")
        btn = (soup2.select_one('a.btn.btn-gradient.submit-btn') or 
               soup2.select_one('a.submit-btn') or 
               soup2.select_one('a.btn-gradient') or
               soup2.select_one('a[href*=".mp4"]'))

        vid_headers = {
            "Referer": next_url or "https://down.vidtube.one/",
            "User-Agent": req_headers.get("User-Agent", self.headers.get("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"))
        }

        if btn and btn.get('href'):
            direct = urljoin(next_url, btn['href'])
            return StreamURL(direct, referer=next_url or "https://down.vidtube.one/", headers=vid_headers)
        
        mp4_regex = r"[\"'](https?://[^\"']+\.mp4[^\"']*)[\"']"
        match = re.search(mp4_regex, html2)
        if match:
            direct = urljoin(next_url, match.group(1))
            return StreamURL(direct, referer=next_url or "https://down.vidtube.one/", headers=vid_headers)
            
        return None

    def extract_xfilesharing(self, url: str, referer: str = None) -> str:
        """Fast browserless 2/3-step extractor for UpDown, Mdiaload, BowFile, Ddownload, 1CloudFile, etc."""
        headers = self.headers.copy()
        headers["Referer"] = referer or url
        headers["Origin"] = urlparse(url).scheme + "://" + urlparse(url).netloc
        
        try:
            # Step 1: GET page
            resp1 = self.session.get(url, headers=headers, timeout=10)
            if resp1.status_code != 200:
                return None
                
            soup1 = BeautifulSoup(resp1.text, 'html.parser')
            form1 = soup1.find('form')
            if not form1:
                for a in soup1.find_all('a', href=True):
                    if '.mp4' in a['href']: return a['href']
                return None
                
            data1 = {inp.get('name'): inp.get('value', '') for inp in form1.find_all('input') if inp.get('name')}
            
            # Step 2: POST form 1
            resp2 = self.session.post(url, data=data1, headers=headers, timeout=10)
            if resp2.status_code != 200:
                return None
                
            soup2 = BeautifulSoup(resp2.text, 'html.parser')
            form2 = soup2.find('form')
            if not form2:
                for a in soup2.find_all('a', href=True):
                    if '.mp4' in a['href']: return a['href']
                return None
                
            data2 = {inp.get('name'): inp.get('value', '') for inp in form2.find_all('input') if inp.get('name')}
            if 'method_premium' in data2:
                del data2['method_premium']
                
            ref_host = f"https://{urlparse(url).hostname}/" if urlparse(url).hostname else "https://updown.cam/"
            x_headers = {
                "Referer": ref_host,
                "User-Agent": headers.get("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            }

            # Step 3: POST form 2 with allow_redirects=False to catch 302 location header instantly!
            resp3 = self.session.post(url, data=data2, headers=headers, allow_redirects=False, timeout=10)
            
            if resp3.status_code in (301, 302, 303, 307) and 'Location' in resp3.headers:
                loc = resp3.headers['Location']
                return StreamURL(loc, referer=ref_host, headers=x_headers)
                
            if resp3.status_code == 200:
                soup3 = BeautifulSoup(resp3.text, 'html.parser')
                for a in soup3.find_all('a', href=True):
                    if '.mp4' in a['href'] or 'gamescdn' in a['href']:
                        return StreamURL(a['href'], referer=ref_host, headers=x_headers)
                        
                mp4_match = re.search(r"[\"'](https?://[^\"']+\.mp4[^\"']*)[\"']", resp3.text)
                if mp4_match:
                    return StreamURL(mp4_match.group(1), referer=ref_host, headers=x_headers)
                        
            return None
        except Exception:
            return None

    def extract(self, url: str, referer: str = None) -> str:
        """Universal entrypoint: detects server type and routes to optimal extractor."""
        if not url:
            return None

        headers = self.headers.copy()
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = referer.rstrip('/')

        hostname = urlparse(url).hostname or ""

        # Fast path for embed player pages with packed JS (UpDown Embed, VidTube Embed, etc.)
        if 'embed' in url.lower() or '/e/' in url.lower():
            try:
                resp = self.session.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    unpacked = self._handle_vidtube_one(resp.text, referer=url)
                    if unpacked:
                        return unpacked
            except Exception:
                pass

        # UpDown, Mdiaload, BowFile, Ddownload, 1CloudFile, Nitroflare (XFileSharing servers)
        if any(h in hostname for h in ['updown.cam', 'mdiaload', 'bowfile', 'ddownload', '1cloudfile', 'nitroflare']):
            return self.extract_xfilesharing(url, referer=referer)

        # VidTube servers
        if any(h in hostname for h in ['vidtube', 'serv-stream', 'cdn-video']):
            file_code = self._extract_file_code(url)
            if file_code:
                download_urls = [
                    f"https://down.vidtube.one/d/{file_code}.html",
                    f"https://{hostname}/d/{file_code}.html",
                    f"https://{hostname}/d/{file_code}"
                ]
                for dl_url in download_urls:
                    try:
                        dl_resp = self.session.get(dl_url, headers=headers, timeout=10)
                        if dl_resp.status_code == 200:
                            direct_url = self._handle_vidtube_pro(dl_url, dl_resp.text, headers)
                            if direct_url:
                                return direct_url
                    except Exception:
                        continue

            try:
                resp = self.session.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    pro_url = self._handle_vidtube_pro(url, resp.text, headers)
                    if pro_url: return pro_url
                    return self._handle_vidtube_one(resp.text, referer=url)
            except Exception:
                pass

        # Generic XFileSharing fallback
        return self.extract_xfilesharing(url, referer=referer)

# Backwards compatibility alias
VidTubeProcessor = ServerProcessor
