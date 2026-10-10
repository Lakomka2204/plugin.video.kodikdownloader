"""Shikimori anime data -> Kodi video info (plot, genres, studios, cast, ...)."""
import html
import re

import xbmc

from . import utils
from . import shikimori

_MPAA = {'g': 'G', 'pg': 'PG', 'pg_13': 'PG-13', 'r': 'R-17', 'r_plus': 'R+', 'rx': 'Rx'}
_BB_REF = re.compile(r'\[(character|person|anime|manga|ranobe|user)=\d+(?:\s+([^\]]*))?\](?:(.*?)\[/\1\])?', re.S)
_BB_IMG = re.compile(r'\[(img|poster|image)[^\]]*\].*?\[/\1\]', re.S | re.I)
_BB_BR = re.compile(r'\[(?:br|hr)\]', re.I)
_BB_TAG = re.compile(r'\[/?[a-z_]+(?:=[^\]]*)?\]', re.I)
_WRITER_ROLES = ('Script', 'Original Creator', 'Series Composition')
MAX_CAST = 15


def clean_bbcode(text):
    """Shikimori descriptions are BBCode: keep the text, drop tags, links and images."""
    if not text:
        return ''
    t = html.unescape(text)
    t = _BB_IMG.sub('', t)
    t = _BB_REF.sub(lambda m: m.group(3) or m.group(2) or '', t)
    t = _BB_BR.sub('\n', t)
    t = _BB_TAG.sub('', t)
    t = re.sub(r'[ \t]+\n', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def _name(x, ru):
    return ((x.get('russian') if ru else None) or x.get('name') or '').strip()


def _abs(path):
    return shikimori.base() + path if path and path.startswith('/') else (path or '')


def _people(roles, ru):
    cast, directors, writers = [], [], []
    for r in roles if isinstance(roles, list) else []:
        if not isinstance(r, dict):
            continue
        en = r.get('roles') or []
        ch, pe = r.get('character'), r.get('person')
        if ch and len(cast) < MAX_CAST:
            img = ch.get('image') or {}
            role = ', '.join((r.get('roles_russian') if ru else en) or en)
            cast.append((_name(ch, ru), role, _abs(img.get('original') or img.get('preview'))))
        elif pe:
            n = _name(pe, ru)
            if n and 'Director' in en and n not in directors:
                directors.append(n)
            if n and any(x in en for x in _WRITER_ROLES) and n not in writers:
                writers.append(n)
    return cast, directors, writers


def build(a, roles, ru):
    """Plain dict with everything we can show; {} if there is no anime data."""
    if not a:
        return {}
    foot = []
    lic = [x for x in (a.get('licensors') or []) if x]
    if lic:
        foot.append(utils.tr(30229).format(', '.join(lic)))
    aired, rel = a.get('aired_on'), a.get('released_on')
    if aired:
        foot.append(utils.tr(30230).format('%s – %s' % (aired, rel) if rel and rel != aired else aired))
    plot = '\n\n'.join(x for x in (clean_bbcode(a.get('description')), '\n'.join(foot)) if x)
    try:
        duration = int(a.get('duration') or 0) * 60
    except (TypeError, ValueError):
        duration = 0
    try:
        rating = float(a.get('score') or 0)
    except (TypeError, ValueError):
        rating = 0.0
    year = (aired or '')[:4]
    cast, directors, writers = _people(roles, ru)
    return {
        'plot': plot,
        'genres': [n for n in (_name(g, ru) for g in a.get('genres') or []) if n],
        'studios': [s['name'] for s in a.get('studios') or [] if s.get('name')],
        'mpaa': _MPAA.get(a.get('rating') or '', ''),
        'duration': duration,
        'premiered': aired or '',
        'year': int(year) if year.isdigit() else 0,
        'rating': rating,
        'cast': cast, 'directors': directors, 'writers': writers,
    }


def make_actors(m):
    return [xbmc.Actor(n, r, i, t) for i, (n, r, t) in enumerate(m.get('cast') or [])]


def apply(tag, m, actors):
    """Put the info on a video info tag (Kodi 20+ API)."""
    if not m:
        return
    if m.get('plot'):
        tag.setPlot(m['plot'])
    if m.get('genres'):
        tag.setGenres(m['genres'])
    if m.get('studios'):
        tag.setStudios(m['studios'])
    if m.get('mpaa'):
        tag.setMpaa(m['mpaa'])
    if m.get('duration'):
        tag.setDuration(m['duration'])
    if m.get('premiered'):
        tag.setPremiered(m['premiered'])
    if m.get('year'):
        tag.setYear(m['year'])
    if m.get('rating') > 0:
        tag.setRating(m['rating'])
    if m.get('directors'):
        tag.setDirectors(m['directors'])
    if m.get('writers'):
        tag.setWriters(m['writers'])
    if actors:
        tag.setCast(actors)
