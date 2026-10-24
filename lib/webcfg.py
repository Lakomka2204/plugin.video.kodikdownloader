"""Temporary web page for entering settings (Kodik token ...) from a phone/PC in the same network.

The plugin button (action=web_setup) sets a window property; the service (Manager.tick) starts a small HTTP
server on the LAN for WEB_MINUTES, protected by a random 4-digit code, and publishes URL+code in window
properties; the plugin shows them. After a successful save, or after 5 wrong codes, or on timeout, it stops."""
import hmac
import html
import http.server
import random
import socket
import threading
import time
from urllib.parse import parse_qs

import xbmc
import xbmcaddon

from . import utils

WEB_MINUTES = 10
MAX_TRIES = 5
_REQ, _URL, _CODE = utils.ID + '.web_req', utils.ID + '.web_url', utils.ID + '.web_code'
FIELDS = (('kodik_token', True), ('kodik_base', False))  # (setting id, secret)
_T = {
    'en': {'title': 'Plugin setup', 'code': 'Code shown on the TV', 'kodik_token': 'Kodik API token',
           'kodik_base': 'Kodik API address', 'keep': 'leave empty to keep the current value',
           'set': 'currently set', 'unset': 'not set', 'save': 'Save', 'ok': 'Saved. You can close this page.',
           'bad': 'Wrong code', 'locked': 'Too many attempts, the page is closed.'},
    'ru': {'title': 'Настройка плагина', 'code': 'Код, показанный на ТВ', 'kodik_token': 'Токен Kodik API',
           'kodik_base': 'Адрес Kodik API', 'keep': 'оставьте пустым, чтобы не менять',
           'set': 'сейчас задан', 'unset': 'не задан', 'save': 'Сохранить',
           'ok': 'Сохранено. Страницу можно закрыть.', 'bad': 'Неверный код',
           'locked': 'Слишком много попыток, страница закрыта.'},
}


def _tr():
    return _T['ru' if utils.use_ru() else 'en']


def _current(key):
    try:
        return xbmcaddon.Addon(utils.ID).getSettingString(key)
    except Exception:
        return ''


def _page(msg=''):
    t = _tr()
    rows = ['<label>%s<input name="code" inputmode="numeric" autocomplete="off" required></label>' % t['code']]
    for key, secret in FIELDS:
        cur = _current(key)
        hint = (t['set'] if cur else t['unset']) + ('; ' + t['keep'] if secret else '')
        val = '' if secret else html.escape(cur, quote=True)
        rows.append('<label>%s <small>(%s)</small><input name="%s" value="%s" autocomplete="off" %s></label>' % (
            t[key], hint, key, val, 'type="password"' if secret else 'type="text"'))
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%(title)s</title><style>body{font-family:sans-serif;max-width:30em;margin:1em auto;padding:0 1em}'
            'label{display:block;margin:1em 0}input{display:block;width:100%%;padding:.6em;font-size:1em;box-sizing:border-box}'
            'button{padding:.7em 1.5em;font-size:1em}p{color:#b00}</style><h2>%(title)s</h2>%(msg)s'
            '<form method="post">%(rows)s<button>%(save)s</button></form>' % {
                'title': t['title'], 'save': t['save'], 'rows': ''.join(rows),
                'msg': '<p>%s</p>' % html.escape(msg) if msg else ''}).encode('utf-8')


class _Handler(http.server.BaseHTTPRequestHandler):
    owner = None  # Manager

    def _send(self, body, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(_page())

    def do_POST(self):
        o, t = self.owner, _tr()
        try:
            n = min(int(self.headers.get('Content-Length') or 0), 4096)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode('utf-8', 'replace')).items()}
        except Exception:
            form = {}
        if o.tries >= MAX_TRIES:
            return self._send(_page(t['locked']), 403)
        if not hmac.compare_digest(form.get('code', '').strip(), o.code):
            o.tries += 1
            if o.tries >= MAX_TRIES:
                o.stop_at = 0  # close on the next tick
            return self._send(_page(t['bad']), 403)
        addon = xbmcaddon.Addon(utils.ID)
        for key, secret in FIELDS:
            v = form.get(key, '').strip()
            if v and len(v) < 300 and (key != 'kodik_base' or v.startswith('http')):
                addon.setSettingString(key, v)
                utils.debug('webcfg: %s updated' % key)
        utils.notify(utils.tr(30033))
        o.stop_at = time.time() + 3
        self._send(_page(t['ok']))

    def log_message(self, fmt, *args):
        pass


def _ip():
    ip = xbmc.getInfoLabel('Network.IPAddress')
    if ip and ip[0].isdigit():
        return ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))  # no packet is sent
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


class Manager:
    """Lives in the service."""

    def __init__(self):
        self.server = None
        self.code = ''
        self.tries = 0
        self.stop_at = 0

    def tick(self):
        if utils._HOME.getProperty(_REQ):
            utils._HOME.clearProperty(_REQ)
            if not self.server:
                self._start()
            else:
                self.stop_at = time.time() + WEB_MINUTES * 60
        if self.server and time.time() >= self.stop_at:
            self.stop()

    def _start(self):
        try:
            _Handler.owner = self
            self.code = '%04d' % random.SystemRandom().randrange(10000)
            self.tries = 0
            try:
                self.server = http.server.ThreadingHTTPServer(('0.0.0.0', 8765), _Handler)
            except OSError:
                self.server = http.server.ThreadingHTTPServer(('0.0.0.0', 0), _Handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.stop_at = time.time() + WEB_MINUTES * 60
            utils._HOME.setProperty(_CODE, self.code)
            utils._HOME.setProperty(_URL, 'http://%s:%d/' % (_ip(), self.server.server_address[1]))
            utils.debug('webcfg: started on %s' % utils._HOME.getProperty(_URL))
        except Exception:
            utils.log_exc('webcfg start')
            self.server = None

    def stop(self):
        s, self.server = self.server, None
        utils._HOME.clearProperty(_URL)
        utils._HOME.clearProperty(_CODE)
        if s:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass
            utils.debug('webcfg: stopped')


def request():
    """Plugin side (settings button): ask the service to open the page, then show URL and code."""
    import xbmcgui
    utils._HOME.setProperty(_REQ, str(time.time()))
    for _ in range(40):
        if utils._HOME.getProperty(_URL):
            break
        xbmc.sleep(100)
    link, code = utils._HOME.getProperty(_URL), utils._HOME.getProperty(_CODE)
    if not link:
        utils._HOME.clearProperty(_REQ)
        xbmcgui.Dialog().ok(utils.NAME, utils.tr(30032))
        return
    xbmcgui.Dialog().ok(utils.NAME, utils.tr(30034).format(link, code, WEB_MINUTES))
