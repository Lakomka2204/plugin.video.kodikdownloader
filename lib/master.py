"""Master playlists for InputStream Adaptive.

Kodik gives one separate HLS manifest per quality. We list them in a master playlist so ISA can offer
the qualities in Video Settings -> Video stream.

- The plugin (short-lived) calls publish(): writes the playlist to a file in addon_data and returns an
  http://127.0.0.1:<port>/m/<token>.m3u8 URL.
- The service (long-lived) runs the tiny HTTP server that serves those files (start()/stop()). Its port is
  shared through a Home window property. Only 127.0.0.1 is bound.
"""
import http.server
import os
import re
import threading
import time
import urllib.request
import uuid

import xbmcgui

from . import utils

_PORT_PROP = utils.ID + '.master_port'
_NAME_RE = re.compile(r'^/m/([0-9a-f]{32})\.m3u8$')
_KEEP_SECONDS = 12 * 3600
# Rough values: ISA only shows them in the stream list / uses them for ordering.
_BANDWIDTH = {360: 800000, 480: 1300000, 720: 2800000, 1080: 5500000}


def _dir():
    d = os.path.join(utils.PROFILE, 'master')
    os.makedirs(d, exist_ok=True)
    return d


def build(links):
    """{quality(int): manifest url} -> master playlist text, lowest quality first."""
    lines = ['#EXTM3U']
    for q in sorted(links):
        bw = _BANDWIDTH.get(q, q * 3500)
        lines.append('#EXT-X-STREAM-INF:BANDWIDTH=%d,RESOLUTION=%dx%d' % (bw, round(q * 16 / 9 / 2) * 2, q))
        lines.append(links[q])
    return '\n'.join(lines) + '\n'


def _cleanup(everything=False):
    now = time.time()
    d = _dir()
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if everything or now - os.path.getmtime(p) > _KEEP_SECONDS:
                os.remove(p)
        except OSError:
            pass


def publish(links):
    """Plugin side. Returns a URL of a master playlist, or None if the service is not serving
    (then the caller plays a single quality as before)."""
    port = xbmcgui.Window(10000).getProperty(_PORT_PROP)
    if not port:
        utils.debug('master: service server is not running, no master playlist')
        return None
    token = uuid.uuid4().hex
    try:
        _cleanup()
        with open(os.path.join(_dir(), token + '.m3u8'), 'w', encoding='utf-8') as f:
            f.write(build(links))
        url = 'http://127.0.0.1:%s/m/%s.m3u8' % (port, token)
        with urllib.request.urlopen(url, timeout=2) as r:  # is the server really alive?
            ok = r.status == 200
    except Exception:
        utils.log_exc('master.publish')
        return None
    utils.debug('master: published %s (alive=%s)' % (url, ok))
    return url if ok else None


class _Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, head_only):
        m = _NAME_RE.match(self.path.split('?', 1)[0])
        path = os.path.join(_dir(), m.group(1) + '.m3u8') if m else ''
        if not m or not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self):
        self._send(False)

    def do_HEAD(self):
        self._send(True)

    def log_message(self, fmt, *args):
        utils.debug('master http: ' + fmt % args)


def start():
    """Service side. Returns the server, or None if it could not start."""
    try:
        _cleanup(everything=True)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        xbmcgui.Window(10000).setProperty(_PORT_PROP, str(port))
        utils.debug('master: server listening on 127.0.0.1:%s' % port)
        return server
    except Exception:
        utils.log_exc('master.start')
        return None


def stop(server):
    xbmcgui.Window(10000).clearProperty(_PORT_PROP)
    if server:
        server.shutdown()
        server.server_close()
