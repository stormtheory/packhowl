# client/ptt.py

from PySide6 import QtCore
from PySide6.QtCore import Qt
import logging
from pynput import keyboard

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

class PTTManager(QtCore.QObject):
    """
    Handles Push-To-Talk (PTT) functionality:
    - Installs Qt event filter for GUI key presses/releases
    - Starts a pynput global listener thread for system-wide PTT key capture
    - Maps configurable PTT key strings to Qt keys and pynput keys
    - Emits signals on PTT press/release for integration with audio engine or UI
    """

    # Signals emitted when PTT is pressed/released
    pttPressed = QtCore.Signal()
    pttReleased = QtCore.Signal()

    def __init__(self, settings, audio_engine=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.audio_engine = audio_engine
        self.ptt_pressed = False

        # ── PTT ───────────────────────────────────────────────
        self.install_ptt_key_filter()
        self.start_global_ptt_listener()

    def install_ptt_key_filter(self):
        # Disabled: Do not install global event filter
        """
        Installs the PTT key filter for GUI key events.

        Maps a user-configured key string from settings to a Qt.Key
        and prepares the object for Qt event filtering.
        """
        ptt_key_str = self.settings.get("ptt_key", "LeftAlt")
        key_map = {
            "LeftAlt": Qt.Key_Alt,
            "RightAlt": Qt.Key_Alt,
            "LeftShift": Qt.Key_Shift,
            "RightShift": Qt.Key_Shift,
            "LeftCtrl": Qt.Key_Control,
            "RightCtrl": Qt.Key_Control,
            "Space": Qt.Key_Space,
        }
        self.ptt_key = key_map.get(ptt_key_str, Qt.Key_Alt)
        self.ptt_pressed = False

        # Install this instance as event filter on the application/window
        # Note: The caller must call `installEventFilter(ptt_manager_instance)`
        # on the relevant Qt object, e.g. main window
        logging.debug(f"[PTT] Installed PTT key filter for key: {self.ptt_key}")

    def start_global_ptt_listener(self):
        """
        Starts a daemon thread that listens for the configured PTT key
        even when the application is not focused.

        This uses the `pynput` library for global keyboard hooks.
        """

        # ------------------------------------------------------------------
        # 1. Resolve the key the user chose in settings
        # ------------------------------------------------------------------
        ptt_key_name = self.settings.get("ptt_key", "leftalt")   # e.g. "LeftAlt"
        ptt_name_lc  = ptt_key_name.lower()

        # map settings‑string → list of pynput Key objects (for non‑character keys)
        special_map = {
            "leftalt":   [keyboard.Key.alt_l],
            "rightalt":  [keyboard.Key.alt_r],
            "alt":       [keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r],
            "leftctrl":  [keyboard.Key.ctrl_l],
            "rightctrl": [keyboard.Key.ctrl_r],
            "ctrl":      [keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r],
            "leftshift": [keyboard.Key.shift_l],
            "rightshift":[keyboard.Key.shift_r],
            "shift":     [keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r],
            "space":     [keyboard.Key.space],
            "f1":        [keyboard.Key.f1],
            "f2":        [keyboard.Key.f2],
            # … add more if you need them
        }

        # Character key?  (letters, numbers, etc.)
        if ptt_name_lc not in special_map:
            self._ptt_is_special   = False
            self._ptt_char_expected = ptt_name_lc  # single lowercase char
            self._ptt_special_keys  = []
        else:
            self._ptt_is_special   = True
            self._ptt_char_expected = None
            self._ptt_special_keys  = special_map[ptt_name_lc]

        # ------------------------------------------------------------------
        # 2. Helper to decide if the incoming pynput key matches PTT
        # ------------------------------------------------------------------
        def _matches_ptt(key) -> bool:
            if self._ptt_is_special:
                return key in self._ptt_special_keys
            # character key
            try:
                return key.char and key.char.lower() == self._ptt_char_expected
            except AttributeError:
                return False    # key.char doesn't exist on special keys

        # ------------------------------------------------------------------
        # 3. Handlers
        # ------------------------------------------------------------------
        def on_press(key):
            if _matches_ptt(key) and not self.ptt_pressed:
                self.ptt_pressed = True
                if self.audio_engine:
                    self.audio_engine.set_ptt_pressed(True)
                logging.debug("[GlobalPTT] key pressed")
                self.pttPressed.emit()

        def on_release(key):
            if _matches_ptt(key) and self.ptt_pressed:
                self.ptt_pressed = False
                if self.audio_engine:
                    self.audio_engine.set_ptt_pressed(False)
                logging.debug("[GlobalPTT] key released")
                self.pttReleased.emit()

        # ------------------------------------------------------------------
        # 4. Start the daemon listener
        # ------------------------------------------------------------------
        self.global_ptt_listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release,
            suppress=False,      # do NOT block the key for other apps
        )
        self.global_ptt_listener.daemon = True
        self.global_ptt_listener.start()
        logging.info("[GlobalPTT] listener started")

    def eventFilter(self, obj, event):
        """
        Qt event filter to detect PTT key presses and releases while the app is focused.

        Returns True to stop further event handling when PTT key is processed.
        Otherwise returns False to allow event propagation.
        """
        if event.type() == QtCore.QEvent.KeyPress:
            if event.key() == self.ptt_key and not self.ptt_pressed:
                self.ptt_pressed = True
                if self.audio_engine:
                    self.audio_engine.set_ptt_pressed(True)
                logging.debug(f"[GUI] PTT key pressed: {event.key()}")
                self.pttPressed.emit()
                return True  # Stop further handling

        elif event.type() == QtCore.QEvent.KeyRelease:
            if event.key() == self.ptt_key and self.ptt_pressed:
                self.ptt_pressed = False
                if self.audio_engine:
                    self.audio_engine.set_ptt_pressed(False)
                logging.debug(f"[GUI] PTT key released: {event.key()}")
                self.pttReleased.emit()
                return True  # Stop further handling

        return False
