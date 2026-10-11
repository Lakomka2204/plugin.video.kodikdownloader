"""Remote-button shortcut for switching voice-over during playback.

We do not know which buttons a remote has, so the setup asks the user to press one and records its
button code, then writes userdata/keymaps/kodikdownloader.xml that runs the plugin's 'switch' action
in the fullscreen video window and in the video OSD.
"""
import os
import time
from xml.sax.saxutils import escape

import xbmc
import xbmcgui
import xbmcvfs

from . import utils
from .utils import tr

_FILE = 'kodikdownloader.xml'
_CMD = 'RunPlugin(plugin://%s/?action=switch)' % utils.ID
_TEMPLATE = """<keymap>
  <FullscreenVideo>
    <keyboard>
      <key id="{code}">{cmd}</key>
    </keyboard>
  </FullscreenVideo>
  <VideoOSD>
    <keyboard>
      <key id="{code}">{cmd}</key>
    </keyboard>
  </VideoOSD>
</keymap>
"""
_IGNORED = set(range(100, 110)) | set(range(400, 420))  # mouse / touch actions


def _path():
    d = xbmcvfs.translatePath('special://userdata/keymaps')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _FILE)


class _Capture(xbmcgui.WindowDialog):
    """Blank dialog with a hint; remembers the button code of the first accepted key press."""

    def __init__(self, text):
        super().__init__()
        self.code = 0
        self.addControl(xbmcgui.ControlLabel(40, 300, 1200, 120, text, alignment=2))

    def onAction(self, action):
        aid = action.getId()
        if aid in (xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK):
            self.code = -1
        elif aid not in _IGNORED:
            self.code = action.getButtonCode() or 0  # 0: no raw code for this event, keep waiting
            utils.debug('keymap capture: action id=%s button code=%s' % (aid, self.code))


def setup():
    dlg = _Capture(tr(30238))
    dlg.show()
    monitor = xbmc.Monitor()
    t0 = time.time()
    while not dlg.code and time.time() - t0 < 30 and not monitor.abortRequested():
        xbmc.sleep(100)
    code = dlg.code
    dlg.close()
    del dlg
    if code <= 0:
        utils.notify(tr(30240))
        return
    with open(_path(), 'w', encoding='utf-8') as f:
        f.write(_TEMPLATE.format(code=code, cmd=escape(_CMD)))
    xbmc.executebuiltin('Action(reloadkeymaps)')
    utils.debug('keymap: wrote %s for button code %s' % (_path(), code))
    utils.notify(tr(30239).format(code))


def remove():
    try:
        os.remove(_path())
    except OSError:
        pass
    xbmc.executebuiltin('Action(reloadkeymaps)')
    utils.debug('keymap: removed')
    utils.notify(tr(30241))
