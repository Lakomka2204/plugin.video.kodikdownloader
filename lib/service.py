import time

import xbmc
import xbmcaddon

from lib import utils, watched

SAVE_EVERY = 30  # seconds between progress saves while playing


def _setting(kind, key, default):
    """Fresh Addon object each time: a long-lived one may not see settings changed after Kodi start."""
    try:
        a = xbmcaddon.Addon(utils.ID)
        return a.getSettingInt(key) if kind == 'int' else a.getSettingBool(key)
    except Exception:
        return default


class Watcher(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.cur = None       # nowplaying record of the episode being watched (ours only)
        self.pos = 0
        self.total = 0
        self.saved_at = 0.0
        self.next_req = None  # finished episode record: start the next one from the main loop
        self.refresh_at = 0.0

    # ---- player events (run on Kodi's thread, keep them light)
    def onAVStarted(self):
        if self.cur:  # previous one was replaced without a stop event
            self._finish(ended=False)
        data = watched.claim_now()
        if not data:
            utils.debug('service: AVStarted, not ours (no nowplaying record)')
            return
        self.cur = data
        self.pos = 0
        self.saved_at = time.time()
        try:
            self.total = int(self.getTotalTime())
        except RuntimeError:
            self.total = 0
        utils.debug('service: tracking %s total=%s' % (data, self.total))

    def onPlayBackStopped(self):
        self._finish(ended=False)

    def onPlayBackEnded(self):
        self._finish(ended=True)

    def onPlayBackError(self):
        utils.debug('service: playback error, tracking dropped')
        self.cur = None

    # ---- logic
    def _finish(self, ended):
        c, self.cur = self.cur, None
        if not c:
            return
        thr = _setting('int', 'watched_threshold', 85)
        pos = self.total if ended else self.pos
        done = watched.save_progress(c['sid'], c['num'], pos, self.total, thr, ended=ended)
        utils.debug('service: finish sid=%s num=%s pos=%s/%s ended=%s threshold=%s -> watched=%s' % (
            c['sid'], c['num'], pos, self.total, ended, thr, done))
        if ended and _setting('bool', 'autoplay_next', False):
            self.next_req = c
        else:
            self.refresh_at = time.time() + 1

    def _play_next(self, c):
        try:
            from lib import ui
            li = ui.autoplay_next(c)
        except Exception:
            utils.log_exc('service autoplay_next')
            return
        if li is None:
            utils.debug('service: no next episode')
            self.refresh_at = time.time() + 1
            return
        utils.debug('service: autoplay -> %s' % li.getPath())
        self.play(li.getPath(), li)

    def tick(self):
        """Called once a second from the main loop."""
        if self.cur and self.isPlayingVideo():
            try:
                self.pos = int(self.getTime())
                self.total = int(self.getTotalTime()) or self.total
            except RuntimeError:
                pass
            if time.time() - self.saved_at >= SAVE_EVERY:
                self.saved_at = time.time()
                watched.save_progress(self.cur['sid'], self.cur['num'], self.pos, self.total,
                                      _setting('int', 'watched_threshold', 85))
        if self.next_req:
            req, self.next_req = self.next_req, None
            self._play_next(req)
        if self.refresh_at and time.time() >= self.refresh_at:
            self.refresh_at = 0.0
            watched.refresh_listing()


def run():
    monitor = xbmc.Monitor()
    watcher = Watcher()
    utils.debug('service started')
    while not monitor.abortRequested():
        if monitor.waitForAbort(1):
            break
        watcher.tick()
    utils.debug('service stopped')


if __name__ == '__main__':
    run()
