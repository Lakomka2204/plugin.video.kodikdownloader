import datetime

import requests
import xbmc
import xbmcgui
import xbmcplugin

from . import utils, cache, kodik
from . import shikimori as shiki
from .utils import tr, url, HANDLE, debug

FILTERS = {
    'ongoing': {'status': 'ongoing', 'order': 'popularity'},
    'top': {'order': 'ranked'},
}
SEASON_NAMES = [('winter', 30011), ('spring', 30012), ('summer', 30013), ('fall', 30014)]
DIR_ACTIONS = (None, 'list', 'seasons', 'search', 'anime')
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


def _show_entry(sid, entry):
    a = _anime_info(sid)
    name = _title(a) if a else ''
    poster = shiki.image_url(a)

    head = xbmcgui.ListItem('[B]%s:[/B] %s' % (tr(30210), _entry_label(entry)))
    head.setArt({'icon': 'DefaultAddonSubtitles.png', 'thumb': 'DefaultAddonSubtitles.png'})
    xbmcplugin.addDirectoryItem(HANDLE, url(action='anime', id=sid, pick=1), head, True)

    if entry['movie_link'] and not entry['episodes']:
        li = xbmcgui.ListItem(name or tr(30209))
        li.setArt({'thumb': poster, 'icon': poster, 'poster': poster, 'fanart': poster})
        tag = li.getVideoInfoTag()
        tag.setMediaType('movie')
        tag.setTitle(name or tr(30209))
        li.setProperty('IsPlayable', 'true')
        target = url(action='play', link=entry['movie_link'], sid=sid, tid=entry['id'], title=name or tr(30209))
        xbmcplugin.addDirectoryItem(HANDLE, target, li, False)
        debug('movie item added: %s' % target)
        _end('movies')
        return

    multi = len({e['season'] for e in entry['episodes']}) > 1
    for idx, e in enumerate(entry['episodes'], 1):
        label = 'S%dE%d' % (e['season'], e['num']) if multi else tr(30208).format(e['num'])
        li = xbmcgui.ListItem(label)
        li.setArt({'thumb': poster, 'icon': poster, 'poster': poster, 'fanart': poster})
        tag = li.getVideoInfoTag()
        tag.setMediaType('episode')
        tag.setTitle(label)
        tag.setTvShowTitle(name)
        tag.setEpisode(e['num'])
        tag.setSeason(e['season'])
        li.setProperty('IsPlayable', 'true')
        target = url(action='play', link=e['link'], sid=sid, tid=entry['id'], ep=idx,
                     title='%s - %s' % (name, label) if name else label)
        xbmcplugin.addDirectoryItem(HANDLE, target, li, False)
    debug('episodes listed: %d (multi_season=%s)' % (len(entry['episodes']), multi))
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_UNSORTED)
    _end('episodes')


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
    stream = links[q]
    debug('chosen %dp: %s' % (q, stream))

    li = xbmcgui.ListItem(label=p.get('title', ''), path=stream)
    if p.get('title'):
        li.getVideoInfoTag().setTitle(p['title'])
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
    debug('setResolvedUrl(True) path=%s' % li.getPath())
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


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
