# client/ptt.py

from PySide6 import QtCore
from PySide6.QtCore import Qt
import logging
from pynput import keyboard
import time
import pygame
import threading

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

PYGAME_AVAILABLE = True

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
    pttGamepadButtonLearned = QtCore.Signal(int)  # emits new button index
    pttInputLearned = QtCore.Signal(dict)
        

    def __init__(self, settings, audio_engine=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.audio_engine = audio_engine
        self.ptt_pressed = False

        # ── PTT ───────────────────────────────────────────────
        self.install_ptt_key_filter()
        self.start_global_ptt_listener()

    def install_ptt_key_filter(self):
        key_map = {
            "LeftAlt": Qt.Key_Alt,
            "RightAlt": Qt.Key_Alt,
            "LeftShift": Qt.Key_Shift,
            "RightShift": Qt.Key_Shift,
            "LeftCtrl": Qt.Key_Control,
            "RightCtrl": Qt.Key_Control,
            "Space": Qt.Key_Space,
            # Add more if needed
        }
        self.ptt_key_qt = self.settings.get("ptt_key_qt", "LeftAlt")
        self.ptt_key = key_map.get(self.ptt_key_qt, Qt.Key_Alt)
        self.ptt_pressed = False
        logging.debug(f"[PTT] Installed GUI PTT key filter for: {self.ptt_key_qt}")



    def start_global_ptt_listener(self):
        """
        Starts a daemon thread that listens for the configured PTT key
        even when the application is not focused.
        This uses the `pynput` library for global keyboard hooks.
        """
        self.stop_global_ptt_listener()

        # ──────────────────────────────────────────────
        # 1. Load and normalize PTT key from flat settings
        # ──────────────────────────────────────────────
        ptt_type = self.settings.get("ptt_key_type", "keyboard")
        ptt_code = self.settings.get("ptt_key_code", "alt_l")
        ptt_qt = self.settings.get("ptt_key_qt", "LeftAlt")

        # Compose internal ptt_key dict for pynput listener
        self.ptt_key = {
            "type": ptt_type,
            "key": ptt_code.lower() if isinstance(ptt_code, str) else str(ptt_code)
        }

        # Store Qt key string for GUI event filter separately
        self.ptt_key_qt = ptt_qt

        logging.debug(f"[GlobalPTT] Loaded ptt_key: {self.ptt_key}, ptt_key_qt: {self.ptt_key_qt}")

        # Only support keyboard type for global listener currently
        if self.ptt_key.get('type') != 'keyboard':
            logging.info("[GlobalPTT] Not starting listener — PTT is not a keyboard type.")
            return

        # Normalize key to string form
        key = self.ptt_key.get('key')
        if isinstance(key, keyboard.Key):
            normalized_key = key.name.lower() if key.name else ""
        elif isinstance(key, str):
            normalized_key = key.lower()
        else:
            normalized_key = ""

        self.ptt_key['key'] = normalized_key  # ← force internal standard

        logging.debug(f"[GlobalPTT] Normalized key to: {normalized_key}")

        # Normalize key name for lookup
        key = self.ptt_key['key']
        if isinstance(key, keyboard.Key):
            ptt_name_lc = key.name.lower() if key.name else ""  # e.g. 'alt_l'
        elif isinstance(key, str):
            ptt_name_lc = key.lower()
        else:
            ptt_name_lc = ""


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
            if self._matches_ptt({'type': 'keyboard', 'key': key}) and not self.ptt_pressed:
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
    
    def listen_for_next_input(self):
        """
        Enters learn mode: captures the next keyboard or gamepad input and sets it as the PTT trigger.
        Stops global PTT listener temporarily to prevent leaking X connections.
        """
        def _learn():
            logging.info("[PTT] Waiting for next keyboard/gamepad input…")

            # Stop any existing listener to avoid too many X connections
            self.stop_global_ptt_listener()

            import pygame
            pygame.init()
            pygame.joystick.init()
            joystick = pygame.joystick.Joystick(0) if pygame.joystick.get_count() > 0 else None
            if joystick:
                joystick.init()

            from pynput import keyboard as pkb

            try:
                with pkb.Events() as events:
                    while True:
                        try:
                            event = events.get(0.01)  # Non-blocking wait
                            if isinstance(event, pkb.Events.Press):
                                key = event.key

                                # Extract normalized key name:
                                if isinstance(key, pkb.Key):
                                    # Special keys like ctrl_l, alt_l, etc.
                                    key_name = key.name if key.name else str(key).lower()
                                elif hasattr(key, 'char') and key.char:
                                    # Character keys (letters, numbers, etc.)
                                    key_name = key.char.lower()
                                else:
                                    # Fallback
                                    key_name = str(key).lower()

                                # Save flat string settings, avoid saving pynput objects
                                self.settings['ptt_key_type'] = 'keyboard'
                                self.settings['ptt_key_code'] = key_name

                                # Compose internal dict for runtime use
                                self.ptt_key = {
                                    'type': 'keyboard',
                                    'key': key_name
                                }

                                if hasattr(self.settings, "save"):
                                    self.settings.save()

                                logging.info(f"[PTT] Learned keyboard key: {key_name}")
                                self.pttInputLearned.emit(self.ptt_key)

                                break
                        except Exception as ex:
                            logging.warning(f"[PTT] Exception during learning keyboard input: {ex}")

                        # Gamepad check
                        pygame.event.pump()
                        if joystick:
                            for button_index in range(joystick.get_numbuttons()):
                                if joystick.get_button(button_index):
                                    self.ptt_key = {'type': 'gamepad', 'button': button_index}
                                    self.settings['ptt_key_type'] = 'gamepad'
                                    self.settings['ptt_key_code'] = str(button_index)
                                    if hasattr(self.settings, "save"):
                                        self.settings.save()
                                    logging.info(f"[PTT] Learned gamepad button: {button_index}")
                                    self.pttInputLearned.emit(self.ptt_key)
                                    return

                        time.sleep(0.01)
            finally:
                # Always restart the global listener once learning is done
                self.global_ptt_listener.stop()
                self.start_global_ptt_listener()
                logging.info("[PTT] Global listener restarted after learning input.")

        threading.Thread(target=_learn, daemon=True).start()



    def stop_global_ptt_listener(self):
        """Stop the pynput global listener if it’s running."""
        if hasattr(self, 'global_ptt_listener') and self.global_ptt_listener:
            try:
                self.global_ptt_listener.stop()
                logging.info("[PTT Init] Previous global listener stopped.")
            except Exception as e:
                logging.warning(f"[PTT Init] Error stopping previous listener: {e}")
            self.global_ptt_listener = None

    def _matches_ptt(self, input_event):
        """
        Safely checks if the incoming input matches the stored PTT trigger.
        Returns False if the stored trigger is invalid.
        """
        """
            Checks if the incoming input matches the configured PTT trigger.
            `input_event` should be a dict like:
            {'type': 'keyboard', 'key': 'a'}
            {'type': 'keyboard', 'key': keyboard.Key.alt_l}
            {'type': 'gamepad', 'button': 0}
            """
        if not isinstance(self.ptt_key, dict):
            return False

        if input_event['type'] != self.ptt_key.get('type'):
            return False

        if input_event['type'] == 'keyboard':
            input_key = input_event['key']
            input_key_name = (
                input_key.name if isinstance(input_key, keyboard.Key) else str(input_key).lower()
            )
            logging.debug(f"[PTT] Comparing input '{input_key_name}' to expected '{self.ptt_key.get('key')}'")
            return input_key_name == self.ptt_key.get('key')

        if input_event['type'] == 'gamepad':
            logging.debug(f"[PTT] Comparing input '{input_key_name}' to expected '{self.ptt_key.get('key')}'")
            return input_event['button'] == self.ptt_key.get('button')
            
        return False
