import re
import math
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from curl_cffi import requests

class VidTubeProcessor:
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

    def _handle_vidtube_one(self, html):
        packer_regex = r"return\s+p}\s*\(\s*(['\"])(.*?)\1\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(['\"])(.*?)\5\.split\(\s*['\"]\|['\"]\s*\)\s*\)"
        match = re.search(packer_regex, html, re.DOTALL)
        
        if not match:
            return None
            
        payload = match.group(2)
        radix = int(match.group(3))
        count = int(match.group(4))
        keywords = match.group(6).split('|')
        
        unpacked_code = self._unpack(payload, radix, count, keywords)
        
        # extract file:"..."
        m3u8_regex = r"file\s*:\s*[\"'](https?://[^\"']+\.m3u8[^\"']*)[\"']"
        mp4_regex = r"file\s*:\s*[\"'](https?://[^\"']+\.mp4[^\"']*)[\"']"
        
        mp4_match = re.search(mp4_regex, unpacked_code)
        if mp4_match:
            return mp4_match.group(1)
            
        m3u8_match = re.search(m3u8_regex, unpacked_code)
        if m3u8_match:
            return m3u8_match.group(1)
            
        return None

    def _extract_file_code(self, url: str) -> Optional[str]:
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
        quality = ""
        
        links = soup.find_all('a', href=True)
        
        for suffix, label in priorities:
            for link in links:
                href = link['href']
                if href.endswith(suffix):
                    next_path = href
                    quality = label
                    break
            if next_path:
                break
                
        if not next_path:
            # Fallback
            path_only = urlparse(target_url).path
            for link in links:
                href = link['href']
                if '/d/' in href and href != path_only:
                    next_path = href
                    quality = "unknown"
                    break
        
        if not next_path:
            return None
            
        next_url = urljoin(target_url, next_path)
        
        try:
            resp = self.session.get(next_url, headers=req_headers)
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
        if btn and btn.get('href'):
            return btn['href']
        
        mp4_regex = r"[\"'](https?://[^\"']+\.mp4[^\"']*)[\"']"
        match = re.search(mp4_regex, html2)
        if match:
            return match.group(1)
            
        return None

    def extract(self, url, referer=None):
        try:
            headers = self.headers.copy()
            if referer:
                headers["Referer"] = referer
                headers["Origin"] = referer.rstrip('/')
            elif "Referer" not in headers:
                headers["Referer"] = "https://topcinemaa.cc/"

            parsed = urlparse(url)
            file_code = self._extract_file_code(url)

            # Strategy 1: Direct MP4 resolution via VidTube download portal pages (/d/<code>.html)
            # This yields unthrottled direct .mp4 links instead of unstable .m3u8 playlists.
            if file_code:
                download_urls = []
                download_urls.append(f"https://down.vidtube.one/d/{file_code}.html")
                if parsed.hostname and parsed.hostname != "down.vidtube.one":
                    download_urls.append(f"https://{parsed.hostname}/d/{file_code}.html")
                    download_urls.append(f"https://{parsed.hostname}/d/{file_code}")

                for dl_url in download_urls:
                    try:
                        dl_resp = self.session.get(dl_url, headers=headers, timeout=10)
                        if dl_resp.status_code == 200:
                            direct_url = self._handle_vidtube_pro(dl_url, dl_resp.text, headers)
                            if direct_url:
                                return direct_url
                    except Exception:
                        continue

            # Strategy 2: Fallback to querying original URL directly
            resp = self.session.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return None
                
            html = resp.text
            
            # Try VidTube pro download link extraction on target page HTML
            pro_url = self._handle_vidtube_pro(url, html, headers)
            if pro_url:
                return pro_url

            # Strategy 3: Final fallback to packed JS inline player decoder (_handle_vidtube_one)
            return self._handle_vidtube_one(html)
                
        except Exception:
            return None

