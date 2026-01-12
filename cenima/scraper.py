#!/usr/bin/env python3
import re
import json
import os
from curl_cffi import requests
from typing import List, Dict, Optional, Any
from urllib.parse import quote, urlparse, unquote
from bs4 import BeautifulSoup

from .config import (
    BASE_URL, HEADERS, AJAX_HEADERS,
    REQUEST_TIMEOUT, RETRY_TOTAL, RETRY_BACKOFF, RETRY_STATUS_CODES
)


def clean_text(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.strip().split())

def clean_arabic_title(text: str) -> str:
    if not text:
        return text
    
    text = re.sub(r'[\u0600-\u06FF]+', '', text)
    
    parts = text.split()
    seen_numbers = set()
    cleaned_parts = []
    for part in parts:
        if part.isdigit():
            if part not in seen_numbers:
                cleaned_parts.append(part)
                seen_numbers.add(part)
        else:
            cleaned_parts.append(part)
    
    text = ' '.join(cleaned_parts)
    
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def parse_episode_number(ep_str: str) -> float:
    if not ep_str:
        return 99999.0
    
    ep_str = str(ep_str).strip()
    
    if ep_str.lower() == "special" or ep_str == "0":
        return 0.0
    
    match = re.search(r'(\d+(?:\.\d+)?)', ep_str)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    
    return 99999.0

def extract_season_number(text: str) -> int:
    from urllib.parse import unquote
    
    # URL decode first (handles %d9%85 etc.)
    text = unquote(text)
    text_lower = text.lower()
    
    text_normalized = text_lower.replace('-', ' ').replace('_', ' ')
    
    if 'final' in text_normalized or 'نهائي' in text_normalized or 'الأخير' in text_normalized:
        part_match = re.search(r'(?:part|الجزء|جزء)[- ]?(\d+)', text_normalized)
        if part_match:
            return 100 + int(part_match.group(1))
        return 100
    
    # Arabic ordinals mapping (order matters - check longer phrases first!)
    arabic_ordinals = {
        # Teens (11-19) - must come before basic numbers
        'الحادي عشر': 11, 'حادي عشر': 11,
        'الثاني عشر': 12, 'ثاني عشر': 12,
        'الثالث عشر': 13, 'ثالث عشر': 13,
        'الرابع عشر': 14, 'رابع عشر': 14,
        'الخامس عشر': 15, 'خامس عشر': 15,
        'السادس عشر': 16, 'سادس عشر': 16,
        'السابع عشر': 17, 'سابع عشر': 17,
        'الثامن عشر': 18, 'ثامن عشر': 18,
        'التاسع عشر': 19, 'تاسع عشر': 19,
        'الحادي والعشرون': 21, 'حادي والعشرون': 21,
        'الثاني والعشرون': 22, 'ثاني والعشرون': 22,
        'العشرون': 20, 'عشرون': 20,
        'العاشر': 10, 'عاشر': 10,
        'التاسع': 9, 'تاسع': 9,
        'الثامن': 8, 'ثامن': 8,
        'السابع': 7, 'سابع': 7,
        'السادس': 6, 'سادس': 6,
        'الخامس': 5, 'خامس': 5,
        'الرابع': 4, 'رابع': 4,
        'الثالث': 3, 'ثالث': 3,
        'الثاني': 2, 'ثاني': 2,
        'الاول': 1, 'الأول': 1, 'اول': 1,
    }
    
    for ordinal in sorted(arabic_ordinals.keys(), key=len, reverse=True):
        if ordinal in text_normalized:
            return arabic_ordinals[ordinal]
    
    match = re.search(r'(?:الموسم|season)[- ]?(\d+)|(?:^|/)s(\d+)(?:$|/)', text_normalized)
    if match:
        for group in match.groups():
            if group:
                return int(group)
    
    return 1

def extract_season_part(text: str) -> Optional[str]:
    from urllib.parse import unquote
    text = unquote(text).lower()
    
    part_match = re.search(r'(?:part|الجزء|جزء)[- ]?(\d+)|p(\d+)', text, re.IGNORECASE)
    if part_match:
        part_num = part_match.group(1) or part_match.group(2)
        return f"Part {part_num}"
    
    if 'الجزء الثاني' in text or 'part 2' in text or 'cour 2' in text:
        return "Part 2"
    elif 'الجزء الاول' in text or 'part 1' in text or 'cour 1' in text:
        return "Part 1"
    
    return None

class TopCinemaScraper:
    
    def __init__(self, base_url: Optional[str] = None):
        self.session = requests.Session(impersonate="chrome120")
        
        if base_url:
            self.base_url = base_url.rstrip('/')
        else:
            self.base_url = "https://topcinema.media"
        
        self.session.headers.update(HEADERS)
    
    def _discover_domain(self) -> str:
        try:
            response = self.session.get(BASE_URL, timeout=10)
            final_url = response.url.rstrip('/')
            return final_url
        except Exception:
            return BASE_URL.rstrip('/')
    
    def _get_endpoint(self, endpoint_type: str) -> str:
        base = self.base_url.rstrip('/')
        endpoints = {
            "search": f"{base}/wp-content/themes/movies2023/Ajaxat/Searching.php",
            "server": f"{base}/wp-content/themes/movies2023/Ajaxat/Single/Server.php",
            "trailer": f"{base}/wp-content/themes/movies2023/Ajaxat/Home/LoadTrailer.php"
        }
        return endpoints.get(endpoint_type, "")
    
    def search(self, query: str, content_type: Optional[str] = None) -> List[Dict]:
        try:
            data = {"search": query, "type": "all"}
            ajax_headers = AJAX_HEADERS.copy()
            ajax_headers["Referer"] = self.base_url
            ajax_headers["Origin"] = self.base_url
            
            response = self.session.post(
                self._get_endpoint("search"),
                data=data,
                headers=ajax_headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            
            for item in soup.select(".Small--Box"):
                result = self._parse_search_result(item)
                if result:
                    if content_type and result.get("type") != content_type:
                        continue
                    results.append(result)
            
            return results
        
        except Exception as e:
            print(f"[ERROR] Search failed: {e}")
            return []
    
    def _parse_search_result(self, item) -> Optional[Dict]:
        try:
            link = item.find("a")
            if not link or not link.get("href"):
                return None
            
            url = link["href"]
            
            title_elem = item.select_one(".title") or item.select_one(".Title") or item.find("h3")
            title = clean_text(title_elem.get_text()) if title_elem else clean_text(link.get("title", "Unknown"))
            
            show_type = "movie"
            if "مسلسل" in url or "/series/" in url or "مسلسل" in title:
                show_type = "series"
            elif "انمي" in url or "/anime/" in url or "انمي" in title:
                show_type = "anime"
            elif "فيلم" in url or "/movie/" in url or "فيلم" in title:
                show_type = "movie"
            
            metadata = {}
            
            quality_candidates = []
            
            ribbon = item.select_one(".ribbon")
            if ribbon:
                quality_candidates.append(clean_text(ribbon.get_text()))
            
            # 2. List items - second item is often quality, but let's scan all
            list_items = item.select("ul.liList li")
            for li in list_items:
                text = clean_text(li.get_text())
                # Check if it looks like quality (contains 1080p, 720p, BluRay, WEB-DL, etc)
                if any(q in text.upper() for q in ['1080P', '720P', '480P', 'BLURAY', 'WEB-DL', 'WEBRIP', 'HDCAM']):
                     quality_candidates.append(text)
                
                # Check for rating (contains star icon or numbers)
                if li.select_one(".fa-star") or "imdb" in li.get("class", []):
                    match = re.search(r'(\d+(?:\.\d+)?)', text)
                    if match:
                        metadata["rating"] = float(match.group(1))
            
            # Prefer longest quality string found (e.g. "1080p BluRay" > "1080p")
            if quality_candidates:
                metadata["quality"] = max(quality_candidates, key=len)
            
            # Year
            year_elem = item.find("span", class_=re.compile("year"))
            if year_elem:
                year_text = clean_text(year_elem.get_text())
                match = re.search(r'(\d{4})', year_text)
                if match:
                    metadata["year"] = int(match.group(1))
            
            # Poster
            img = item.find("img")
            if img:
                poster = img.get("data-src") or img.get("src")
                if poster:
                    metadata["poster"] = poster
            
            return {
                "title": title,
                "url": url,
                "type": show_type,
                "metadata": metadata
            }
        
        except Exception as e:
            print(f"[WARN] Failed to parse search result: {e}")
            return None
    
    def get_show_details(self, url: str) -> Optional[Dict]:
        try:
            decoded_url = unquote(url)
            if "فيلم" in decoded_url or "/movie/" in decoded_url:
                return self.get_movie_details(url)
            elif "انمي" in decoded_url or "/anime/" in decoded_url:
                return self.get_series_details(url, show_type="anime")
            elif "مسلسل" in decoded_url or "/series/" in decoded_url:
                return self.get_series_details(url, show_type="series")
            else:
                result = self.get_series_details(url)
                if result and result.get("seasons"):
                    return result
                return self.get_movie_details(url)
        
        except Exception as e:
            print(f"[ERROR] Failed to get show details: {e}")
            return None
    
    def get_movie_details(self, url: str) -> Optional[Dict]:
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            details = self._parse_metadata(soup, url)
            details["type"] = "movie"
            
            movie_id = self._extract_content_id(url, soup)
            
            base_url = url.rstrip('/')
            watch_url = f"{base_url}/watch/"
            
            if not movie_id:
                try:
                    w_resp = self.session.get(watch_url, timeout=REQUEST_TIMEOUT)
                    if w_resp.status_code == 200:
                        w_soup = BeautifulSoup(w_resp.text, "html.parser")
                        movie_id = self._extract_content_id(watch_url, w_soup)
                        
                        if not details.get("quality"):
                             desc = w_soup.find("meta", {"name": "description"})
                             if desc:
                                 content = desc.get("content", "")
                                 if "بجودة" in content:
                                     match = re.search(r'بجودة\s+([A-Za-z0-9\-]+)', content)
                                     if match:
                                         details["metadata"]["quality"] = match.group(1)
                except Exception as e:
                    pass
            
            if movie_id:
                details["servers"] = self.get_servers(movie_id, watch_url)
            
            return details
        
        except Exception as e:
            print(f"[ERROR] Failed to get movie details: {e}")
            return None
    
    def get_series_details(self, url: str, show_type: str = "series") -> Optional[Dict]:
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            details = self._parse_metadata(soup, url)
            details["type"] = show_type
            
            season_boxes = soup.find_all("div", class_=lambda c: c and "Small--Box" in c and "Season" in c)
            season_links = []
            seen_urls = set()
            
            if season_boxes:
                for box in season_boxes:
                    link = box.find("a", href=True)
                    if link and link.get("href"):
                        season_url = link["href"]
                        if season_url not in seen_urls:
                            seen_urls.add(season_url)
                            season_links.append(season_url)
            
            if not season_links:
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    text = link.get_text().lower()
                    title = link.get("title", "").lower()
                    
                    if (("/ series/" in href or "/anime/" in href) and 
                        ("الموسم" in href or "season" in href or 
                         "season" in text or "الموسم" in title)):
                        if href not in seen_urls:
                            seen_urls.add(href)
                            season_links.append(href)
            
            if not season_links:
                if show_type != "movie":
                     season_links = [url]
            
            seasons = []
            for season_url in season_links:
                season_num = extract_season_number(season_url)
                season_part = extract_season_part(season_url)
                
                if season_num >= 100:
                    if season_part:
                        display_label = f"Final Season {season_part}"
                    else:
                        display_label = "Final Season"
                elif season_part:
                    display_label = f"Season {season_num} {season_part}"
                else:
                    display_label = f"Season {season_num}"
                
                seasons.append({
                    "season_number": season_num,
                    "season_part": season_part,
                    "display_label": display_label,
                    "url": season_url,
                    "poster": None,
                    "episodes": []
                })
            
            seasons.sort(key=lambda s: s.get("season_number", 999))
            details["seasons"] = seasons
            
            return details
        
        except Exception as e:
            print(f"[ERROR] Failed to get series details: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _parse_metadata(self, soup: BeautifulSoup, url: str) -> Dict:
        metadata = {"url": url}
        
        title_elem = soup.find("h1", class_="post-title") or \
                     soup.find("h1") or \
                     soup.find("h2", class_=re.compile("title"))
                     
        if title_elem:
            metadata["title"] = clean_text(title_elem.get_text())
        else:
            metadata["title"] = "Unknown Title"
        
        poster = soup.find("img", class_=re.compile("poster"))
        if poster:
            poster_url = poster.get("data-src") or poster.get("src")
            if poster_url:
                metadata["poster"] = poster_url
        
        synopsis_elem = soup.find("div", class_=re.compile("synopsis|description|story"))
        if synopsis_elem:
            metadata["synopsis"] = clean_text(synopsis_elem.get_text())
        
        rating_elem = soup.find(string=re.compile("IMDb", re.IGNORECASE))
        if rating_elem:
            parent = rating_elem.find_parent()
            if parent:
                rating_text = clean_text(parent.get_text())
                match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                if match:
                    metadata["imdb_rating"] = float(match.group(1))
        
        year_elem = soup.find(string=re.compile("سنة|year", re.IGNORECASE))
        if year_elem:
            parent = year_elem.find_parent()
            if parent:
                year_text = clean_text(parent.get_text())
                match = re.search(r'(\d{4})', year_text)
                if match:
                    metadata["year"] = int(match.group(1))
        
        tax = soup.find('ul', class_='RightTaxContent')
        if tax:
            key_mapping = {
                "قسم المسلسل": "category", "قسم الفيلم": "category", "نوع المسلسل": "genres",
                "نوع الفيلم": "genres", "النوع": "genres", "جودة المسلسل": "quality",
                "جودة الفيلم": "quality", "عدد الحلقات": "episode_count", "توقيت المسلسل": "duration",
                "توقيت الفيلم": "duration", "مدة الفيلم": "duration", "موعد الصدور": "release_year",
                "سنة الانتاج": "release_year", "لغة المسلسل": "language", "لغة الفيلم": "language",
                "دولة المسلسل": "country", "دولة الفيلم": "country", "المخرجين": "directors",
                "المخرج": "directors", "بطولة": "cast"
            }
            
            for li in tax.find_all('li'):
                key_el = li.find('span')
                if key_el:
                    raw_key = key_el.get_text(strip=True).replace(':', '')
                    key = key_mapping.get(raw_key)
                    
                    if key:
                        val_text = ""
                        links = [a.get_text(strip=True) for a in li.find_all('a') if a.get_text(strip=True)]
                        if links:
                            if key in ["genres", "cast", "directors"]:
                                metadata[key] = links
                                val_text = ", ".join(links)
                            else:
                                val_text = links[0]
                                metadata[key] = val_text
                        else:
                            val_text = li.get_text(strip=True).replace(raw_key, '').replace(':', '').strip()
                            metadata[key] = val_text
                        
                        if key == "release_year" and "year" not in metadata:
                             match = re.search(r'(\d{4})', val_text)
                             if match: metadata["year"] = int(match.group(1))

        if "quality" not in metadata:
             desc_elem = soup.find("meta", {"name": "description"}) or soup.find("meta", property="og:description")
             if desc_elem:
                 content = desc_elem.get("content", "")
                 match = re.search(r'(?:بجودة|quality)\s+([A-Za-z0-9\-\.]+)', content, re.IGNORECASE)
                 if match:
                     metadata["quality"] = match.group(1)

        if "genres" not in metadata:
            genres = []
            genre_links = soup.find_all("a", href=re.compile("genre"))
            for link in genre_links:
                genre = clean_text(link.get_text())
                if genre:
                    genres.append(genre)
            if genres:
                metadata["genres"] = genres
        
        trailer_button = soup.find("a", class_=re.compile("trailer"))
        if trailer_button and trailer_button.get("data-url"):
            trailer_url = self._get_trailer_url(url, trailer_button["data-url"])
            if trailer_url:
                metadata["trailer"] = trailer_url
        
        return metadata
    
    def _parse_season(self, season_url: str) -> Optional[Dict]:
        try:
            season_num = extract_season_number(season_url)
            
            # Prepare list URL (use /list/ endpoint)
            base_url = season_url.rstrip('/')
            
            # Fix: Check if we are already on a list page or need to convert
            # Many anime pages like /anime/one-piece/ need /list/ to show all episodes
            # But specific season pages might not.
            # safe assumption: try fetching the base URL first to see if it has episodes
            # If not, try /list/
            
            # Let's try to detect if we need /list/
            # If the URL is `.../anime-name/` or `.../series-name/`, it often needs `/list` for full pagination
            # But if it is `.../season-1/`, it might not.
            
            use_list_endpoint = False
            if not base_url.endswith('/list'):
                 # Heuristic: if page doesn't have episodes, we might need /list
                 # But we can't fetch twice efficiently.
                 # Python scraper logic usually enforced /list for series to get linear list.
                 use_list_endpoint = True
                 list_url_candidate = base_url + '/list'
            else:
                 list_url_candidate = base_url

            # Collect all episodes from all pages
            all_episodes = []
            seen_urls = set()
            page = 1
            max_pages = 50  # Safety limit
            
            # Start loop
            first_try_url = list_url_candidate if use_list_endpoint else base_url
            
            # If first try fails (404), fallback to base_url without /list
            fallback_mode = False

            while page <= max_pages:
                # Build page URL
                if page == 1:
                    current_url = first_try_url if not fallback_mode else base_url
                else:
                    if fallback_mode:
                        current_url = f"{base_url}/?page={page}"
                    else:
                        current_url = f"{first_try_url}/?page={page}"
                
                try:
                    response = self.session.get(current_url, timeout=REQUEST_TIMEOUT)
                    
                    # If /list 404s, try fallback immediately
                    if response.status_code == 404 and page == 1 and use_list_endpoint and not fallback_mode:
                        fallback_mode = True
                        continue
                        
                    response.raise_for_status()
                    
                    soup = BeautifulSoup(response.text, "html.parser")
                    
                    # Find episode links - Method 1: Look for .allepcont .row > a
                    episode_anchors = soup.select(".allepcont .row > a")
                    
                    # Method 2: Broader search if Method 1 fails
                    if not episode_anchors:
                        episode_anchors = soup.select(".allepcont a")
                    
                    # Method 3: Look for any links with episode indicators
                    if not episode_anchors:
                        episode_anchors = []
                        for link in soup.find_all("a", href=True):
                            title = link.get("title", "")
                            text = link.get_text()
                            href = link["href"]
                            
                            # Check if it looks like an episode link
                            # Added: specific check for class "overlay" which is common in grid layouts
                            if (link.select_one(".epnum") or 
                                "الحلقة" in title or "Episode" in title or
                                "الحلقة" in text or "Episode" in text or
                                "episode" in href.lower() or 
                                ("watch" in href and "button" in link.get("class", []))):
                                episode_anchors.append(link)
                            
                            # Fallback for grid layouts where <a> wraps everything
                            if not episode_anchors and ("anime" in href or "series" in href) and "watch" not in href:
                                # This handles cases where episodes are just listed as regular links in a grid
                                # But we must be careful not to pick up unrelated links
                                if re.search(r'\d+', text) or re.search(r'\d+', title):
                                     # Weak check but might be necessary for some layouts
                                     pass

                    # SPECIAL FIX FOR TOPCINEMA ANIME SEASONS
                    # Sometimes the season page lists episodes in `.Episodes--Seasons--Episodes a`
                    if not episode_anchors:
                         episode_anchors = soup.select(".Episodes--Seasons--Episodes a")

                    # If no episodes found, we've reached the end
                    if not episode_anchors:
                        break
                    
                    # Parse episodes from this page
                    page_episodes = []
                    for anchor in episode_anchors:
                        href = anchor.get("href")
                        if not href or href in seen_urls:
                            continue
                        seen_urls.add(href)
                        
                        episode_data = self._parse_episode_link(anchor, href)
                        if episode_data:
                            page_episodes.append(episode_data)
                    
                    # If no new episodes found, we're done
                    if not page_episodes:
                        break
                    
                    all_episodes.extend(page_episodes)
                    
                    # Check if there's a next page by looking for pagination
                    next_page = soup.select_one('.page-numbers.next') or \
                                soup.select_one(f'a[href*="page={page + 1}"]')
                    
                    if not next_page:
                        break
                    
                    page += 1
                    
                except Exception as e:
                    # If page fetch fails, stop pagination
                    if page == 1:
                        raise  # Re-raise if first page fails
                    break
            
            # Sort episodes by number
            all_episodes.sort(key=lambda e: parse_episode_number(e.get("episode_number", "")))
            
            # Get poster from first page
            response = self.session.get(base_url + '/', timeout=REQUEST_TIMEOUT)
            soup = BeautifulSoup(response.text, "html.parser")
            poster = None
            poster_img = soup.find("img", class_=re.compile("poster"))
            if poster_img:
                poster = poster_img.get("data-src") or poster_img.get("src")
            
            return {
                "season_number": season_num,
                "poster": poster,
                "episodes": all_episodes
            }
        
        except Exception as e:
            print(f"[WARN] Failed to parse season: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _parse_episode_link(self, link_elem, url: str) -> Optional[Dict]:
        try:
            if url and not url.startswith(('http:', 'https:')):
                url = urljoin(self.base_url, url)
            
            if '/category/' in url or '/genre/' in url:
                return None
            
            ep_text = clean_text(link_elem.get_text())
            title_attr = link_elem.get("title", "")
            
            # Try URL first (more reliable) - Arabic or English
            ep_match = re.search(r'(?:الحلقة|episode|ep)[- ]?(\d+(?:\.\d+)?)', url, re.IGNORECASE)
            
            # Detect special episodes
            is_special = False
            special_type = None
            if re.search(r'(?:ova|special|movie|خاص)', url, re.IGNORECASE):
                is_special = True
                if 'ova' in url.lower():
                    special_type = 'OVA'
                elif 'movie' in url.lower():
                    special_type = 'Movie'
                else:
                    special_type = 'Special'
            
            if not ep_match:
                # Try title attribute
                ep_match = re.search(r'(\d+(?:\.\d+)?)', title_attr)
            if not ep_match:
                # Try text content
                ep_match = re.search(r'(\d+(?:\.\d+)?)', ep_text)
            
            # Handle episodes without numbers
            if not ep_match:
                if is_special:
                    ep_num_str = special_type
                    display_num = f"[{special_type}]"
                else:
                    # Extract any meaningful text or use URL hash
                    ep_num_str = "0"
                    display_num = "[No Number]"
            else:
                ep_num_str = None
                for group in ep_match.groups():
                    if group:
                        ep_num_str = group
                        break
                
                if not ep_num_str:
                    return None
                
                display_num = f"{special_type} {ep_num_str}" if special_type else ep_num_str
            
            # Clean the title - remove episode numbers from title text
            clean_title = clean_arabic_title(ep_text or title_attr or "")
            # Remove the episode number from title if it's just duplicating
            clean_title = re.sub(r'\b' + re.escape(str(ep_num_str)) + r'\b', '', clean_title).strip()
            if not clean_title or clean_title.isspace():
                clean_title = ""
            
            # Don't fetch servers during initial parsing - do it lazily when needed
            return {
                "episode_number": ep_num_str,
                "display_number": display_num,
                "title": clean_title,
                "url": url,
                "is_special": is_special,
                "servers": []  # Will be populated on demand
            }
        
        except Exception:
            return None
    
    # --- Servers ---
    
    def _extract_vidtube_url(self, embed_url: str) -> Optional[str]:
        """Extract actual video URL from embed page using Python VidTubeProcessor."""
        from .processor import VidTubeProcessor
        
        # Check if it's actually a vidtube domain
        vidtube_domains = ['vidtube.one', 'vidtube.pro', 'vidtube.me', 'vidtube.to']
        if not any(domain in embed_url.lower() for domain in vidtube_domains):
            return None
            
        try:
             processor = VidTubeProcessor(self.session)
             return processor.extract(embed_url)
        except Exception as e:
            return None

    def get_servers(self, content_id: str, referer: str, max_servers: int = 10) -> List[Dict]:
        servers = []
        
        headers = AJAX_HEADERS.copy()
        headers["Referer"] = referer
        headers["Origin"] = self.base_url
        
        for i in range(max_servers):
            try:
                data = {"id": str(content_id), "i": str(i)}
                response = self.session.post(
                    self._get_endpoint("server"),
                    headers=headers,
                    data=data,
                    timeout=5
                )
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    iframe = soup.find("iframe")
                    
                    if iframe and iframe.get("src"):
                        embed_url = iframe["src"].strip()
                        
                        if 'vidtube' in embed_url.lower():
                            # Extract actual video URL using script.mjs logic (internal method)
                            video_url = self._extract_vidtube_url(embed_url)
                            
                            if video_url:
                                servers.append({
                                    "name": f"VidTube Server {i+1}",
                                    "server_number": i,
                                    "embed_url": embed_url,
                                    "video_url": video_url
                                })
                                break
                            
            except Exception as e:
                pass
        
        return servers
    
    def _extract_content_id(self, url: str, soup: Optional[BeautifulSoup] = None) -> Optional[str]:
        try:
            if not soup:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
                soup = BeautifulSoup(response.text, "html.parser")
            
            # 1. Check for Li elements in the server list (MOST RELIABLE for movies)
            for selector in ["ul.servers-list li", ".server--item", "li[data-server]"]:
                for li in soup.select(selector):
                    if li.get("data-id"):
                        return li.get("data-id")

            # 2. Look for data-id attributes generally
            for elem in soup.find_all(attrs={"data-id": True}):
                content_id = elem.get("data-id")
                if content_id and content_id.isdigit():
                    return content_id
            
            # 3. Look for postid class (WordPress common)
            # <div class="post-1234 ...">
            for cls in soup.find_all(class_=re.compile(r"post-\d+")):
                for c in cls.get("class", []):
                    if c.startswith("post-"):
                        try:
                            pid = c.split("-")[1]
                            if pid.isdigit():
                                return pid
                        except:
                            pass
                            
            # 4. Fallback: search in scripts
            scripts = soup.find_all("script")
            for script in scripts:
                if script.string:
                    # id="123" or id='123'
                    match = re.search(r'id["\']?\s*[:=]\s*["\']?(\d+)', script.string)
                    if match:
                        return match.group(1)
                    # p=123 (shortlink in script)
                    match = re.search(r'p=(\d+)', script.string)
                    if match:
                         return match.group(1)
                    # "post_id": 123
                    match = re.search(r'"post_id"\s*:\s*(\d+)', script.string)
                    if match:
                        return match.group(1)
                    # var post_id = 123;
                    match = re.search(r'var\s+post_id\s*=\s*(\d+)', script.string)
                    if match:
                        return match.group(1)

            # 5. Check link rel=shortlink
            shortlink = soup.find("link", rel="shortlink")
            if shortlink and shortlink.get("href"):
                 match = re.search(r'p=(\d+)', shortlink["href"])
                 if match:
                     return match.group(1)
            
            # 6. Check for specific TopCinema player div
            play_div = soup.find(id="play")
            if play_div and play_div.get("data-id"):
                return play_div.get("data-id")

            return None
        
        except Exception:
            return None
    
    def _get_trailer_url(self, page_url: str, form_url: str) -> Optional[str]:
        try:
            data = f"href={quote(form_url, safe=':/')}"
            headers = AJAX_HEADERS.copy()
            headers["Referer"] = quote(page_url, safe=':/')
            
            response = self.session.post(
                self._get_endpoint("trailer"),
                headers=headers,
                data=data.encode('utf-8'),
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            iframe = soup.find("iframe")
            
            if iframe and iframe.get("src"):
                trailer_url = iframe["src"].strip()
                if trailer_url.startswith(('http://', 'https://')):
                    return trailer_url
            
            return None
        
        except Exception:
            return None
    
    def fetch_season_episodes(self, season: Dict) -> List[Dict]:
        if season.get("episodes"):
            return season["episodes"]
        
        season_url = season.get("url")
        if not season_url:
            return []
        
        season_data = self._parse_season(season_url)
        if season_data and season_data.get("episodes"):
            season["episodes"] = season_data["episodes"]
            season["poster"] = season_data.get("poster")
            return season["episodes"]
        
        return []
    
    def fetch_episode_servers(self, episode: Dict) -> List[Dict]:
        if episode.get("servers"):
            return episode["servers"]
        
        url = episode.get("url")
        if not url:
            return []
        
        episode_id = self._extract_content_id(url)
        if not episode_id:
            return []
        
        servers = self.get_servers(episode_id, url)
        episode["servers"] = servers
        return servers
    
    def search_movies(self, query: str) -> List[Dict]:
        return self.search(query, content_type="movie")
    
    def search_series(self, query: str) -> List[Dict]:
        return self.search(query, content_type="series")
    
    def search_anime(self, query: str) -> List[Dict]:
        return self.search(query, content_type="anime")
