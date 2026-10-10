import base64
import re
import string

import requests

from . import utils, cache

LINK_RE = re.compile(r'^(?:https?:)?//([a-z0-9.\-]+)/([a-z]+)/(\d+)/([0-9a-z]+)/(\d+p)(?:.*)$')

_lo, _up = string.ascii_lowercase, string.ascii_uppercase
_TABLE = str.maketrans(_lo + _up, _lo[18:] + _lo[:18] + _up[18:] + _up[:18])


class KodikError(Exception):
    def __init__(self, code, detail=''):
        super().__init__('%s %s' % (code, detail))
        self.code = code
        self.detail = detail


# ----------------------------------------------------------------- search
def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _clean_title(title):
    t = title or '?'
    if t.lower().endswith('.subtitles'):
        t = t[:-len('.subtitles')]
    return t.strip()


def build_entries(results):
    """Group kodik results by translation -> [{id,title,sub,episodes,movie_link}]"""
    out = {}
    for r in results:
        t = r.get('translation') or {}
        raw_title = t.get('title') or '?'
        is_sub = t.get('type') == 'subtitles' or raw_title.lower().endswith('.subtitles')
        episodes = []
        seasons = r.get('seasons') or {}
        for s_key in sorted(seasons, key=_int):
            eps = (seasons[s_key] or {}).get('episodes') or {}
            for e_key in sorted(eps, key=_int):
                link = eps[e_key]
                if isinstance(link, dict):
                    link = link.get('link')
                if link:
                    episodes.append({'season': _int(s_key), 'num': _int(e_key), 'link': link})
        movie_link = None
        link = r.get('link') or ''
        if not episodes and '/video/' in link:
            movie_link = link
        if not episodes and not movie_link:
            continue
        entry = {
            'id': str(t.get('id', raw_title)),
            'title': _clean_title(raw_title),
            'sub': bool(is_sub),
            'episodes': episodes,
            'movie_link': movie_link,
        }
        old = out.get(entry['id'])
        if old is None or len(entry['episodes']) > len(old['episodes']):
            out[entry['id']] = entry
    return sorted(out.values(), key=lambda e: (e['sub'], e['title'].lower()))


def get_entries(shiki_id):
    key = 'kodik:%s' % shiki_id
    cached = cache.get(key, 300)
    if cached is not None:
        utils.debug('kodik search cache HIT for shikimori_id=%s (%d entries)' % (shiki_id, len(cached)))
        return cached
    token = utils.get_str('kodik_token').strip()
    if not token:
        raise KodikError('no_token')
    base = (utils.get_str('kodik_base') or 'https://kodik-api.com').rstrip('/')
    utils.debug('kodik GET %s/search?token=***&shikimori_id=%s&with_episodes=true&limit=100' % (base, shiki_id))
    r = requests.get(base + '/search',
                     params={'token': token, 'shikimori_id': shiki_id, 'with_episodes': 'true', 'limit': 100},
                     headers={'User-Agent': utils.UA}, timeout=20)
    utils.debug('kodik search -> HTTP %s, %d bytes' % (r.status_code, len(r.content)))
    try:
        data = r.json()
    except ValueError:
        data = {}
        utils.debug('kodik search: body is not JSON: %r' % r.text[:300])
    if r.status_code != 200 or data.get('error'):
        utils.debug('kodik search error: %r' % (data.get('error') or r.text[:300]))
        raise KodikError('http', str(r.status_code))
    results = data.get('results') or []
    utils.debug('kodik search: total=%s results=%d' % (data.get('total'), len(results)))
    entries = build_entries(results)
    for e in entries:
        utils.debug('  entry id=%s title=%r sub=%s episodes=%d movie=%s' % (
            e['id'], e['title'], e['sub'], len(e['episodes']), e['movie_link']))
    cache.set(key, entries)
    return entries


# ---------------------------------------------------------------- resolve
def decrypt(src):
    s = src.translate(_TABLE)
    s += '=' * (-len(s) % 4)
    url = base64.b64decode(s).decode('utf-8')
    if url.startswith('//'):
        url = 'https:' + url
    if '.m3u8' not in url:
        url = url.rstrip('/') + '/manifest.m3u8'
    return url


def _parse_links(data):
    links = {}
    for q, items in ((data or {}).get('links') or {}).items():
        if not items:
            continue
        src = items[0].get('src') or ''
        try:
            if src.startswith('http') or src.startswith('//'):
                url = 'https:' + src if src.startswith('//') else src
            else:
                url = decrypt(src)
        except Exception as e:
            utils.log('decrypt failed for %sp: %s' % (q, e))
            continue
        links[_int(q)] = url
        utils.debug('  quality %sp -> %s' % (q, url))
    return links


def resolve(link):
    """kodik player link -> {quality(int): m3u8 url}"""
    if link.startswith('//'):
        link = 'https:' + link
    m = LINK_RE.match(link)
    if not m:
        utils.debug('resolve: link does not match regex: %s' % link)
        raise KodikError('bad_link', link)
    host, typ, vid, hsh, quality = m.groups()
    utils.debug('resolve: link=%s -> host=%s type=%s id=%s hash=%s q=%s' % (link, host, typ, vid, hsh, quality))
    endpoint = 'https://%s/ftor' % host
    headers = {'User-Agent': utils.UA, 'Referer': 'https://%s/%s/%s/%s/%s' % (host, typ, vid, hsh, quality),
               'X-Requested-With': 'XMLHttpRequest'}
    links = {}
    try:
        utils.debug('ftor POST %s data=type=%s&hash=%s&id=%s' % (endpoint, typ, hsh, vid))
        r = requests.post(endpoint, data={'type': typ, 'hash': hsh, 'id': vid}, headers=headers, timeout=15)
        utils.debug('ftor POST -> HTTP %s, %d bytes, body[:300]=%r' % (r.status_code, len(r.content), r.text[:300]))
        if r.status_code == 200:
            links = _parse_links(r.json())
    except (requests.RequestException, ValueError) as e:
        utils.log('ftor POST failed: %s' % e)
    if not links:  # fallback: same request as in the Go downloader (GET)
        utils.debug('ftor POST gave no links, falling back to GET')
        r = requests.get(endpoint, params={'hash': hsh, 'id': vid, 'quality': quality, 'type': typ},
                         headers=headers, timeout=15)
        utils.debug('ftor GET -> HTTP %s, %d bytes, url=%s body[:300]=%r' % (r.status_code, len(r.content), r.url, r.text[:300]))
        r.raise_for_status()
        links = _parse_links(r.json())
    if not links:
        raise KodikError('no_links')
    utils.debug('resolved qualities: %s' % sorted(links))
    return links
