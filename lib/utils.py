import sys
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ID = ADDON.getAddonInfo('id')
NAME = ADDON.getAddonInfo('name')
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip('-').isdigit() else -1
UA = 'ShikiKodik-Kodi/0.1 (personal use)'
_HOME = xbmcgui.Window(10000)


def tr(string_id):
    return ADDON.getLocalizedString(string_id)


def get_str(key, default=''):
    try:
        return ADDON.getSettingString(key)
    except Exception:
        return default


def get_int(key, default=0):
    try:
        return ADDON.getSettingInt(key)
    except Exception:
        return default


def get_bool(key, default=False):
    try:
        return ADDON.getSettingBool(key)
    except Exception:
        return default


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s] %s' % (ID, msg), level)


_DEBUG = None


def debug(msg):
    """Verbose log. Controlled only by the addon's own 'Debug logging' setting.
    Written at INFO level so it shows up in kodi.log even without Kodi's debug logging."""
    global _DEBUG
    if _DEBUG is None:
        _DEBUG = get_bool('debug', True)
    if _DEBUG:
        xbmc.log('[%s] %s' % (ID, msg), xbmc.LOGINFO)


def log_exc(where):
    import traceback
    xbmc.log('[%s] EXCEPTION in %s:\n%s' % (ID, where, traceback.format_exc()), xbmc.LOGERROR)


def log_environment(params):
    token = get_str('kodik_token')
    debug('=== main entry: addon=%s argv=%s params=%s' % (ADDON.getAddonInfo('version'), sys.argv, params))
    debug('settings: shiki_domain=%s custom=%r title_lang=%s page_size=%s cache_ttl=%s show_adult=%s pin_set=%s '
          'kodik_base=%s token_set=%s(len=%d) quality=%s use_isa=%s headers_set=%s' % (
              get_str('shiki_domain'), get_str('shiki_domain_custom'), get_int('title_lang'), get_int('page_size'),
              get_int('cache_ttl'), get_bool('show_adult'), bool(get_str('adult_pin')), get_str('kodik_base'),
              bool(token), len(token), get_int('default_quality'), get_bool('use_isa'),
              bool(get_str('stream_headers'))))
    debug('profile dir: %s' % PROFILE)


def notify(msg, icon=xbmcgui.NOTIFICATION_INFO):
    xbmcgui.Dialog().notification(NAME, msg, icon, 4000)


def url(**kwargs):
    q = {k: v for k, v in kwargs.items() if v is not None}
    return 'plugin://%s/?%s' % (ID, urlencode(q))


_adult = None


def adult_allowed():
    """18+ switch. With a PIN set, it is asked once per Kodi session."""
    global _adult
    if _adult is not None:
        return _adult
    if not get_bool('show_adult'):
        _adult = False
        return _adult
    pin = get_str('adult_pin').strip()
    if not pin or _HOME.getProperty(ID + '.adult_ok') == '1':
        _adult = True
        return _adult
    value = xbmcgui.Dialog().input(tr(30206), type=xbmcgui.INPUT_NUMERIC,
                                   option=xbmcgui.ALPHANUM_HIDE_INPUT)
    if value == pin:
        _HOME.setProperty(ID + '.adult_ok', '1')
        _adult = True
    else:
        if value:
            notify(tr(30207), xbmcgui.NOTIFICATION_ERROR)
        _adult = False
    return _adult
