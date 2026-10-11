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
        self.refresh_req = None  # after playback: refresh our list, then focus the next episode

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
        self.refresh_req = {'t0': time.time(), 'stage': 0, 'sid': c['sid'], 'num': c['num'] if done else None}

    def _select_next(self, r):
        """After a finished episode: put the focus on the next episode of the refreshed list."""
        if r['num'] is None:
            return
        hi = watched.get_anime(r['sid']).get('hi') or 0
        if hi and r['num'] >= hi:
            utils.debug('service: last episode finished, nothing to focus')
        elif xbmc.getInfoLabel('ListItem.Property(kk_continue)') == '1':
            utils.debug('service: focus is on the Continue item, it already points to the next episode')
        elif xbmc.getInfoLabel('ListItem.Episode') != str(r['num']):
            utils.debug('service: focused episode is %r, not %s: leaving the focus alone' % (
                xbmc.getInfoLabel('ListItem.Episode'), r['num']))
        else:
            utils.debug('service: focus next episode (Action Down)')
            xbmc.executebuiltin('Action(Down)')

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
        r = self.refresh_req
        if r and not self.isPlayingVideo():
            if time.time() - r['t0'] > 20:  # never got back to our list
                self.refresh_req = None
            elif r['stage'] == 0:
                if watched.refresh_listing():  # False while the video window is still closing
                    r['stage'], r['t1'] = 1, time.time()
            elif time.time() - r['t1'] >= 1.5 and not xbmc.getCondVisibility('Container.IsUpdating'):
                self.refresh_req = None
                self._select_next(r)


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
