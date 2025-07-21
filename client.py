#!/usr/bin/env python3.12
"""
Silent Link – minimal PySide6 client shell
• First-run wizard collects Display Name + Server IP
• System tray icon, hide-on-close behavior
• Background QThread handles TLS networking & auto-reconnect
• Placeholders for audio send/receive via sounddevice
"""

import json, sys, ssl, asyncio, traceback
from functools import partial
from typing import Optional

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import QTimer
from common import (APP_NAME, APP_ICON_PATH, CLIENT_IP, SSL_CA_PATH, CERTS_DIR,
                    DATA_DIR, ensure_data_dirs)
from common import SERVER_PORT as DEFAULT_SERVER_PORT
from settings import Settings
import socket
import argparse
import logging
import re
import ipaddress

parser = argparse.ArgumentParser()
parser.add_argument("-d", "--debug", action='store_true', help='Run GUI in debug mode')
args = parser.parse_args()

### SET LOGGING LEVEL
logger = logging.getLogger()
if args.debug:
    logger.setLevel(logging.DEBUG)     # INFO, DEBUG
else:
    logger.setLevel(logging.INFO)     # INFO, DEBUG

import os
# especially needed on Wayland (e.g. GNOME, KDE) where Qt apps may silently fail to show windows under certain themes or missing dependencies
os.environ["QT_QPA_PLATFORM"] = "xcb"

###############################################################################
# ─── ERROR CHECK ────────────────────────────────────────────────────────────
###############################################################################

CLIENT_CERT_PATH = CERTS_DIR / f"{socket.gethostname()}.pem"

if not CLIENT_CERT_PATH.exists():
    raise FileNotFoundError(f"Client cert not found: {CLIENT_CERT_PATH}")


###############################################################################
# ─── UI helpers ─────────────────────────────────────────────────────────────
###############################################################################

class FirstRunDialog(QtWidgets.QDialog):
    """Ask for Display Name, Server IP, and Port on first launch."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} – Setup")
        form = QtWidgets.QFormLayout(self)

        self.name_edit = QtWidgets.QLineEdit()
        self.ip_edit = QtWidgets.QLineEdit()
        self.port_edit = QtWidgets.QLineEdit(str(DEFAULT_SERVER_PORT))

        form.addRow("Display Name:", self.name_edit)
        form.addRow("Server IP:", self.ip_edit)
        form.addRow("Server Port:", self.port_edit)

        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.clicked.connect(self.accept)
        self.save_btn.setEnabled(False)
        form.addRow(self.save_btn)

        # Real-time validation
        self.name_edit.textChanged.connect(self.validate)
        self.ip_edit.textChanged.connect(self.validate)
        self.port_edit.textChanged.connect(self.validate)

    def validate(self):
        name = self.name_edit.text().strip()
        ip = self.ip_edit.text().strip()
        port = self.port_edit.text().strip()

        # Name: 1–32 chars, letters, numbers, spaces only
        valid_name = 1 <= len(name) <= 32 and all(c.isalnum() or c.isspace() for c in name)

        # IP: use ipaddress for strict validation fallback
        try:
            ipaddress.ip_address(ip)
            valid_ip = True
        except ValueError:
            valid_ip = False

        # Port: integer 1–65535
        try:
            p = int(port)
            valid_port = 1 <= p <= 65535
        except ValueError:
            valid_port = False

        is_valid = valid_name and valid_ip and valid_port

        self.save_btn.setEnabled(is_valid)

    @property
    def display_name(self):
        return self.name_edit.text().strip()

    @property
    def server_ip(self):
        return self.ip_edit.text().strip()

    @property
    def server_port(self) -> Optional[int]:
        # Validate port: integer between 1 and 65535
        try:
            p = int(self.port_edit.text().strip())
            if 1 <= p <= 65535:
                return p
        except ValueError:
            pass
        return None


###############################################################################
# ─── Networking Thread ─────────────────────────────────────────────────────
###############################################################################

class NetThread(QtCore.QThread):
    """Runs asyncio TLS client loop without blocking the Qt event loop."""
    status = QtCore.Signal(str)
    userlist = QtCore.Signal(list)
    chatmsg  = QtCore.Signal(dict)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._stop = False
        global SERVER_PORT
        SERVER_PORT = self.settings["server_port"]
        self._loop = None  # Store event loop for coroutine submission
        self.outbound_queue = asyncio.Queue()  # Outbound message queue for thread-safe sending

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        while not self._stop:
            try:
                await self._connect_and_loop()
            except Exception as e:
                self.status.emit(f"[ERR] {e}")
                traceback.print_exc()
            # Auto-reconnect with back-off
            for i in range(5, 0, -1):
                if self._stop:
                    return
                self.status.emit(f"[INFO] reconnect in {i}s")
                await asyncio.sleep(1)

    async def _connect_and_loop(self):
        ip = self.settings["server_ip"]
        self.status.emit(f"[INFO] connecting to {ip}:{SERVER_PORT}")

        # Build TLS context (client side, mutual auth)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_2  # TLS 1.3 only
        ctx.load_verify_locations(cafile=str(SSL_CA_PATH))
        ctx.load_cert_chain(certfile=str(CLIENT_CERT_PATH))  # your `client.pem`
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED

        # NOTE: load client cert/key here if you require client auth
        reader, writer = await asyncio.open_connection(
            host=ip, port=SERVER_PORT, ssl=ctx, local_addr=(CLIENT_IP, 0)
        )
        self.status.emit("[OK] connected")

        # Send "hello" with display_name
        hello = {
                "type": "init",
                "name": self.settings["display_name"],
                "ip": socket.gethostbyname(socket.gethostname())  # add IP if needed
            }

        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()

        send_task = asyncio.create_task(self._send_outgoing(writer))  # background sender loop

        # main RX loop
        while not reader.at_eof() and not self._stop:
            line = await reader.readline()
            if not line:
                break
            msg = json.loads(line.decode())
            match msg.get("type"):
                case "userlist": self.userlist.emit(msg["users"])
                case "chat":     self.chatmsg.emit(msg)
                # TODO: audio payloads, control frames, etc.

        send_task.cancel()  # cleanup on disconnect

        self.status.emit("[WARN] server closed connection")
        try:
            writer.write_eof()
        except Exception:
            pass
        try:
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception as e:
            print(f"[WARN] Close error: {e}")

    async def _send_outgoing(self, writer):
        """Drains outbound_queue and writes to TLS socket."""
        try:
            while not self._stop:
                msg = await self.outbound_queue.get()
                writer.write((json.dumps(msg) + "\n").encode())
                await writer.drain()
                self.outbound_queue.task_done()
        except asyncio.CancelledError:
            pass

    def queue_message(self, msg: dict):
        """Queue message from GUI to network thread asynchronously."""
        if self._loop and not self._stop:
            asyncio.run_coroutine_threadsafe(
                self.outbound_queue.put(msg), self._loop
            )

    def stop(self):
        """Graceful shutdown signal for the network thread."""
        self._stop = True


###############################################################################
# ─── Main Window ───────────────────────────────────────────────────────────
###############################################################################

class MainWindow(QtWidgets.QWidget):
    RATE_LIMIT_MS = 1000  # 1 message per second rate limit
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
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
        self.mic_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.mic_slider.setRange(0, 100)
        self.mic_slider.setValue(100)
        self.input_device = QtWidgets.QComboBox()
        self.ptt_checkbox = QtWidgets.QCheckBox("Push to Talk")

        self.mute_spk_btn = QtWidgets.QPushButton("Mute Audio")
        self.spk_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
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

        # ── Combine panes ────────────────────────────────────────────────
        splitter = QtWidgets.QSplitter()
        lbox = QtWidgets.QWidget(); lbox.setLayout(left_layout)
        rbox = QtWidgets.QWidget(); rbox.setLayout(right_layout)
        splitter.addWidget(lbox)
        splitter.addWidget(rbox)
        h = QtWidgets.QHBoxLayout(self)
        h.addWidget(splitter)

        # ── System tray ──────────────────────────────────────────────────
        tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(APP_ICON_PATH), self)
        menu = QtWidgets.QMenu()
        menu.addAction("Show", self.showNormal)
        menu.addAction("Quit", QtWidgets.QApplication.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda _=None: self.showNormal())
        tray.show()
        self.tray = tray

        # ── Net thread ───────────────────────────────────────────────────
        self.net = NetThread(settings)
        self.net.status.connect(self.add_status)
        self.net.userlist.connect(self.update_users)
        self.net.chatmsg.connect(self.add_chat)
        self.net.start()

        # ── Signals ──────────────────────────────────────────────────────
        send_btn.clicked.connect(self.send_chat)
        self.chat_edit.returnPressed.connect(send_btn.click)

        # ── Audio devices population and restoration ──────────────────────
        import sounddevice as sd
        for d in sd.query_devices():
            if d['max_input_channels'] > 0:
                self.input_device.addItem(d['name'], d['name'])
            if d['max_output_channels'] > 0:
                self.output_device.addItem(d['name'], d['name'])

        # Restore saved audio-related settings or set defaults
        self.mic_slider.setValue(self.settings.get("mic_vol", 100))  # Mic volume slider
        self.spk_slider.setValue(self.settings.get("spk_vol", 100))  # Speaker volume slider
        self.ptt_checkbox.setChecked(self.settings.get("ptt", False))  # Push-to-talk
        self.vox_checkbox.setChecked(self.settings.get("vox", False))  # Voice activated

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
        self.ptt_checkbox.toggled.connect(self.save_ptt)
        self.vox_checkbox.toggled.connect(self.save_vox)
        self.input_device.currentIndexChanged.connect(self.save_input_device)
        self.output_device.currentIndexChanged.connect(self.save_output_device)

        self.update_server_label()

    # ── UI updates ───────────────────────────────────────────────────────
    def update_server_label(self):
        ip = self.settings["server_ip"]
        self.server_lbl.setText(f"🔗 Server: {ip}:{SERVER_PORT}")

    def add_status(self, line: str):
        self.status.addItem(line)
        self.status.scrollToBottom()

    def update_users(self, users: list):
        self.users.clear()
        for u in users:
            name = u["name"]
            if name == self.settings["display_name"]:
                name += " (you)"

            # Status indicator: tx (talking), muted, default
            status_icon = "🟢" if u.get("tx") else "🔴" if u.get("muted") else " "

            item = QtWidgets.QTreeWidgetItem([status_icon, name, u["ip"]])
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
        self.add_chat({"type": "chat", "name": self.settings["display_name"], "text": text})

        # Queue message to network thread
        if self.net:
            self.net.queue_message(payload)

        self.chat_edit.clear()

    # ── Settings save methods for audio controls ─────────────────────────
    def save_mic_vol(self, value):
        self.settings["mic_vol"] = value
        self.settings.save()  # Save JSON to disk

    def save_spk_vol(self, value):
        self.settings["spk_vol"] = value
        self.settings.save()

    def save_ptt(self, checked):
        self.settings["ptt"] = checked
        self.settings.save()

    def save_vox(self, checked):
        self.settings["vox"] = checked
        self.settings.save()

    def save_input_device(self, index):
        device = self.input_device.itemData(index)
        if device is not None:
            self.settings["input_device"] = device
            self.settings.save()

    def save_output_device(self, index):
        device = self.output_device.itemData(index)
        if device is not None:
            self.settings["output_device"] = device
            self.settings.save()

    # ── close/hide → tray ────────────────────────────────────────────────
    def closeEvent(self, ev: QtGui.QCloseEvent):
        self.hide()
        ev.ignore()


###############################################################################
# ─── Entry-point ────────────────────────────────────────────────────────────
###############################################################################

def main():
    app = QtWidgets.QApplication(sys.argv)

    ###############################################################################
    # ─── ERROR CHECKING / LOAD JSON / FIRST TIME RUN ─────────────────────────────
    ###############################################################################
    ensure_data_dirs()
    settings = Settings()
    logging.debug(json.dumps(settings.data, indent=2))

    # First-run wizard
    if not settings["display_name"] or not settings["server_ip"]:
        dlg = FirstRunDialog()
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            settings["display_name"] = dlg.display_name
            settings["server_ip"]    = dlg.server_ip
            settings["server_port"]  = dlg.server_port
            settings.save()  # Save immediately after first run setup
        else:
            sys.exit(0)

    win = MainWindow(settings)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
