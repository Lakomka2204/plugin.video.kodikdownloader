import time

import xbmc
import xbmcaddon

from lib import cache, master, shikimori, utils, watched

SAVE_EVERY = 30  # seconds between progress saves while playing
CAL_EVERY = 1800  # seconds between new-episode checks
CAL_MAX_AGE = 3 * 86400  # do not announce episodes older than this


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
        self.seek_req = None  # (time, position): seek after a voice/quality switch
        self.fallback_req = None  # master playlist failed to open: play a single quality instead
        self.refresh_req = None  # after playback: refresh our list, then focus the next episode

    # ---- player events (run on Kodi's thread, keep them light)
    def onAVStarted(self):
        if self.cur:  # previous one was replaced without a stop event
            self._finish(ended=False)
        self.refresh_req = None  # a new playback started (also after a voice/quality switch)
        data = watched.claim_now()
        if not data:
            utils.debug('service: AVStarted, not ours (no nowplaying record)')
            return
        self.cur = data
        watched.set_current(data)
        self.pos = 0
        self.saved_at = time.time()
        try:
            self.total = int(self.getTotalTime())
        except RuntimeError:
            self.total = 0
        utils.debug('service: tracking %s total=%s' % (data, self.total))
        if data.get('seek_to'):
            self.seek_req = (time.time() + 2.0, int(data['seek_to']))

    def onPlayBackStopped(self):
        self._finish(ended=False)

    def onPlayBackEnded(self):
        self._finish(ended=True)

    def onPlayBackError(self):
        self.cur = None
        watched.clear_current()
        rec = watched.take_fallback()
        utils.debug('service: playback error, fallback=%s' % ('yes' if rec else 'no'))
        self.fallback_req = rec

    # ---- logic
    def _finish(self, ended):
        c, self.cur = self.cur, None
        watched.clear_current()
        if not c:
            return
        thr = _setting('int', 'watched_threshold', 85)
        if ended and self.total > 0 and self.pos * 100.0 / self.total < thr:
            # ISA reports EOF when a stream (e.g. after a quality switch) cannot be read: not a real ending
            utils.debug('service: EOF at %s/%s is below the %s%% threshold, treated as a stop' % (
                self.pos, self.total, thr))
            ended = False
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
        if self.seek_req and time.time() >= self.seek_req[0]:
            _, pos = self.seek_req
            self.seek_req = None
            if self.isPlayingVideo():
                utils.debug('service: seek to %ss after switch' % pos)
                self.seekTime(pos)
        if self.fallback_req:
            rec, self.fallback_req = self.fallback_req, None
            try:
                from lib import ui
                li = ui.fallback_item(rec)
                utils.debug('service: master playlist failed, retrying with a single quality')
                self.play(li.getPath(), li)
            except Exception:
                utils.log_exc('service fallback')
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


def check_new_episodes():
    """Notify about episodes (of anime with watch history) that have come out since the last check.
    Stored per anime: the upcoming episode seen in the calendar. When the calendar moves on to a later
    episode (or the anime leaves it), the stored one has aired."""
    if not _setting('bool', 'notify_new', True):
        return
    ids = watched.followed()
    if not ids:
        return
    seen = {}
    for e in shikimori.calendar(fresh=True):
        a = e.get('anime') or {}
        if a.get('id') in ids:
            seen[a['id']] = (int(e.get('next_episode') or 0), shikimori.parse_time(e.get('next_episode_at')) or 0, a)
    state = cache.pref_get('cal', {})
    now = time.time()
    out = {}
    for sid in set(seen) | set(int(k) for k in state):
        prev = state.get(str(sid))
        cur = seen.get(sid)
        if prev and (not cur or prev['ep'] < cur[0]) and prev['at'] <= now and now - prev['at'] < CAL_MAX_AGE:
            utils.debug('service: episode %s of %s is out' % (prev['ep'], sid))
            utils.notify(utils.tr(30029).format(prev['name'], prev['ep']))
        if cur:
            out[str(sid)] = {'ep': cur[0], 'at': cur[1], 'name': utils.anime_title(cur[2])}
    cache.pref_set('cal', out)


def run():
    monitor = xbmc.Monitor()
    watcher = Watcher()
    server = master.start()
    utils.debug('service started (master server %s)' % ('on' if server else 'OFF'))
    next_cal = time.time() + 60
    while not monitor.abortRequested():
        if monitor.waitForAbort(1):
            break
        watcher.tick()
        if time.time() >= next_cal and not watcher.isPlayingVideo():
            next_cal = time.time() + CAL_EVERY
            try:
                check_new_episodes()
            except Exception:
                utils.log_exc('service check_new_episodes')
    master.stop(server)
    utils.debug('service stopped')


if __name__ == '__main__':
    run()
