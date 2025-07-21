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

from common import (APP_NAME, APP_ICON_PATH, CLIENT_IP, SSL_CA_PATH, CERTS_DIR,
                    DATA_DIR, ensure_data_dirs)
from common import SERVER_PORT as DEFAULT_SERVER_PORT


class MainWindow(QtWidgets.QMainWindow):
    RATE_LIMIT_MS = 1000  # 1 message per second rate limit

    def __init__(self, settings, net_thread, audio_engine):
        super().__init__()
        self.settings = settings
        self.net = net_thread
        self.audio_engine = audio_engine

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
        self.users.setHeaderLabels(["🟢", "Name", "IP"])
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
        audio_controls = QtWidgets.QGroupBox("Audio Controls")
        audio_layout = QtWidgets.QGridLayout()

        self.mute_mic_btn = QtWidgets.QPushButton("Mute Mic")
        self.mic_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.mic_slider.setRange(0, 100)
        self.mic_slider.setValue(100)
        self.input_device = QtWidgets.QComboBox()
        self.ptt_checkbox = QtWidgets.QCheckBox("Push to Talk")

        self.mute_spk_btn = QtWidgets.QPushButton("Mute Audio")
        self.spk_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.spk_slider.setRange(0, 100)
        self.spk_slider.setValue(100)
        self.output_device = QtWidgets.QComboBox()
        self.vox_checkbox = QtWidgets.QCheckBox("Voice Activated")

        # ── Top row ────────────────────────────────────────────────────────────
        audio_layout.addWidget(self.mute_mic_btn, 0, 0)
        audio_layout.addWidget(self.mic_slider, 0, 1)
        audio_layout.addWidget(self.input_device, 0, 2)
        audio_layout.addWidget(self.ptt_checkbox, 0, 3)

        # ── Bottom row ─────────────────────────────────────────────────────────
        audio_layout.addWidget(self.mute_spk_btn, 1, 0)
        audio_layout.addWidget(self.spk_slider, 1, 1)
        audio_layout.addWidget(self.output_device, 1, 2)
        audio_layout.addWidget(self.vox_checkbox, 1, 3)

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
        self.mic_muted = False
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
        self.ptt_checkbox.setChecked(self.settings.get("ptt", False))
        self.vox_checkbox.setChecked(self.settings.get("vox", False))

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
        self.ptt_checkbox.toggled.connect(self._ptt_toggled)
        self.vox_checkbox.toggled.connect(self._vox_toggled)
        self.input_device.currentIndexChanged.connect(self.save_input_device)
        self.output_device.currentIndexChanged.connect(self.save_output_device)

        self.update_server_label()

    # ── UI updates ───────────────────────────────────────────────────────
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

    def add_status(self, line: str):
        self.status.addItem(line)
        self.status.scrollToBottom()

    def update_users(self, users: list):
        self.users.clear()
        for u in users:
            name = u.get("name", "Unknown")
            if name == self.settings.get("display_name", ""):
                name += " (you)"

            # Status indicator: tx (talking), muted, default
            status_icon = "🟢" if u.get("tx") else "🔴" if u.get("muted") else " "

            item = QtWidgets.QTreeWidgetItem([status_icon, name, u.get("ip", "")])
            self.users.addTopLevelItem(item)

    def add_chat(self, msg: dict):
        # Handle incoming messages from the server
        try:
            if msg.get("type") == "chat":
                # Safe handling of expected chat messages
                self.chat_view.append(
                    f"<b>{msg.get('name', 'Unknown')}</b>: {msg.get('text', '')}"
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

        payload = {"type": "chat", "text": text[:512]}  # Limit length

        # Echo locally
        self.add_chat({"type": "chat", "name": self.settings.get("display_name", "Unknown"), "text": text})

        # Queue message to network thread
        if self.net:
            self.net.queue_message(payload)

        self.chat_edit.clear()

    # ── Audio mute toggles and UI updates ──────────────────────────────
    def _toggle_mic_mute(self):
        self.mic_muted = not self.mic_muted
        self.audio_engine.set_mic_muted(self.mic_muted)
        self._update_mute_buttons()

    def _toggle_spk_mute(self):
        self.spk_muted = not self.spk_muted
        self.audio_engine.set_spk_muted(self.spk_muted)
        self._update_mute_buttons()

    def _update_mute_buttons(self):
        self.mute_mic_btn.setText("Unmute Mic" if self.mic_muted else "Mute Mic")
        self.mute_spk_btn.setText("Unmute Audio" if self.spk_muted else "Mute Audio")

    # ── Settings save methods for audio controls ─────────────────────────
    def save_mic_vol(self, value):
        self.settings["mic_vol"] = value
        self.settings.save()  # Save JSON to disk

    def save_spk_vol(self, value):
        self.settings["spk_vol"] = value
        self.settings.save()

    def _ptt_toggled(self, checked):
        self.settings["ptt"] = checked
        self.settings.save()
        self.audio_engine.set_ptt_enabled(checked)

    def _vox_toggled(self, checked):
        self.settings["vox"] = checked
        self.settings.save()
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
        self.audio_engine.stop()
        self.net.stop()
        self.net.wait()
