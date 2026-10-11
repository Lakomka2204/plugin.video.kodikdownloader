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


_CAST = 15
_GQL_FIELDS = """id kind score status episodes episodesAired duration rating description licensors
  airedOn { date } releasedOn { date } genres { name russian } studios { name }
  characterRoles { rolesEn rolesRu character { name russian poster { originalUrl } } }
  personRoles { rolesEn rolesRu person { name russian } }"""
_STAFF = ('Director', 'Script', 'Original Creator', 'Series Composition')


def _throttle():
    global _last
    wait = 0.22 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()


def _norm_gql(an):
    """GraphQL anime -> (REST-shaped anime dict, trimmed REST-shaped roles list)."""
    def date(x):
        return (x or {}).get('date')
    full = {
        'id': int(an['id']), 'kind': an.get('kind'), 'status': an.get('status'), 'score': an.get('score'),
        'episodes': an.get('episodes'), 'episodes_aired': an.get('episodesAired'), 'duration': an.get('duration'),
        'rating': an.get('rating'), 'description': an.get('description'), 'licensors': an.get('licensors') or [],
        'aired_on': date(an.get('airedOn')), 'released_on': date(an.get('releasedOn')),
        'genres': [{'name': g.get('name'), 'russian': g.get('russian')} for g in an.get('genres') or []],
        'studios': [{'name': s.get('name')} for s in an.get('studios') or []],
    }
    roles = []
    chars = sorted(an.get('characterRoles') or [], key=lambda r: 'Main' not in (r.get('rolesEn') or []))
    for r in chars[:_CAST]:
        c = r.get('character') or {}
        roles.append({'roles': r.get('rolesEn') or [], 'roles_russian': r.get('rolesRu') or [],
                      'character': {'name': c.get('name'), 'russian': c.get('russian'),
                                    'image': {'original': (c.get('poster') or {}).get('originalUrl')}},
                      'person': None})
    for r in an.get('personRoles') or []:
        en = r.get('rolesEn') or []
        if any(x in en for x in _STAFF):
            p = r.get('person') or {}
            roles.append({'roles': en, 'roles_russian': r.get('rolesRu') or [], 'character': None,
                          'person': {'name': p.get('name'), 'russian': p.get('russian')}})
    return full, roles


def _graphql(ids):
    out = {}
    for k in range(0, len(ids), 50):
        chunk = ids[k:k + 50]
        query = '{ animes(ids: "%s", limit: %d) { %s } }' % (','.join(str(int(i)) for i in chunk), len(chunk),
                                                              _GQL_FIELDS)
        _throttle()
        t0 = time.time()
        r = requests.post(base() + '/api/graphql', json={'query': query},
                          headers={'User-Agent': utils.UA, 'Accept': 'application/json'}, timeout=30)
        utils.debug('shikimori GraphQL ids=%s -> HTTP %s, %d bytes, %.2fs' % (
            chunk, r.status_code, len(r.content), time.time() - t0))
        data = r.json()
        if data.get('errors'):
            raise ValueError('graphql errors: %r' % (data['errors'],))
        r.raise_for_status()
        for an in (data.get('data') or {}).get('animes') or []:
            full, roles = _norm_gql(an)
            out[full['id']] = (full, roles)
    return out


def details(ids):
    """{id: (full anime dict, roles)} for the catalog pages. Never raises.
    One GraphQL request for everything not cached; if that fails, REST per anime (no cast)."""
    ttl = utils.get_int('cache_ttl', 30) * 60
    out, missing = {}, []
    for i in ids:
        c = cache.get('detail:%s:%s' % (base(), i), ttl)
        if c is not None:
            out[i] = (c['a'], c['roles'])
        else:
            missing.append(i)
    if not missing:
        return out
    got = {}
    try:
        got = _graphql(missing)
    except Exception:
        utils.log_exc('shikimori graphql details, falling back to REST')
    for i in missing:
        try:
            full, roles = got[i] if i in got else (get_anime(i), [])
        except Exception:
            utils.log_exc('shikimori details(%s)' % i)
            continue
        cache.set('detail:%s:%s' % (base(), i), {'a': full, 'roles': roles})
        out[i] = (full, roles)
    utils.debug('details: %d requested, %d cached, %d graphql, %d total' % (
        len(ids), len(ids) - len(missing), len(got), len(out)))
    return out


def image_url(a):
    img = ((a or {}).get('image') or {}).get('original') or ''
    if img.startswith('/'):
        return base() + img
    return img
