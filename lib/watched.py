"""Local watch marks. Keyed by (shikimori_id, Kodik episode number), NOT by voice-over.

watched.json (lives in addon_data, separate from the cache so "Clear cache" does not wipe it):
{"v": 1, "anime": {"<shikimori_id>": {
    "name": str, "poster": str, "count": int, "ts": float,
    "eps": {"<kodik episode number>": {"w": bool, "pos": int, "total": int, "ts": float, "synced": bool}}}}}

"synced" is for the future Shikimori sync (False = changed locally, not sent yet).
Plugin and service both write this file (read-modify-write, atomic replace). Races are unlikely
(one writer at a time in practice) and are not guarded.
"""
import json
import os
import time
from urllib.parse import parse_qs

import xbmc
import xbmcgui

from . import utils

_NOW = utils.ID + '.nowplaying'


def ep_key(season, num):
    """One place that defines the mark key. Kodik numbers episodes from 1 per shikimori_id.
    If an entry ever has several seasons under one id, change this to '%s:%s'."""
    return str(int(num))


# ---------------------------------------------------------------- storage
def _path():
    os.makedirs(utils.PROFILE, exist_ok=True)
    return os.path.join(utils.PROFILE, 'watched.json')


def _load():
    try:
        with open(_path(), 'r', encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get('anime'), dict):
            return d
    except Exception:
        pass
    return {'v': 1, 'anime': {}}


def _save(d):
    p = _path()
    tmp = p + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        os.replace(tmp, p)
    except Exception as e:
        utils.log('watched write failed: %s' % e)


def _anime(d, sid):
    a = d['anime'].setdefault(str(sid), {})
    a.setdefault('eps', {})
    return a


def clear():
    try:
        os.remove(_path())
    except OSError:
        pass


# ------------------------------------------------------------------ reads
def get_anime(sid):
    return _load()['anime'].get(str(sid), {})


def get_eps(sid):
    return get_anime(sid).get('eps', {})


def get_ep(sid, num):
    return get_eps(sid).get(str(num), {})


def continue_target(eps, nums, last=None):
    """Episode number to continue from, or None.
    Marks decide first; if they give nothing, fall back to the episode that was started last."""
    t = _target(eps, nums)
    if t is None and last and last.get('num') in nums and not eps.get(str(last['num']), {}).get('w'):
        return last['num']
    return t


def _target(eps, nums):
    """Last touched episode unfinished -> that one; finished -> first unwatched after it."""
    recs = {int(k): e for k, e in eps.items() if k.isdigit()}
    if not recs:
        return None
    ln = max(recs, key=lambda n: recs[n].get('ts', 0))
    le = recs[ln]
    if not le.get('w') and le.get('pos', 0) > 0:
        return ln if ln in nums else None
    if le.get('w'):
        base = ln
    else:  # last touched was unmarked: fall back to the highest watched episode
        ws = [n for n, e in recs.items() if e.get('w')]
        if not ws:
            return None
        base = max(ws)
    for n in nums:
        if n > base and not recs.get(n, {}).get('w'):
            return n
    return None


def next_episode(a):
    """Same as continue_target, but for the 'Continue watching' menu where the episode list is not loaded."""
    eps = a.get('eps', {})
    last = a.get('last') or {}
    known = [int(k) for k in eps if k.isdigit()] + ([last['num']] if last.get('num') else [])
    lo = a.get('lo') or 1
    hi = a.get('hi') or (max(known) + 1 if known else 1)
    return continue_target(eps, range(lo, hi + 1), last)


def recent(limit=30):
    """Anime with history, newest first; fully watched ones are hidden."""
    rows = []
    for sid, a in _load()['anime'].items():
        eps = a.get('eps', {})
        if not a.get('last') and not any(e.get('w') or e.get('pos', 0) > 0 for e in eps.values()):
            continue
        done = sum(1 for e in eps.values() if e.get('w'))
        if a.get('count') and done >= a['count']:
            continue
        rows.append((a.get('ts', 0), sid, a))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(sid, a) for _, sid, a in rows[:limit]]


# ----------------------------------------------------------------- writes
def set_meta(sid, name, poster, count, lo=None, hi=None):
    """Remember title/poster/episode count and number range for the 'Continue watching' menu.
    Empty values never overwrite."""
    d = _load()
    a = _anime(d, sid)
    changed = False
    for k, v in (('name', name), ('poster', poster), ('count', count), ('lo', lo), ('hi', hi)):
        if v and a.get(k) != v:
            a[k] = v
            changed = True
    if changed:
        _save(d)


def save_progress(sid, num, pos, total, threshold, ended=False):
    """Called by the service. Returns True if the episode is (now) watched."""
    d = _load()
    a = _anime(d, sid)
    e = a['eps'].get(str(num), {})
    if e.get('w'):
        return True  # re-watching a finished episode changes nothing
    done = ended or (total > 0 and pos * 100.0 / total >= threshold)
    if not done and pos < 10:
        return False  # opened and closed right away: do not disturb 'continue'
    e = a['eps'].setdefault(str(num), {})
    e.update(w=bool(done), pos=0 if done else int(pos), total=int(total), ts=time.time(), synced=False)
    a['ts'] = e['ts']
    _save(d)
    return bool(done)


def touch(sid, num):
    """Plugin side, at playback start: remember the episode, so 'Continue watching' works even if the
    viewing is shorter than the service's 10 s minimum or the service is not running."""
    d = _load()
    a = _anime(d, sid)
    a['last'] = {'num': int(num), 'ts': time.time()}
    a['ts'] = a['last']['ts']
    _save(d)


def mark(sid, nums, watched):
    d = _load()
    a = _anime(d, sid)
    now = time.time()
    for n in nums:
        k = str(n)
        if watched:
            a['eps'].setdefault(k, {}).update(w=True, pos=0, ts=now, synced=False)
        elif k in a['eps']:
            a['eps'][k].update(w=False, pos=0, ts=now, synced=False)
    a['ts'] = now
    _save(d)


# ------------------------------------------- "what is playing" (plugin -> service)
def set_now(data):
    data = dict(data, t=time.time())
    xbmcgui.Window(10000).setProperty(_NOW, json.dumps(data))


def claim_now(max_age=120):
    """Service side: take the record once; a stale one (failed playback) is ignored."""
    w = xbmcgui.Window(10000)
    raw = w.getProperty(_NOW)
    if not raw:
        return None
    w.clearProperty(_NOW)
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if time.time() - data.get('t', 0) > max_age:
        utils.debug('nowplaying record is stale, ignored')
        return None
    return data


# --------------------------------------------------------------- UI refresh
def refresh_listing():
    """Refresh our own episode / continue list after marks changed. Does nothing for other windows."""
    path = xbmc.getInfoLabel('Container.FolderPath')
    if not path.startswith('plugin://%s/' % utils.ID):
        return
    if 'action=anime' not in path and 'action=continue' not in path:
        return
    q = parse_qs(path.split('?', 1)[1]) if '?' in path else {}
    if 'pick' in q and q.get('id'):
        # this URL would open the voice-over dialog again; reload the clean one instead
        cmd = 'Container.Update(%s,replace)' % utils.url(action='anime', id=q['id'][0])
    else:
        cmd = 'Container.Refresh'
    utils.debug('refresh_listing: %s (path=%s)' % (cmd, path))
    xbmc.executebuiltin(cmd)
