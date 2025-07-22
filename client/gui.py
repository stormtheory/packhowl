# client/gui.py

"""
GUI module for Silent Link client using PySide6
• MainWindow class contains all UI components and logic
• Audio controls, chat, user list, status log, system tray integration
• Signals connected to network thread and audio engine
"""

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Qt
import sounddevice as sd
import logging
from pynput import keyboard

from config import (APP_NAME, APP_ICON_PATH, CLIENT_IP, SSL_CA_PATH, CERTS_DIR,
                    DATA_DIR, ensure_data_dirs)
from config import SERVER_PORT as DEFAULT_SERVER_PORT

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

class MainWindow(QtWidgets.QMainWindow):
    RATE_LIMIT_MS = 1000  # 1 message per second rate limit

    def __init__(self, settings, net_thread, audio_engine):
        super().__init__()
        self.settings = settings
        self.net = net_thread
        self.audio_engine = audio_engine
        
        if self.audio_engine:
            self.audio_engine.inputLevel.connect(self.update_mic_level)
            self.audio_engine.outputLevel.connect(self.update_spk_level)

        self.start_global_ptt_listener()

        # Create a central widget container for QMainWindow
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)  # Set central widget for QMainWindow

        # Create main layout and set it on the central widget
        main_layout = QtWidgets.QVBoxLayout()
        central_widget.setLayout(main_layout)

        # Setup status bar using QMainWindow's built-in statusBar()
        self.status_bar = self.statusBar()
        self.status_bar.setFixedHeight(20)

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QtGui.QIcon(APP_ICON_PATH))
        self.resize(960, 500)

        self._last_sent_msecs = 0  # Rate limiter state

        # ── Left pane (server + user list + status) ───────────────────────
        left_layout = QtWidgets.QVBoxLayout()
        self.server_lbl = QtWidgets.QLabel()
        self.users = QtWidgets.QTreeWidget()
        self.users.setHeaderLabels(["SPK", "MIC", "Name", "IP"])
        self.users.setColumnWidth(0, 50)   # SPK
        self.users.setColumnWidth(1, 50)   # MIC
        self.users.setColumnWidth(2, 120)  # Name
        self.users.setColumnWidth(3, 100)  # IP
        self.status = QtWidgets.QListWidget()
        left_layout.addWidget(self.server_lbl)
        left_layout.addWidget(self.users, 3)
        left_layout.addWidget(QtWidgets.QLabel("Status / Errors"))
        left_layout.addWidget(self.status, 1)

        # ── Right pane (chat) ─────────────────────────────────────────────
        right_layout = QtWidgets.QVBoxLayout()
        self.chat_view = QtWidgets.QTextBrowser()
        bottom = QtWidgets.QHBoxLayout()
        self.chat_edit = QtWidgets.QLineEdit()
        send_btn = QtWidgets.QPushButton("Send")
        bottom.addWidget(self.chat_edit, 1)
        bottom.addWidget(send_btn)
        right_layout.addWidget(self.chat_view, 3)
        right_layout.addLayout(bottom)

        # ── Audio controls ─────────────────────────────────────────────────────
        self.ptt_pressed = False
        
        audio_controls = QtWidgets.QGroupBox("Audio Controls")
        audio_layout = QtWidgets.QGridLayout()

        # Mic controls (buttons + slider + combo + check)
        self.mute_mic_btn = QtWidgets.QPushButton("Mute Mic")
        self.mic_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.mic_slider.setRange(0, 100)
        self.mic_slider.setValue(100)
        self.input_device = QtWidgets.QComboBox()
        self.audio_mode_combo = QtWidgets.QComboBox()
        self.audio_mode_combo.addItems(["Open Mic", "Push to Talk", "Voice Activated"])


        # Speaker controls (buttons + slider + combo + check)
        self.mute_spk_btn = QtWidgets.QPushButton("Mute Audio")
        self.spk_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.spk_slider.setRange(0, 100)
        self.spk_slider.setValue(100)
        self.output_device = QtWidgets.QComboBox()

        # ── Layout top row: Mic controls ─────────────────────────────
        audio_layout.addWidget(self.mute_mic_btn, 0, 0)
        audio_layout.addWidget(self.mic_slider, 0, 1)
        audio_layout.addWidget(self.input_device, 0, 2)
        audio_layout.addWidget(self.audio_mode_combo, 0, 3)


        # ── Layout second row: Speaker controls ──────────────────────
        audio_layout.addWidget(self.mute_spk_btn, 1, 0)
        audio_layout.addWidget(self.spk_slider, 1, 1)
        audio_layout.addWidget(self.output_device, 1, 2)
        
        # ── Layout third row: Mic & Speaker level bars ───────────────
        self.mic_level_bar = QtWidgets.QProgressBar()
        self.mic_level_bar.setRange(0, 100)
        self.mic_level_bar.setTextVisible(False)
        self.mic_level_bar.setFixedHeight(10)

        self.spk_level_bar = QtWidgets.QProgressBar()
        self.spk_level_bar.setRange(0, 100)
        self.spk_level_bar.setTextVisible(False)
        self.spk_level_bar.setFixedHeight(10)

        # ── Mic Gain Slider ────────────────────────────────────────────────
        self.mic_gain_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.mic_gain_slider.setRange(10, 500)  # Represents 1.0 to 5.0 gain
        self.mic_gain_slider.setValue(int(self.settings.get("mic_gain", 2.0) * 100))
        self.mic_gain_slider.setToolTip("Mic Gain (1.0× to 5.0×)")
        self.mic_gain_slider.valueChanged.connect(self.update_mic_gain)

        audio_layout.addWidget(QtWidgets.QLabel("Mic Gain"), 2, 0)
        audio_layout.addWidget(self.mic_gain_slider, 2, 1, 1, 3)

        # Level‑meter row (row 3) – updated index
        audio_layout.addWidget(QtWidgets.QLabel("Mic Level", alignment=Qt.AlignRight),     3, 0)
        audio_layout.addWidget(self.mic_level_bar,                3, 1)
        audio_layout.addWidget(QtWidgets.QLabel("Speaker Level", alignment=Qt.AlignRight), 3, 2)
        audio_layout.addWidget(self.spk_level_bar,                3, 3)


        audio_controls.setLayout(audio_layout)
        right_layout.addWidget(audio_controls)

        # ── Combine panes with splitter ─────────────────────────────────────────
        splitter = QtWidgets.QSplitter()
        lbox = QtWidgets.QWidget()
        lbox.setLayout(left_layout)
        rbox = QtWidgets.QWidget()
        rbox.setLayout(right_layout)
        splitter.addWidget(lbox)
        splitter.addWidget(rbox)

        main_layout.addWidget(splitter)  # Add splitter to main layout

        # ── System tray ─────────────────────────────────────────────────────────
        tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(APP_ICON_PATH), self)
        menu = QtWidgets.QMenu()
        menu.addAction("Show", self.showNormal)
        menu.addAction("Quit", QtWidgets.QApplication.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: self.showNormal() if reason == QtWidgets.QSystemTrayIcon.Trigger else None)
        tray.show()
        self.tray = tray

        # ── Connect net_thread signals ───────────────────────────────
        self.net.status.connect(self.add_status)
        self.net.userlist.connect(self.update_users)
        self.net.chatmsg.connect(self._handle_incoming_msg)

        # ── Restore mute states and connect buttons ─────────────────
        self.mic_muted = True
        self.spk_muted = False
        self._update_mute_buttons()

        self.mute_mic_btn.clicked.connect(self._toggle_mic_mute)
        self.mute_spk_btn.clicked.connect(self._toggle_spk_mute)

        # ── Signals for chat sending ────────────────────────────────
        send_btn.clicked.connect(self.send_chat)
        self.chat_edit.returnPressed.connect(send_btn.click)

        # ── Audio devices population and restoration ──────────────────────
        for d in sd.query_devices():
            if d['max_input_channels'] > 0:
                self.input_device.addItem(d['name'], d['name'])
            if d['max_output_channels'] > 0:
                self.output_device.addItem(d['name'], d['name'])

        # Restore saved audio-related settings or set defaults
        self.mic_slider.setValue(self.settings.get("mic_vol", 100))
        self.spk_slider.setValue(self.settings.get("spk_vol", 100))
        
        # Audio Mode loader
        saved_audio_mode = self.settings.get("audio_mode", "Push to Talk")
        index = self.audio_mode_combo.findText(saved_audio_mode)
        self.audio_engine.set_audio_mode(saved_audio_mode)
        if index != -1:
            self.audio_mode_combo.setCurrentIndex(index)
            
        # Restore saved input device selection, fallback gracefully
        saved_input = self.settings.get("input_device", None)
        if saved_input:
            idx = self.input_device.findData(saved_input)
            if idx != -1:
                self.input_device.setCurrentIndex(idx)

        # Restore saved output device selection, fallback gracefully
        saved_output = self.settings.get("output_device", None)
        if saved_output:
            idx = self.output_device.findData(saved_output)
            if idx != -1:
                self.output_device.setCurrentIndex(idx)

        # Connect signals to save changes immediately
        self.mic_slider.valueChanged.connect(self.save_mic_vol)
        self.spk_slider.valueChanged.connect(self.save_spk_vol)
        self.audio_mode_combo.currentTextChanged.connect(self._audio_mode_changed)
        self.input_device.currentIndexChanged.connect(self.save_input_device)
        self.output_device.currentIndexChanged.connect(self.save_output_device)

        self.update_server_label()

    # ── PTT ──────────────────────────────────────────────────
    def install_ptt_key_filter(self):
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
        self.installEventFilter(self)
        
    def start_global_ptt_listener(self):
        """
        Starts a daemon thread that listens for the configured PTT key
        even when the application is not focused.
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
                self.audio_engine.set_ptt_pressed(True)
                logging.debug("[GlobalPTT] key pressed")

        def on_release(key):
            if _matches_ptt(key) and self.ptt_pressed:
                self.ptt_pressed = False
                self.audio_engine.set_ptt_pressed(False)
                logging.debug("[GlobalPTT] key released")

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
        if event.type() == QtCore.QEvent.KeyPress:
            if event.key() == self.ptt_key and not self.ptt_pressed:
                self.ptt_pressed = True
                if self.audio_engine:
                    self.audio_engine.set_ptt_pressed(True)
                logging.debug(f"[GUI] PTT key pressed: {event.key()}")
                return True  # Stop further handling
        elif event.type() == QtCore.QEvent.KeyRelease:
            if event.key() == self.ptt_key and self.ptt_pressed:
                self.ptt_pressed = False
                if self.audio_engine:
                    self.audio_engine.set_ptt_pressed(False)
                logging.debug(f"[GUI] PTT key released: {event.key()}")
                return True  # Stop further handling
        return super().eventFilter(obj, event)



    # ── UI updates ───────────────────────────────────────────────────────
    def update_mic_gain(self, value):
        """
        Called when mic gain slider is changed.
        Saves to settings and applies immediately.
        """
        self.settings["mic_gain"] = value / 100.0  # Convert back to float
        self.settings.save()

    def update_mic_level(self, level: float):
        """
        Update the mic input level indicator.
        Args:
            level (float): Normalized input level (0.0 to 1.0)
        """
        value = max(0, min(int(level * 100), 100))
        self.mic_level_bar.setValue(value)

    def update_spk_level(self, level: float):
        """
        Update the speaker output level indicator.
        Args:
            level (float): Normalized output level (0.0 to 1.0)
        """
        value = max(0, min(int(level * 100), 100))
        self.spk_level_bar.setValue(value)

    
    def update_server_label(self):
        ip = self.settings.get("server_ip", "Unknown")
        port = self.settings.get("server_port", 12345)
        self.server_lbl.setText(f"🔗 Server: {ip}:{port}")

    def show_status(self, message: str, timeout: int = 5000):
        """
        Displays a status message in the status bar.
        Args:
            message (str): Message to show.
            timeout (int): Duration in ms. 0 = permanent.
        """
        self.status_bar.showMessage(message, timeout)
        
    def _audio_mode_changed(self, mode: str):
        self.settings["audio_mode"] = mode
        self.settings.save()
        if self.audio_engine:
            self.audio_engine.set_audio_mode(mode)


    def add_status(self, line: str):
        self.status.addItem(line)
        self.status.scrollToBottom()

    def update_users(self, users: list):
        self.users.clear()
        for u in users:
            name = u.get("name", "Unknown")
            if name == self.settings.get("display_name", ""):
                name += " (you)"
            logging.debug(u)
            # Status indicator: tx 🟢 (talking), 🔴 muted, default
            mic_icon = "💬" if u.get("tx") else "🔇" if u.get("muted") else " "
            spk_icon = "🔇" if u.get("spk_muted") else "🔊"

            item = QtWidgets.QTreeWidgetItem([spk_icon, mic_icon, name, u.get("ip", "")])
            self.users.addTopLevelItem(item)

    def add_chat(self, msg: dict):
        # Handle incoming messages from the server
        try:
            if msg.get("type") == "chat":
                # Safe handling of expected chat messages
                logging.debug(msg)
                self.chat_view.append(
                    f"<b>{msg.get('display_name', 'Unknown')}</b>: {msg.get('text', '')}"
                )
            else:
                # Unexpected message type — log it for debugging
                print(f"⚠️ Unexpected message type or format: {msg}")
        except Exception as e:
            # Catch unexpected structure or other runtime issues
            print(f"❌ Error processing message: {e} | Raw: {msg}")

    def _handle_incoming_msg(self, msg: dict):
        """
        Handles all incoming messages from the server.
        Distinguishes chat and audio packets.
        """
        try:
            if msg.get("type") == "audio":
                # Forward opus audio data (hex string) to audio engine for playback
                audio_data = msg.get("data", "")
                self.audio_engine.queue_incoming_audio(audio_data)
            else:
                # Otherwise, handle as chat or userlist messages via signals
                self.add_chat(msg)
        except Exception as e:
            logging.warning(f"Error handling incoming message: {e}")

    # ── chat send ────────────────────────────────────────────────────────
    def send_chat(self):
        text = self.chat_edit.text().strip()
        if not text:
            return

        now = QtCore.QTime.currentTime().msecsSinceStartOfDay()
        if now - self._last_sent_msecs < self.RATE_LIMIT_MS:
            self.add_status("[WARN] Please wait before sending another message.")
            return
        self._last_sent_msecs = now

        payload = {"display_name": self.settings.get("display_name", "Unknown"), "type": "chat", "text": text[:512]}  # Limit length

        # Echo locally
        self.add_chat({"type": "chat", "display_name": self.settings.get("display_name", "Unknown"), "text": text})

        # Queue message to network thread
        if self.net:
            self.net.queue_message(payload)

        self.chat_edit.clear()
        
    # ── send Status Update ─────────────────────────────────────────────────────
    def send_status_update(self):
        
        RATE_LIMIT_MS = 250  # 4 messages per second
        now = QtCore.QTime.currentTime().msecsSinceStartOfDay()
        if now - self._last_sent_msecs < RATE_LIMIT_MS:
            return
        self._last_sent_msecs = now

        payload = {"type": "status", "spk_muted": self.spk_muted, "muted": self.mic_muted, "display_name": self.settings.get("display_name")}
        logging.debug(payload)
        # Queue message to network thread
        if self.net:
            self.net.queue_message(payload)

    # ── Audio mute toggles and UI updates ──────────────────────────────
    def _toggle_mic_mute(self):
        self.mic_muted = not self.mic_muted
        self.audio_engine.set_mic_muted(self.mic_muted)
        self._update_mute_buttons()
        self.send_status_update()

    def _toggle_spk_mute(self):
        self.spk_muted = not self.spk_muted
        self.audio_engine.set_spk_muted(self.spk_muted)
        self._update_mute_buttons()
        self.send_status_update()


    def _update_mute_buttons(self):
        self.mute_mic_btn.setText("🔇 Mic" if self.mic_muted else "🎤 Mic")
        self.mute_spk_btn.setText("🔇 Audio" if self.spk_muted else "🔉 Audio")
        self.send_status_update()

    # ── Settings save methods for audio controls ─────────────────────────
    def save_mic_vol(self, value):
        self.settings["mic_vol"] = value
        self.settings.save()  # Save JSON to disk

    def save_spk_vol(self, value):
        self.settings["spk_vol"] = value
        self.settings.save()

    def _ptt_toggled(self, checked):
        self.settings["ptt"] = checked
        #self.settings.save()
        self.audio_engine.set_ptt_enabled(checked)

    def _vox_toggled(self, checked):
        self.settings["vox"] = checked
        #self.settings.save()
        self.audio_engine.set_vox_enabled(checked)

    def save_input_device(self, index):
        device = self.input_device.itemData(index)
        if device is not None:
            self.settings["input_device"] = device
            self.settings.save()
            # Restart audio engine to apply device changes
            self.audio_engine.stop()
            self.audio_engine.start()

    def save_output_device(self, index):
        device = self.output_device.itemData(index)
        if device is not None:
            self.settings["output_device"] = device
            self.settings.save()
            # Restart audio engine to apply device changes
            self.audio_engine.stop()
            self.audio_engine.start()

    # ── close/hide → tray ────────────────────────────────────────────────
    def closeEvent(self, ev: QtGui.QCloseEvent):
        self.hide()
        ev.ignore()

    # Ensure graceful cleanup on app exit
    def cleanup(self):
        if hasattr(self, "global_ptt_listener"):
            self.global_ptt_listener.stop()
        if getattr(self, "global_ptt_listener", None):
            self.global_ptt_listener.stop()
        self.audio_engine.stop()
        self.net.stop()
        self.net.wait()

