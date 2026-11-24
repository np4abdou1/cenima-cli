"""Subtitle track discovery and language preference helpers."""

import re


def normalize_language(value):
    value = str(value or '').strip().lower()
    aliases = {'ara': 'ar', 'arabic': 'ar', 'eng': 'en', 'english': 'en',
               'fre': 'fr', 'fra': 'fr', 'french': 'fr', 'deu': 'de', 'ger': 'de'}
    return aliases.get(value, value[:2] if len(value) >= 2 else '')


def select_subtitle_track(tracks, preferred='ar'):
    usable = [track for track in tracks if track.get('type') == 'sub']
    if not usable:
        return None
    wanted = normalize_language(preferred)
    for track in usable:
        lang = normalize_language(track.get('lang') or track.get('language'))
        if lang == wanted:
            return track
    for track in usable:
        title = str(track.get('title') or '').lower()
        if wanted and wanted in title:
            return track
    return usable[0]


def mpv_subtitle_args(preferred='ar'):
    language = normalize_language(preferred)
    if language:
        return [f'--slang={language},en,eng,und', '--sub-auto=fuzzy']
    return ['--sub-auto=fuzzy']
