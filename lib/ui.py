import datetime
import re

import requests
import xbmc
import xbmcgui
import xbmcplugin

from . import utils, cache, kodik, watched, info, master, keymap
from . import shikimori as shiki
from .utils import tr, url, HANDLE, debug

FILTERS = {
    'ongoing': {'status': 'ongoing', 'order': 'popularity'},
    'top': {'order': 'ranked'},
}
SEASON_NAMES = [('winter', 30011), ('spring', 30012), ('summer', 30013), ('fall', 30014)]
DIR_ACTIONS = (None, 'list', 'seasons', 'search', 'anime', 'continue', 'genres')
_ended = False
# Kodik screenshot URLs: .../screenshots/seria/<seria id>/<n>.jpg. Build the same kind of URL for every
# episode (default on, switchable in settings in case Kodik has none for some episodes).
_SHOT_RE = re.compile(r'^(.*/screenshots/seria/)(\d+)/(\d+)(\.[a-z]+)$', re.I)
_SERIA_RE = re.compile(r'/seria/(\d+)/')


# ---------------------------------------------------------------- helpers
def _title(a):
    if utils.get_int('title_lang') == 1:
        return a.get('name') or a.get('russian') or '?'
    return a.get('russian') or a.get('name') or '?'


def _end(content=None, ok=True):
    global _ended
    if HANDLE < 0:  # run as a script (RunPlugin / non-folder item): nothing to end
        return
    if content:
        xbmcplugin.setContent(HANDLE, content)
    debug('endOfDirectory handle=%s succeeded=%s content=%s' % (HANDLE, ok, content))
    xbmcplugin.endOfDirectory(HANDLE, succeeded=ok, cacheToDisc=False)
    _ended = True


def _fail_play():
    debug('setResolvedUrl(False)')
    xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())


def _entry_label(e):
    count = tr(30209) if (e['movie_link'] and not e['episodes']) else '%d %s' % (len(e['episodes']), tr(30217))
    prefix = '[%s] ' % tr(30201) if e['sub'] else ''
    return '%s%s  ·  %s' % (prefix, e['title'], count)


def _kodik_error(e):
    debug('kodik error shown to user: %r' % e)
    if isinstance(e, kodik.KodikError) and e.code == 'no_token':
        if xbmcgui.Dialog().yesno(utils.NAME, tr(30204)):
            utils.ADDON.openSettings()
    elif isinstance(e, kodik.KodikError) and e.code == 'http':
        xbmcgui.Dialog().ok(utils.NAME, tr(30216).format(e.detail))
    else:
        utils.log_exc('kodik')
        utils.notify(tr(30205), xbmcgui.NOTIFICATION_ERROR)


# ------------------------------------------------------------------ menus
def root():
    items = [
        (30008, {'action': 'continue'}, 'DefaultRecentlyAddedEpisodes.png'),
        (30001, {'action': 'list', 'kind': 'ongoing'}, 'DefaultInProgressShows.png'),
        (30003, {'action': 'list', 'kind': 'top'}, 'DefaultMusicTop100.png'),
        (30004, {'action': 'seasons'}, 'DefaultYear.png'),
        (30015, {'action': 'genres'}, 'DefaultGenre.png'),
        (30005, {'action': 'search'}, 'DefaultAddonsSearch.png'),
    ]
    for sid, params, icon in items:
        li = xbmcgui.ListItem(tr(sid))
        li.setArt({'icon': icon, 'thumb': icon})
        xbmcplugin.addDirectoryItem(HANDLE, url(**params), li, params['action'] != 'search')
    li = xbmcgui.ListItem(tr(30006))
    li.setArt({'icon': 'DefaultAddonProgram.png', 'thumb': 'DefaultAddonProgram.png'})
    xbmcplugin.addDirectoryItem(HANDLE, url(action='settings'), li, False)
    _end()


def seasons():
    today = datetime.date.today()
    idx, year = (today.month - 1) // 3, today.year
    for _ in range(12):
        name, sid = SEASON_NAMES[idx]
        li = xbmcgui.ListItem('%s %d' % (tr(sid), year))
        li.setArt({'icon': 'DefaultYear.png', 'thumb': 'DefaultYear.png'})
        xbmcplugin.addDirectoryItem(
            HANDLE, url(action='list', kind='season', season='%s_%d' % (name, year)), li, True)
        idx -= 1
        if idx < 0:
            idx, year = 3, year - 1
    _end()


def genres():
    ru = utils.get_int('title_lang') != 1
    gl = shiki.genres()
    gl.sort(key=lambda g: ((g['russian'] if ru else g['name']) or g['name'] or '').lower())
    for g in gl:
        li = xbmcgui.ListItem((g['russian'] if ru else g['name']) or g['name'] or '?')
        li.setArt({'icon': 'DefaultGenre.png', 'thumb': 'DefaultGenre.png'})
        xbmcplugin.addDirectoryItem(HANDLE, url(action='list', kind='genre', genre=g['id']), li, True)
    _end()


def _info_for(det):
    full, roles = det
    inf = info.build(full, roles, utils.get_int('title_lang') != 1)
    return inf, info.make_actors(inf)


def _anime_item(a, det=None):
    li = xbmcgui.ListItem(label=_title(a))
    poster = shiki.image_url(a)
    li.setArt({'poster': poster, 'thumb': poster, 'icon': poster, 'fanart': poster})
    tag = li.getVideoInfoTag()
    tag.setMediaType('tvshow')
    tag.setTitle(_title(a))
    if a.get('name'):
        tag.setOriginalTitle(a['name'])
    year = (a.get('aired_on') or '')[:4]
    if year.isdigit():
        tag.setYear(int(year))
    try:
        if a.get('score') and float(a['score']) > 0:
            tag.setRating(float(a['score']))
    except ValueError:
        pass
    eps = ''
    if a.get('episodes') or a.get('episodes_aired'):
        eps = '%s/%s' % (a.get('episodes_aired') or 0, a.get('episodes') or '?')
    tag.setPlot(' · '.join(x for x in (a.get('kind'), a.get('status'), eps) if x))
    if det:  # full description, genres, studios, cast, episode duration ... on the anime itself
        info.apply(tag, *_info_for(det))
    return li


def anime_list(p):
    kind, page = p.get('kind'), int(p.get('page', 1))
    if kind == 'season':
        filters = {'season': p['season'], 'order': 'popularity'}
    elif kind == 'genre':
        filters = {'genre': p['genre'], 'order': 'popularity'}
    elif kind == 'search':
        filters = {'search': p['q'], 'order': 'popularity'}
    else:
        filters = dict(FILTERS[kind])
    items = shiki.animes(page, **filters)
    details = shiki.details([a['id'] for a in items]) if items else {}
    debug('anime_list kind=%s page=%s -> %d items' % (kind, page, len(items)))
    if not items and page == 1:
        utils.notify(tr(30214))
    # page links go on top, so they are reachable without scrolling and stay together
    if page > 1:
        prv = dict(p)
        prv['page'] = page - 1
        li = xbmcgui.ListItem(tr(30009))
        li.setArt({'icon': 'DefaultFolderBack.png', 'thumb': 'DefaultFolderBack.png'})
        xbmcplugin.addDirectoryItem(HANDLE, url(**prv), li, True)
    if items and len(items) >= (utils.get_int('page_size', 20) or 20):
        nxt = dict(p)
        nxt['page'] = page + 1
        li = xbmcgui.ListItem(tr(30007))
        li.setArt({'icon': 'DefaultFolder.png', 'thumb': 'DefaultFolder.png'})
        xbmcplugin.addDirectoryItem(HANDLE, url(**nxt), li, True)
    for a in items:
        xbmcplugin.addDirectoryItem(HANDLE, url(action='anime', id=a['id']), _anime_item(a, details.get(a['id'])), True)
    _end('tvshows')


def search(p):
    """Run as a script from a non-folder menu item. The results are opened as a separate directory whose
    URL carries the query, so 'back' from an anime returns to the results, not to the input box."""
    q = p.get('q')
    if q:  # old URLs
        anime_list({'action': 'list', 'kind': 'search', 'q': q, 'page': p.get('page', 1)})
        return
    q = xbmcgui.Dialog().input(tr(30213))
    debug('search input -> %r' % q)
    if q:
        xbmc.executebuiltin('Container.Update(%s)' % url(action='list', kind='search', q=q))


# ------------------------------------------------------- anime -> episodes
def _anime_info(sid):
    try:
        return shiki.get_anime(sid)
    except (requests.RequestException, ValueError):
        utils.log_exc('shikimori get_anime(%s)' % sid)
        return {}


class _NoVoice(Exception):
    pass


def _screenshots(entries):
    """Kodik screenshots are per search result (a title), not per episode: pool them, dedupe, keep order."""
    out = []
    for en in entries:
        for s in en.get('screenshots') or []:
            s = 'https:' + s if s.startswith('//') else s
            if s not in out:
                out.append(s)
    return out


def _episode_index(entries):
    """{(season, num): [(entry, link), ...]} built from the already fetched search data (no extra requests)."""
    idx = {}
    for en in entries:
        for e in en['episodes']:
            idx.setdefault((e['season'], e['num']), []).append((en, e['link']))
    return idx


def _shot_template(pool):
    """(prefix, ext, count) from a known screenshot URL, to build the same kind of URL for other episodes."""
    for u in pool:
        m = _SHOT_RE.match(u)
        if m:
            return m.group(1), m.group(4), max(sum(1 for x in pool if '/seria/%s/' % m.group(2) in x), 1)
    return None


def _episode_shot(tpl, link, num):
    m = _SERIA_RE.search(link or '')
    if not tpl or not m:
        return None
    prefix, ext, count = tpl
    return '%s%s/%d%s' % (prefix, m.group(1), num % count + 1, ext)


def anime_open(p):
    """Episodes first, voice-over second. The list is the union of episode numbers over all voice-overs,
    built from one (cached) Kodik search. The voice-over is chosen when an episode is played."""
    sid = p['id']
    try:
        entries = kodik.get_entries(sid)
    except (kodik.KodikError, requests.RequestException) as e:
        _kodik_error(e)
        _end(ok=False)
        return
    if not entries:
        debug('no kodik entries for shikimori_id=%s' % sid)
        xbmcgui.Dialog().ok(utils.NAME, tr(30202))
        _end(ok=False)
        return
    debug('anime_open sid=%s entries=%d preferred=%s' % (sid, len(entries), cache.pref_get('tid:%s' % sid)))
    _show_anime(sid, entries)


def pick_voice(p):
    """Head item of the list: choose the default voice-over, then reload the list."""
    sid = p['id']
    try:
        entries = kodik.get_entries(sid)
    except (kodik.KodikError, requests.RequestException) as e:
        _kodik_error(e)
        return
    choice = xbmcgui.Dialog().select(tr(30200), [_entry_label(e) for e in entries])
    debug('pick_voice dialog -> %s' % choice)
    if choice < 0:
        return
    cache.pref_set('tid:%s' % sid, entries[choice]['id'])
    watched.refresh_listing()


def _ep_label(season, num, multi):
    return 'S%dE%d' % (season, num) if multi else tr(30208).format(num)


def _play_url(sid, season, num, title, fresh=None, movie=None):
    return url(action='play', sid=sid, season=season, num=num, title=title, fresh=fresh, movie=movie)


def _apply_marks(tag, rec):
    if rec.get('w'):
        tag.setPlaycount(1)
    elif rec.get('pos', 0) > 0 and rec.get('total', 0) > 0:
        tag.setResumePoint(float(rec['pos']), float(rec['total']))


def _context(sid, num, fresh_url):
    def act(mode):
        return 'RunPlugin(%s)' % url(action='mark', sid=sid, num=num, mode=mode)
    return [(tr(30222), act('watched')), (tr(30223), act('unwatched')), (tr(30224), act('until')),
            (tr(30225), 'PlayMedia(%s)' % fresh_url),
            (tr(30231), 'RunPlugin(%s)' % url(action='voice', id=sid))]


def _add_playable(sid, season, num, name, art, label, title, rec, nvoices=0, movie=False, cont=False):
    li = xbmcgui.ListItem(label)
    li.setArt(art)
    if nvoices:
        li.setLabel2(tr(30227).format(nvoices))
    tag = li.getVideoInfoTag()
    tag.setTitle(label)
    if movie:
        tag.setMediaType('movie')
    else:
        tag.setMediaType('episode')
        tag.setTvShowTitle(name)
        tag.setEpisode(num)
        tag.setSeason(season)
    _apply_marks(tag, rec)
    li.setProperty('IsPlayable', 'true')
    if cont:
        li.setProperty('kk_continue', '1')  # the service looks for it to pick the right item to focus
    mv = 1 if movie else None
    li.addContextMenuItems(_context(sid, num, _play_url(sid, season, num, title, fresh=1, movie=mv)))
    xbmcplugin.addDirectoryItem(HANDLE, _play_url(sid, season, num, title, movie=mv), li, False)


def _show_anime(sid, entries):
    a = _anime_info(sid)
    name = _title(a) if a else ''
    poster = shiki.image_url(a)
    shots = _screenshots(entries)
    idx = _episode_index(entries)
    keys = sorted(idx)
    is_movie = not keys
    nums = [n for _, n in keys] or [1]
    watched.set_meta(sid, name, poster, len(nums), min(nums), max(nums))
    rec_anime = watched.get_anime(sid)
    marks = rec_anime.get('eps', {})
    tpl = _shot_template(shots) if utils.get_bool('episode_shots', True) else None
    debug('show_anime: episodes=%d movie=%s screenshots=%d per_episode=%s' % (len(keys), is_movie, len(shots), bool(tpl)))

    if is_movie:
        label = name or tr(30209)
        art = {'thumb': poster, 'icon': poster, 'poster': poster, 'fanart': shots[0] if shots else poster}
        _add_playable(sid, 1, 1, name, art, label, label, marks.get('1', {}), movie=True)
        _end('movies')
        return

    multi = len({s for s, _ in keys}) > 1

    def art_for(i):  # a different still per episode; Kodi loads images of visible items only
        s, n = keys[i]
        shot = _episode_shot(tpl, idx[(s, n)][0][1], n) or (shots[i % len(shots)] if shots else poster)
        return {'thumb': shot, 'icon': shot, 'landscape': shot, 'fanart': shot, 'poster': poster}

    def title_for(s, n):
        lbl = _ep_label(s, n, multi)
        return '%s - %s' % (name, lbl) if name else lbl

    cont = watched.continue_target(marks, nums, rec_anime.get('last'))
    debug('continue target=%s marks=%d' % (cont, len(marks)))
    if cont is not None:
        i = next(k for k, (_, n) in enumerate(keys) if n == cont)
        s, n = keys[i]
        art = art_for(i)
        art.update({'thumb': 'DefaultRecentlyAddedEpisodes.png', 'icon': 'DefaultRecentlyAddedEpisodes.png'})
        _add_playable(sid, s, n, name, art, '[B]%s[/B]' % tr(30221).format(n), title_for(s, n),
                      marks.get(str(n), {}), nvoices=len(idx[(s, n)]), cont=True)
    for i, (s, n) in enumerate(keys):
        _add_playable(sid, s, n, name, art_for(i), _ep_label(s, n, multi), title_for(s, n),
                      marks.get(str(n), {}), nvoices=len(idx[(s, n)]))
    debug('episodes listed: %d (multi_season=%s)' % (len(keys), multi))
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_UNSORTED)
    _end('episodes')


def continue_list():
    rows = watched.recent(30)
    debug('continue_list: %d anime' % len(rows))
    if not rows:
        utils.notify(tr(30214))
        _end(ok=False)
        return
    details = shiki.details([int(s) for s, _ in rows])
    for sid, a in rows:
        name = a.get('name') or str(sid)
        nxt = watched.next_episode(a)
        li = xbmcgui.ListItem('%s  ·  %s' % (name, tr(30208).format(nxt)) if nxt else name)
        poster = a.get('poster') or ''
        li.setArt({'poster': poster, 'thumb': poster, 'icon': poster, 'fanart': poster})
        tag = li.getVideoInfoTag()
        tag.setMediaType('tvshow')
        tag.setTitle(name)
        if int(sid) in details:
            info.apply(tag, *_info_for(details[int(sid)]))
        xbmcplugin.addDirectoryItem(HANDLE, url(action='anime', id=sid), li, True)
    _end('tvshows')


def mark(p):
    sid, num, mode = p['sid'], int(p['num']), p.get('mode')
    if mode == 'until':
        watched.mark(sid, range(1, num + 1), True)
    else:
        watched.mark(sid, [num], mode == 'watched')
    debug('mark sid=%s num=%s mode=%s' % (sid, num, mode))
    watched.refresh_listing()


def clear_watched():
    if xbmcgui.Dialog().yesno(utils.NAME, tr(30219)):
        watched.clear()
        debug('watch history cleared')
        utils.notify(tr(30220))
        watched.refresh_listing()


# --------------------------------------------------------------- playback
def _pick_quality(links):
    available = sorted(links)
    pref = utils.get_int('default_quality', 0)
    debug('pick quality: available=%s pref=%s' % (available, pref))
    if len(available) == 1:
        return available[0]
    if pref == 0:
        idx = xbmcgui.Dialog().select(tr(30203), ['%dp' % q for q in available])
        debug('quality dialog -> %s' % idx)
        return available[idx] if idx >= 0 else None
    if pref >= 9999:
        return available[-1]
    return min(available, key=lambda q: (abs(q - pref), q))  # closest, ties -> lower


def _use_isa():
    isa_installed = xbmc.getCondVisibility('System.AddonIsEnabled(inputstream.adaptive)')
    use_isa = utils.get_bool('use_isa', True) and isa_installed
    debug('use_isa setting=%s installed=%s -> %s' % (utils.get_bool('use_isa', True), isa_installed, use_isa))
    return use_isa


def _stream_item(stream, title, manual=False):
    """manual=True: `stream` is our master playlist and ISA lets the user pick the quality in Video Settings."""
    li = xbmcgui.ListItem(label=title, path=stream)
    if title:
        li.getVideoInfoTag().setTitle(title)
    headers = utils.get_str('stream_headers').strip()
    use_isa = _use_isa()
    debug('stream item: manual=%s headers=%r' % (manual, headers))
    if use_isa:
        li.setProperty('inputstream', 'inputstream.adaptive')
        li.setMimeType('application/vnd.apple.mpegurl')
        li.setContentLookup(False)
        if manual:
            li.setProperty('inputstream.adaptive.stream_selection_type', 'manual-osd')
        if headers:
            li.setProperty('inputstream.adaptive.manifest_headers', headers)
            li.setProperty('inputstream.adaptive.stream_headers', headers)
    elif headers:
        li.setPath('%s|%s' % (stream, headers))
    return li


def _track(p, fallback=None, seek_to=None, q=None):
    """Tell the service what is about to play: it keeps the marks, can seek and can fall back."""
    if not (p.get('sid') and p.get('num')):
        debug('track: no sid/num in params, marks skipped')
        return
    now = {'sid': str(p['sid']), 'tid': str(p.get('tid', '')), 'season': int(p.get('season') or 1),
           'num': int(p['num']), 'title': p.get('title', ''), 'movie': 1 if p.get('movie') else 0}
    if fallback:
        now['fallback'] = fallback  # the service plays this if the master playlist fails to open
    if seek_to:
        now['seek_to'] = int(seek_to)  # after a voice/quality switch: continue from here
    if q:
        now['q'] = int(q)  # single-stream mode: the quality in use
    watched.set_now(now)
    watched.touch(p['sid'], p['num'])
    debug('track: %s' % now)


def _voice_for(p):
    """Choose the voice-over for one episode from the cached search data: remembered one if it has the
    episode, else a dialog with only those that do. Returns (entry, link), None if cancelled."""
    sid = p['sid']
    entries = kodik.get_entries(sid)
    if p.get('movie'):
        cands = [(en, en['movie_link']) for en in entries if en['movie_link'] and not en['episodes']]
    else:
        cands = _episode_index(entries).get((int(p.get('season') or 1), int(p['num'])), [])
    pref = cache.pref_get('tid:%s' % sid)
    debug('voice: %d candidates for season=%s num=%s, preferred=%s' % (len(cands), p.get('season'), p.get('num'), pref))
    if not cands:
        raise _NoVoice()
    ask = utils.get_bool('voice_ask')
    if not ask:
        for en, link in cands:
            if en['id'] == pref:
                return en, link
    if len(cands) == 1:
        return cands[0]
    labels = [('[%s] ' % tr(30201) if en['sub'] else '') + en['title'] for en, _ in cands]
    pre = next((k for k, (en, _) in enumerate(cands) if en['id'] == pref), -1)
    choice = xbmcgui.Dialog().select(tr(30200), labels, preselect=pre)
    debug('voice dialog -> %s' % choice)
    if choice < 0:
        return None
    if pref is None:  # first time for this anime: becomes the default; later one-off picks do not
        cache.pref_set('tid:%s' % sid, cands[choice][0]['id'])
    return cands[choice]


def _fallback_quality(links):
    """Quality for the single-stream retry (no dialogs): the default quality setting, else the best."""
    available = sorted(links)
    pref = utils.get_int('default_quality', 0)
    if pref in (0, 9999):
        return available[-1]
    return min(available, key=lambda q: (abs(q - pref), q))


def fallback_item(rec):
    """Service: the master playlist failed to open, build the plain single-quality item instead."""
    fb = rec['fallback']
    li = _stream_item(fb['url'], fb['title'])
    _track({'sid': rec['sid'], 'tid': rec['tid'], 'season': rec['season'], 'num': rec['num'],
            'title': rec.get('title', ''), 'movie': rec.get('movie')})
    debug('fallback_item: %s' % fb['url'])
    return li


def _make_item(links, title, q=None):
    """Item for resolved links: a master playlist (qualities in Video Settings) or one quality.
    Returns (item, fallback or None, quality or None); item is None if the user cancelled."""
    if q is None and utils.get_int('quality_mode', 0) == 0 and len(links) > 1 and _use_isa():
        url_ = master.publish(links)
        if url_:
            fb = _fallback_quality(links)
            debug('master playlist %s, qualities=%s, fallback %dp' % (url_, sorted(links), fb))
            return _stream_item(url_, title, manual=True), {'url': links[fb], 'title': title}, None
    if q is None:
        q = _pick_quality(links)
        if q is None:
            return None, None, None
    debug('chosen %dp: %s' % (q, links[q]))
    return _stream_item(links[q], title), None, q


def play(p):
    debug('play: title=%r params=%s' % (p.get('title'), {k: v for k, v in p.items() if k != 'title'}))
    link = p.get('link')  # legacy URLs (older bookmarks) carry the link itself
    if not link:
        try:
            picked = _voice_for(p)
        except _NoVoice:
            xbmcgui.Dialog().ok(utils.NAME, tr(30228))
            _fail_play()
            return
        except (kodik.KodikError, requests.RequestException) as e:
            _kodik_error(e)
            _fail_play()
            return
        if picked is None:
            _fail_play()
            return
        entry, link = picked
        p = dict(p, tid=entry['id'])
        debug('voice chosen: id=%s title=%r' % (entry['id'], entry['title']))
    try:
        links = kodik.resolve(link)
    except (kodik.KodikError, requests.RequestException, ValueError):
        utils.log_exc('kodik.resolve')
        xbmcgui.Dialog().ok(utils.NAME, tr(30211))
        _fail_play()
        return
    li, fb, q = _make_item(links, p.get('title', ''))
    if li is None:
        _fail_play()
        return
    _track(p, fallback=fb, q=q)
    debug('setResolvedUrl(True) path=%s' % li.getPath())
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def _closest(available, pref):
    return min(available, key=lambda q: (abs(q - pref), q))  # ties -> lower


def switch(p):
    """Run by the remote-button shortcut during playback: change the voice-over (in 'ask before playing'
    mode also the quality) and continue from the same position. The service does the seek."""
    cur = watched.get_current()
    player = xbmc.Player()
    if not cur or not player.isPlayingVideo():
        utils.notify(tr(30235))
        return
    pos = int(player.getTime())
    mode1 = utils.get_int('quality_mode', 0) == 1
    debug('switch: current=%s pos=%s' % (cur, pos))
    try:
        entries = kodik.get_entries(cur['sid'])
        if cur.get('movie'):
            cands = [(en, en['movie_link']) for en in entries if en['movie_link'] and not en['episodes']]
        else:
            cands = _episode_index(entries).get((cur['season'], cur['num']), [])
    except (kodik.KodikError, requests.RequestException) as e:
        _kodik_error(e)
        return
    what = 0
    if mode1:  # in 'video settings' mode the quality is changed there, not here
        what = xbmcgui.Dialog().select(tr(30232), [tr(30233), tr(30234)])
        if what < 0:
            return
    here = next(((en, ln) for en, ln in cands if str(en['id']) == str(cur['tid'])), None)
    if what == 0:
        if len(cands) < 2:
            utils.notify(tr(30242))
            return
        labels = [('> ' if str(en['id']) == str(cur['tid']) else '') + ('[%s] ' % tr(30201) if en['sub'] else '')
                  + en['title'] for en, _ in cands]
        pre = next((k for k, (en, _) in enumerate(cands) if str(en['id']) == str(cur['tid'])), -1)
        choice = xbmcgui.Dialog().select(tr(30233), labels, preselect=pre)
        debug('switch voice dialog -> %s' % choice)
        if choice < 0 or str(cands[choice][0]['id']) == str(cur['tid']):
            return
        entry, link = cands[choice]
        cache.pref_set('tid:%s' % cur['sid'], entry['id'])  # the new voice-over becomes the default
    else:
        if not here:
            return
        entry, link = here
    try:
        links = kodik.resolve(link)
    except (kodik.KodikError, requests.RequestException, ValueError):
        utils.log_exc('kodik.resolve (switch)')
        xbmcgui.Dialog().ok(utils.NAME, tr(30211))
        return
    q = None
    if mode1:
        available = sorted(links)
        if what == 0 and cur.get('q'):
            q = _closest(available, cur['q'])
        elif what == 1:
            idx = xbmcgui.Dialog().select(tr(30234), ['%dp' % x for x in available],
                                          preselect=available.index(cur['q']) if cur.get('q') in available else -1)
            if idx < 0:
                return
            q = available[idx]
    li, fb, q2 = _make_item(links, cur.get('title', ''), q)
    if li is None:
        return
    _track({'sid': cur['sid'], 'tid': entry['id'], 'season': cur['season'], 'num': cur['num'],
            'title': cur.get('title', ''), 'movie': cur.get('movie')}, fallback=fb, seek_to=pos, q=q2)
    debug('switch: playing %s from %ss' % (li.getPath(), pos))
    player.play(li.getPath(), li)


def clear_cache():
    if xbmcgui.Dialog().yesno(utils.NAME, tr(30218)):
        cache.clear()
        debug('cache cleared')
        utils.notify(tr(30212))
    else:
        debug('clear cache cancelled')


# ----------------------------------------------------------------- router
def route(p):
    action = p.get('action')
    utils.log_environment(p)
    try:
        if action is None:
            root()
        elif action == 'list':
            anime_list(p)
        elif action == 'seasons':
            seasons()
        elif action == 'genres':
            genres()
        elif action == 'search':
            search(p)
        elif action == 'anime':
            anime_open(p)
        elif action == 'play':
            play(p)
        elif action == 'voice':
            pick_voice(p)
        elif action == 'switch':
            switch(p)
        elif action == 'keymap_setup':
            keymap.setup()
        elif action == 'keymap_remove':
            keymap.remove()
        elif action == 'continue':
            continue_list()
        elif action == 'mark':
            mark(p)
        elif action == 'clear_watched':
            clear_watched()
        elif action == 'settings':
            utils.ADDON.openSettings()
        elif action == 'clear_cache':
            clear_cache()
        else:
            debug('unknown action %r' % action)
            if action in DIR_ACTIONS:
                _end(ok=False)
    except Exception as e:
        utils.log_exc('route(%s)' % action)
        utils.notify('%s: %s' % (tr(30205) if isinstance(e, requests.RequestException) else 'Error', e),
                     xbmcgui.NOTIFICATION_ERROR)
        if action == 'play':
            _fail_play()
        elif action in DIR_ACTIONS and not _ended:
            _end(ok=False)
    debug('=== run finished: action=%s' % action)
