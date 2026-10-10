import datetime

import requests
import xbmc
import xbmcgui
import xbmcplugin

from . import utils, cache, kodik, watched
from . import shikimori as shiki
from .utils import tr, url, HANDLE, debug

FILTERS = {
    'ongoing': {'status': 'ongoing', 'order': 'popularity'},
    'top': {'order': 'ranked'},
}
SEASON_NAMES = [('winter', 30011), ('spring', 30012), ('summer', 30013), ('fall', 30014)]
DIR_ACTIONS = (None, 'list', 'seasons', 'search', 'anime', 'continue')
_ended = False


# ---------------------------------------------------------------- helpers
def _title(a):
    if utils.get_int('title_lang') == 1:
        return a.get('name') or a.get('russian') or '?'
    return a.get('russian') or a.get('name') or '?'


def _end(content=None, ok=True):
    global _ended
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
        (30005, {'action': 'search'}, 'DefaultAddonsSearch.png'),
    ]
    for sid, params, icon in items:
        li = xbmcgui.ListItem(tr(sid))
        li.setArt({'icon': icon, 'thumb': icon})
        xbmcplugin.addDirectoryItem(HANDLE, url(**params), li, True)
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


def _anime_item(a):
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
    return li


def anime_list(p):
    kind, page = p.get('kind'), int(p.get('page', 1))
    if kind == 'season':
        filters = {'season': p['season'], 'order': 'popularity'}
    elif kind == 'search':
        filters = {'search': p['q'], 'order': 'popularity'}
    else:
        filters = dict(FILTERS[kind])
    items = shiki.animes(page, **filters)
    debug('anime_list kind=%s page=%s -> %d items' % (kind, page, len(items)))
    if not items and page == 1:
        utils.notify(tr(30214))
    for a in items:
        xbmcplugin.addDirectoryItem(HANDLE, url(action='anime', id=a['id']), _anime_item(a), True)
    if items and len(items) >= (utils.get_int('page_size', 20) or 20):
        nxt = dict(p)
        nxt['page'] = page + 1
        xbmcplugin.addDirectoryItem(HANDLE, url(**nxt), xbmcgui.ListItem(tr(30007)), True)
    _end('tvshows')


def search(p):
    q = p.get('q')
    if not q:
        q = xbmcgui.Dialog().input(tr(30213))
        debug('search input -> %r' % q)
        if not q:
            _end(ok=False)
            return
    anime_list({'action': 'list', 'kind': 'search', 'q': q, 'page': p.get('page', 1)})


# ------------------------------------------------------- anime -> episodes
def _anime_info(sid):
    try:
        return shiki.get_anime(sid)
    except (requests.RequestException, ValueError):
        utils.log_exc('shikimori get_anime(%s)' % sid)
        return {}


def anime_open(p):
    """Pick a voice-over (dialog or remembered choice) and list episodes in THIS directory call.
    No redirects: Container.Update after endOfDirectory(False) is dropped by Kodi."""
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

    pref_key = 'tid:%s' % sid
    remembered = None if p.get('pick') else cache.pref_get(pref_key)
    debug('anime_open sid=%s pick=%s remembered=%s entries=%d' % (sid, p.get('pick'), remembered, len(entries)))
    entry = next((e for e in entries if e['id'] == remembered), None)
    if entry is None:
        if len(entries) == 1:
            entry = entries[0]
            debug('single entry, auto-selected %s' % entry['title'])
        else:
            choice = xbmcgui.Dialog().select(tr(30200), [_entry_label(e) for e in entries])
            debug('translation dialog -> %s' % choice)
            if choice < 0:
                _end(ok=False)
                return
            entry = entries[choice]
        cache.pref_set(pref_key, entry['id'])
    debug('selected entry id=%s title=%r' % (entry['id'], entry['title']))
    _show_entry(sid, entry)


def _ep_label(e, multi):
    return 'S%dE%d' % (e['season'], e['num']) if multi else tr(30208).format(e['num'])


def _play_url(sid, tid, e, title, fresh=None):
    return url(action='play', link=e['link'], sid=sid, tid=tid, season=e['season'], num=e['num'],
               title=title, fresh=fresh)


def _apply_marks(tag, rec):
    if rec.get('w'):
        tag.setPlaycount(1)
    elif rec.get('pos', 0) > 0 and rec.get('total', 0) > 0:
        tag.setResumePoint(float(rec['pos']), float(rec['total']))


def _context(sid, num, fresh_url):
    def act(mode):
        return 'RunPlugin(%s)' % url(action='mark', sid=sid, num=num, mode=mode)
    return [(tr(30222), act('watched')), (tr(30223), act('unwatched')), (tr(30224), act('until')),
            (tr(30225), 'PlayMedia(%s)' % fresh_url)]


def _add_playable(sid, tid, e, name, poster, label, title, rec, movie=False):
    li = xbmcgui.ListItem(label)
    li.setArt({'thumb': poster, 'icon': poster, 'poster': poster, 'fanart': poster})
    tag = li.getVideoInfoTag()
    tag.setTitle(label)
    if movie:
        tag.setMediaType('movie')
    else:
        tag.setMediaType('episode')
        tag.setTvShowTitle(name)
        tag.setEpisode(e['num'])
        tag.setSeason(e['season'])
    _apply_marks(tag, rec)
    li.setProperty('IsPlayable', 'true')
    li.addContextMenuItems(_context(sid, e['num'], _play_url(sid, tid, e, title, fresh=1)))
    xbmcplugin.addDirectoryItem(HANDLE, _play_url(sid, tid, e, title), li, False)


def _show_entry(sid, entry):
    a = _anime_info(sid)
    name = _title(a) if a else ''
    poster = shiki.image_url(a)
    is_movie = bool(entry['movie_link'] and not entry['episodes'])
    episodes = [{'season': 1, 'num': 1, 'link': entry['movie_link']}] if is_movie else entry['episodes']
    all_nums = [e['num'] for e in episodes]
    watched.set_meta(sid, name, poster, len(episodes), min(all_nums), max(all_nums))
    rec_anime = watched.get_anime(sid)
    marks = rec_anime.get('eps', {})

    head = xbmcgui.ListItem('[B]%s:[/B] %s' % (tr(30210), _entry_label(entry)))
    head.setArt({'icon': 'DefaultAddonSubtitles.png', 'thumb': 'DefaultAddonSubtitles.png'})
    xbmcplugin.addDirectoryItem(HANDLE, url(action='anime', id=sid, pick=1), head, True)

    if is_movie:
        label = name or tr(30209)
        _add_playable(sid, entry['id'], episodes[0], name, poster, label, label, marks.get('1', {}), movie=True)
        debug('movie item added')
        _end('movies')
        return

    multi = len({e['season'] for e in episodes}) > 1
    cont = watched.continue_target(marks, all_nums, rec_anime.get('last'))
    debug('continue target=%s marks=%d' % (cont, len(marks)))
    if cont is not None:
        e = next(x for x in episodes if x['num'] == cont)
        lbl = _ep_label(e, multi)
        _add_playable(sid, entry['id'], e, name, poster, '[B]%s[/B]' % tr(30221).format(cont),
                      '%s - %s' % (name, lbl) if name else lbl, marks.get(str(cont), {}))
    for e in episodes:
        lbl = _ep_label(e, multi)
        _add_playable(sid, entry['id'], e, name, poster, lbl, '%s - %s' % (name, lbl) if name else lbl,
                      marks.get(str(e['num']), {}))
    debug('episodes listed: %d (multi_season=%s)' % (len(episodes), multi))
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_UNSORTED)
    _end('episodes')


def continue_list():
    rows = watched.recent(30)
    debug('continue_list: %d anime' % len(rows))
    if not rows:
        utils.notify(tr(30214))
        _end(ok=False)
        return
    for sid, a in rows:
        name = a.get('name') or str(sid)
        nxt = watched.next_episode(a)
        li = xbmcgui.ListItem('%s  ·  %s' % (name, tr(30208).format(nxt)) if nxt else name)
        poster = a.get('poster') or ''
        li.setArt({'poster': poster, 'thumb': poster, 'icon': poster, 'fanart': poster})
        tag = li.getVideoInfoTag()
        tag.setMediaType('tvshow')
        tag.setTitle(name)
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
def _closest(available, pref):
    return min(available, key=lambda q: (abs(q - pref), q))  # ties -> lower


def _pick_quality(links, ask=True):
    available = sorted(links)
    pref = utils.get_int('default_quality', 0)
    debug('pick quality: available=%s pref=%s ask=%s' % (available, pref, ask))
    if len(available) == 1:
        return available[0]
    if pref == 0:
        if not ask:  # autoplay: no dialogs, reuse the last quality the user picked
            last = cache.pref_get('last_quality')
            return _closest(available, last) if last else available[-1]
        idx = xbmcgui.Dialog().select(tr(30203), ['%dp' % q for q in available])
        debug('quality dialog -> %s' % idx)
        return available[idx] if idx >= 0 else None
    if pref >= 9999:
        return available[-1]
    return _closest(available, pref)


def _stream_item(stream, title):
    li = xbmcgui.ListItem(label=title, path=stream)
    if title:
        li.getVideoInfoTag().setTitle(title)
    headers = utils.get_str('stream_headers').strip()
    isa_installed = xbmc.getCondVisibility('System.AddonIsEnabled(inputstream.adaptive)')
    use_isa = utils.get_bool('use_isa', True) and isa_installed
    debug('use_isa setting=%s installed=%s -> %s; headers=%r' % (utils.get_bool('use_isa', True), isa_installed,
                                                                  use_isa, headers))
    if use_isa:
        li.setProperty('inputstream', 'inputstream.adaptive')
        li.setProperty('inputstream.adaptive.manifest_type', 'hls')
        li.setMimeType('application/x-mpegURL')
        li.setContentLookup(False)
        if headers:
            li.setProperty('inputstream.adaptive.manifest_headers', headers)
            li.setProperty('inputstream.adaptive.stream_headers', headers)
    elif headers:
        li.setPath('%s|%s' % (stream, headers))
    return li


def _track(p):
    """Tell the service what is about to play: it keeps the marks and the resume point."""
    if not (p.get('sid') and p.get('num')):
        debug('track: no sid/num in params, marks skipped')
        return
    rec = watched.get_ep(p['sid'], p['num'])
    resume = 0 if (p.get('fresh') or rec.get('w')) else int(rec.get('pos', 0))
    now = {'sid': str(p['sid']), 'tid': str(p.get('tid', '')), 'season': int(p.get('season') or 1),
           'num': int(p['num']), 'resume': resume}
    watched.set_now(now)
    watched.touch(p['sid'], p['num'])
    debug('track: %s' % now)


def play(p):
    debug('play: title=%r link=%s' % (p.get('title'), p.get('link')))
    try:
        links = kodik.resolve(p['link'])
    except (kodik.KodikError, requests.RequestException, ValueError):
        utils.log_exc('kodik.resolve')
        xbmcgui.Dialog().ok(utils.NAME, tr(30211))
        _fail_play()
        return
    q = _pick_quality(links)
    if q is None:
        _fail_play()
        return
    cache.pref_set('last_quality', q)
    stream = links[q]
    debug('chosen %dp: %s' % (q, stream))
    li = _stream_item(stream, p.get('title', ''))
    _track(p)
    debug('setResolvedUrl(True) path=%s' % li.getPath())
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def autoplay_next(cur):
    """Called by the service after an episode ended. Returns a ready ListItem or None."""
    entry = next((e for e in kodik.get_entries(cur['sid']) if str(e['id']) == str(cur['tid'])), None)
    if not entry:
        return None
    eps = entry['episodes']
    i = next((k for k, e in enumerate(eps) if e['num'] == cur['num'] and e['season'] == cur['season']), None)
    if i is None or i + 1 >= len(eps):
        return None
    nxt = eps[i + 1]
    links = kodik.resolve(nxt['link'])
    q = _pick_quality(links, ask=False)
    multi = len({e['season'] for e in eps}) > 1
    lbl = _ep_label(nxt, multi)
    name = watched.get_anime(cur['sid']).get('name', '')
    title = '%s - %s' % (name, lbl) if name else lbl
    li = _stream_item(links[q], title)
    _track({'sid': cur['sid'], 'tid': cur['tid'], 'season': nxt['season'], 'num': nxt['num']})
    debug('autoplay_next: %s at %dp' % (title, q))
    return li


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
        elif action == 'search':
            search(p)
        elif action == 'anime':
            anime_open(p)
        elif action == 'play':
            play(p)
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
