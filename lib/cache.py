import hashlib
import json
import os
import time

from . import utils


def _dir():
    d = os.path.join(utils.PROFILE, 'cache')
    os.makedirs(d, exist_ok=True)
    return d


def _path(key):
    return os.path.join(_dir(), hashlib.md5(key.encode('utf-8')).hexdigest() + '.json')


def get(key, ttl):
    if ttl <= 0:
        return None
    p = _path(key)
    try:
        if time.time() - os.path.getmtime(p) > ttl:
            return None
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def set(key, value):
    try:
        with open(_path(key), 'w', encoding='utf-8') as f:
            json.dump(value, f)
    except Exception as e:
        utils.log('cache write failed: %s' % e)


def clear():
    d = _dir()
    for name in os.listdir(d):
        try:
            os.remove(os.path.join(d, name))
        except OSError:
            pass


# --- small persistent preferences (e.g. last chosen voice-over per anime) ---
def _prefs_path():
    os.makedirs(utils.PROFILE, exist_ok=True)
    return os.path.join(utils.PROFILE, 'prefs.json')


def pref_get(key, default=None):
    try:
        with open(_prefs_path(), 'r', encoding='utf-8') as f:
            return json.load(f).get(key, default)
    except Exception:
        return default


def pref_set(key, value):
    try:
        try:
            with open(_prefs_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[key] = value
        with open(_prefs_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as e:
        utils.log('prefs write failed: %s' % e)
