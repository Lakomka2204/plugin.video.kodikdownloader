import json
import time


import requests

from . import utils, cache

_last = 0.0


def base():
    d = (utils.get_str('shiki_domain_custom') or '').strip() or utils.get_str('shiki_domain') or 'https://shikimori.one'
    if not d.startswith('http'):
        d = 'https://' + d
    return d.rstrip('/')


def _request(path, params):
    global _last
    for attempt in range(3):
        wait = 0.22 - (time.time() - _last)  # stay under 5 req/s
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        t0 = time.time()
        utils.debug('shikimori GET %s%s params=%s (attempt %d)' % (base(), path, params, attempt + 1))
        r = requests.get(base() + path, params=params,
                         headers={'User-Agent': utils.UA, 'Accept': 'application/json'}, timeout=15)
        utils.debug('shikimori -> HTTP %s, %d bytes, %.2fs, final url=%s' % (
            r.status_code, len(r.content), time.time() - t0, r.url))
        if r.status_code == 429:
            time.sleep(1.2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise requests.HTTPError('Shikimori: too many requests (429)')


def _get(path, params=None):
    ttl = utils.get_int('cache_ttl', 30) * 60
    key = base() + path + json.dumps(params or {}, sort_keys=True)
    data = cache.get(key, ttl)
    if data is None:
        utils.debug('shikimori cache MISS %s (ttl=%ss)' % (path, ttl))
        data = _request(path, params)
        cache.set(key, data)
    else:
        utils.debug('shikimori cache HIT %s' % path)
    return data


def animes(page=1, **filters):
    params = {
        'page': page,
        'limit': utils.get_int('page_size', 20) or 20,
        'censored': 'false' if utils.adult_allowed() else 'true',
    }
    params.update(filters)
    return _get('/api/animes', params)


def get_anime(anime_id):
    return _get('/api/animes/%s' % anime_id)


def image_url(a):
    img = ((a or {}).get('image') or {}).get('original') or ''
    if img.startswith('/'):
        return base() + img
    return img
