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
        except Exception as e:
            return None
        
        soup2 = BeautifulSoup(html2, "html.parser")
        
        btn = soup2.select_one('a.btn.btn-gradient.submit-btn')
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

            resp = self.session.get(url, headers=headers)
            if resp.status_code != 200:
                return None
                
            html = resp.text
            parsed = urlparse(url)
            
            if 'vidtube.one' in parsed.hostname:
                return self._handle_vidtube_one(html)
            else:
                return self._handle_vidtube_pro(url, html, headers)
                
        except Exception as e:
            return None
