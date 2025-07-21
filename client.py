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
from PySide6.QtCore import QTimer, Qt
from common import (APP_NAME, APP_ICON_PATH, CLIENT_IP, SSL_CA_PATH, CERTS_DIR,
                    DATA_DIR, ensure_data_dirs)
from common import SERVER_PORT as DEFAULT_SERVER_PORT
from settings import Settings
import socket
import argparse
import logging
import re
import ipaddress
import sounddevice as sd
import numpy as np

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
    IP_REGEX = re.compile(r"""
        ^
        (?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)(?:\.|$)){4}  # IPv4
        |
        (
            # IPv6 simplified pattern
            ([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}
        )
        $
    """, re.VERBOSE)

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
    audio_frame = QtCore.Signal(bytes)  # new signal for incoming audio

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._stop = False
        global SERVER_PORT
        SERVER_PORT = self.settings["server_port"]
        self._loop = None  
        self.outbound_queue = asyncio.Queue()  
        self.audio_queue = asyncio.Queue()     # new audio queue for sending frames

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
        ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_2
        ctx.load_verify_locations(cafile=str(SSL_CA_PATH))
        ctx.load_cert_chain(certfile=str(CLIENT_CERT_PATH))
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED

        # NOTE: load client cert/key here if you require client auth
        reader, writer = await asyncio.open_connection(
            host=ip, port=SERVER_PORT, ssl=ctx, local_addr=(CLIENT_IP, 0)
        )
        self.status.emit("[OK] connected")
        hello = {"type":"init","name":self.settings["display_name"],"ip":socket.gethostbyname(socket.gethostname())}
        writer.write((json.dumps(hello)+"\n").encode())
        await writer.drain()

        send_task = asyncio.create_task(self._send_outgoing(writer))
        audio_send_task = asyncio.create_task(self._send_audio(writer))

        # main RX loop
        while not reader.at_eof() and not self._stop:
            line = await reader.readline()
            if not line:
                break
            msg = json.loads(line.decode())
            if msg.get("type")=="userlist":
                self.userlist.emit(msg["users"])
            elif msg.get("type")=="chat":
                self.chatmsg.emit(msg)
            elif msg.get("type")=="audio":
                self.audio_frame.emit(msg["frame"])  # incoming audio frames

        send_task.cancel()
        audio_send_task.cancel()
        self.status.emit("[WARN] server closed connection")
        try:
            writer.write_eof()
        except:
            pass
        try:
            await writer.drain(); writer.close(); await writer.wait_closed()
        except Exception as e:
            print(f"[WARN] Close error: {e}")

    async def _send_outgoing(self, writer):
        """Drains outbound_queue and writes to TLS socket."""
        try:
            while not self._stop:
                msg = await self.outbound_queue.get()
                writer.write((json.dumps(msg)+"\n").encode())
                await writer.drain()
                self.outbound_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _send_audio(self, writer):
        try:
            while not self._stop:
                frame = await self.audio_queue.get()
                msg = {"type":"audio","frame":frame.hex()}
                writer.write((json.dumps(msg)+"\n").encode())
                await writer.drain()
                self.audio_queue.task_done()
        except asyncio.CancelledError:
            pass

    def queue_message(self, msg: dict):
        if self._loop and not self._stop:
            asyncio.run_coroutine_threadsafe(self.outbound_queue.put(msg), self._loop)

    def queue_audio(self, frame: bytes):
        if self._loop and not self._stop:
            asyncio.run_coroutine_threadsafe(self.audio_queue.put(frame), self._loop)

    def stop(self):
        """Graceful shutdown signal for the network thread."""
        self._stop = True

###############################################################################
# ─── Audio Threads ──────────────────────────────────────────────────────────
###############################################################################

class AudioRecordThread(QtCore.QThread):
    frame = QtCore.Signal(bytes)
    def __init__(self, device=None, vox=False, ptt=False, mute=False, rms_threshold=500):
        super().__init__()
        self.device = device
        self.vox = vox
        self.ptt = ptt
        self.mute = mute
        self.rms_threshold = rms_threshold
        self._stream = None

    def run(self):
        self._stream = sd.InputStream(
            device=self.device, channels=1, dtype='int16',
            callback=self._cb, samplerate=48000
        )
        self._stream.start()
        sd.sleep(999999999)

    def _cb(self, indata, frames, time, status):
        if self.mute:
            return
        rms = np.sqrt(np.mean(indata**2))
        active = self.ptt or (self.vox and rms > self.rms_threshold)
        if active:
            self.frame.emit(indata.tobytes())

class AudioPlayThread(QtCore.QThread):
    def __init__(self, device=None, gain=1.0):
        super().__init__()
        self.device = device
        self.gain = gain
        self.q = asyncio.Queue()

    def run(self):
        with sd.OutputStream(device=self.device, channels=1,
                             dtype='int16', samplerate=48000,
                             callback=self._play_cb):
            sd.sleep(999999999)

    def _play_cb(self, outdata, frames, time, status):
        try:
            data = self.q.get_nowait()
            arr = np.frombuffer(data, dtype='int16') * self.gain
            outdata[:] = arr.reshape(outdata.shape)
        except:
            outdata.fill(0)

    def enqueue(self, data: bytes):
        try:
            self.q.put_nowait(data)
        except:
            pass

###############################################################################
# ─── Main Window ───────────────────────────────────────────────────────────
###############################################################################

class MainWindow(QtWidgets.QWidget):
    RATE_LIMIT_MS = 1000
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QtGui.QIcon(APP_ICON_PATH))
        self.resize(800, 500)
        self._last_sent_msecs = 0

        # Audio controls UI
        self.mic_mute_btn = QtWidgets.QPushButton("Mute Mic")
        self.out_mute_btn = QtWidgets.QPushButton("Mute Audio")
        self.input_dev = QtWidgets.QComboBox()
        self.output_dev = QtWidgets.QComboBox()
        self.mic_gain = QtWidgets.QSlider(Qt.Horizontal)
        self.out_gain = QtWidgets.QSlider(Qt.Horizontal)
        self.ptt_checkbox = QtWidgets.QCheckBox("Push‑to‑talk")
        self.vox_checkbox = QtWidgets.QCheckBox("Voice‑activated")

        # populate device lists
        for dev in sd.query_devices():
            if dev['max_input_channels']>0:
                self.input_dev.addItem(dev['name'], dev['name'])
            if dev['max_output_channels']>0:
                self.output_dev.addItem(dev['name'], dev['name'])
        self.mic_gain.setRange(0,100); self.mic_gain.setValue(100)
        self.out_gain.setRange(0,100); self.out_gain.setValue(100)

        # Left pane
        left_layout = QtWidgets.QVBoxLayout()
        self.server_lbl = QtWidgets.QLabel()
        self.users = QtWidgets.QTreeWidget()
        self.users.setHeaderLabels(["🔉", "Name", "IP", "Muted"])
        self.status = QtWidgets.QListWidget()
        left_layout.addWidget(self.server_lbl)
        left_layout.addWidget(self.users,3)
        left_layout.addWidget(QtWidgets.QLabel("Status / Errors"))
        left_layout.addWidget(self.status,1)

        # Chat pane
        right_layout = QtWidgets.QVBoxLayout()
        self.chat_view = QtWidgets.QTextBrowser()
        bottom = QtWidgets.QHBoxLayout()
        self.chat_edit = QtWidgets.QLineEdit()
        send_btn = QtWidgets.QPushButton("Send")
        bottom.addWidget(self.chat_edit,1); bottom.addWidget(send_btn)
        right_layout.addWidget(self.chat_view,3); right_layout.addLayout(bottom)

        # Audio layout
        audio_layout = QtWidgets.QHBoxLayout()
        audio_layout.addWidget(self.mic_mute_btn)
        audio_layout.addWidget(self.out_mute_btn)
        audio_layout.addWidget(QtWidgets.QLabel("In:")); audio_layout.addWidget(self.input_dev)
        audio_layout.addWidget(QtWidgets.QLabel("Out:")); audio_layout.addWidget(self.output_dev)
        audio_layout.addWidget(QtWidgets.QLabel("Mic Vol:")); audio_layout.addWidget(self.mic_gain)
        audio_layout.addWidget(QtWidgets.QLabel("Spk Vol:")); audio_layout.addWidget(self.out_gain)
        audio_layout.addWidget(self.ptt_checkbox)
        audio_layout.addWidget(self.vox_checkbox)
        right_layout.addLayout(audio_layout)

        # Combine panes
        splitter = QtWidgets.QSplitter()
        lbox = QtWidgets.QWidget(); lbox.setLayout(left_layout)
        rbox = QtWidgets.QWidget(); rbox.setLayout(right_layout)
        splitter.addWidget(lbox); splitter.addWidget(rbox)
        main_layout = QtWidgets.QHBoxLayout(self); main_layout.addWidget(splitter)

        # System tray
        tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(APP_ICON_PATH), self)
        menu = QtWidgets.QMenu(); menu.addAction("Show", self.showNormal); menu.addAction("Quit", QtWidgets.QApplication.quit)
        tray.setContextMenu(menu); tray.activated.connect(lambda _:self.showNormal()); tray.show()
        self.tray = tray

        # Threads
        self.net = NetThread(settings)
        self.net.status.connect(self.add_status); self.net.userlist.connect(self.update_users)
        self.net.chatmsg.connect(self.add_chat); self.net.audio_frame.connect(self.on_audio_frame)
        self.net.start()

        self.record = AudioRecordThread(device=self.input_dev.currentData(), vox=False, ptt=False)
        self.record.frame.connect(self.net.queue_audio)
        self.record.ptt = False
        self.record.vox = False
        self.record.mute = False
        self.record.start()

        self.player = AudioPlayThread(device=self.output_dev.currentData())
        self.player.start()

        send_btn.clicked.connect(self.send_chat)
        self.chat_edit.returnPressed.connect(send_btn.click)

        # Control callbacks
        self.mic_mute_btn.clicked.connect(lambda: setattr(self.record, 'mute', not self.record.mute))
        self.out_mute_btn.clicked.connect(lambda: setattr(self.player, 'gain', 0 if self.player.gain>0 else self.out_gain.value()/100))
        self.mic_gain.valueChanged.connect(lambda v: setattr(self.record, 'mute', False))
        self.out_gain.valueChanged.connect(lambda v: setattr(self.player, 'gain', v/100))
        self.input_dev.currentIndexChanged.connect(lambda i: setattr(self.record, 'device', self.input_dev.itemData(i)))
        self.output_dev.currentIndexChanged.connect(lambda i: setattr(self.player, 'device', self.output_dev.itemData(i)))
        self.ptt_checkbox.toggled.connect(lambda v: setattr(self.record, 'ptt', v))
        self.vox_checkbox.toggled.connect(lambda v: setattr(self.record, 'vox', v))

        self.update_server_label()

    # ── UI updates ───────────────────────────────────────────────────────
    def update_server_label(self):
        ip = self.settings["server_ip"]
        self.server_lbl.setText(f"🔗 Server: {ip}:{SERVER_PORT}")

    def add_status(self, line: str):
        self.status.addItem(line); self.status.scrollToBottom()

    def update_users(self, users: list):
        self.users.clear()
        for u in users:
            name = u["name"]
            
            if name==self.settings["display_name"]: name+=" (you)"
            status_icon = "🟢" if u.get("tx") else "🔴" if u.get("muted") else " "
            item = QtWidgets.QTreeWidgetItem([status_icon, name, u["ip"], "Yes" if u.get("muted") else "No"])
            self.users.addTopLevelItem(item)

    def add_chat(self, msg: dict):
        # Handle incoming messages from the server
        try:
            if msg.get("type")=="chat":
                self.chat_view.append(f"<b>{msg.get('name')}</b>: {msg.get('text')}")
            else:
                print(f"⚠️ Unexpected message type or format: {msg}")
        except Exception as e:
            print(f"❌ Error processing message: {e} | Raw: {msg}")

    # ── chat send ────────────────────────────────────────────────────────
    def on_audio_frame(self, data_hex: str):
        data = bytes.fromhex(data_hex)
        self.player.enqueue(data)

    def send_chat(self):
        text = self.chat_edit.text().strip()
        if not text: return
        now = QtCore.QTime.currentTime().msecsSinceStartOfDay()
        if now - self._last_sent_msecs < self.RATE_LIMIT_MS:
            self.add_status("[WARN] Please wait before sending another message.")
            return
        self._last_sent_msecs = now
        payload={"type":"chat","text":text[:512]}
        self.add_chat({"type":"chat","name":self.settings["display_name"],"text":text})
        if self.net: self.net.queue_message(payload)
        self.chat_edit.clear()

    # ── close/hide → tray ────────────────────────────────────────────────
    def closeEvent(self, ev: QtGui.QCloseEvent):
        self.hide(); ev.ignore()

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
        dlg=FirstRunDialog()
        if dlg.exec()==QtWidgets.QDialog.Accepted:
            settings["display_name"]=dlg.display_name
            settings["server_ip"]=dlg.server_ip
            settings["server_port"]=dlg.server_port
        else:
            sys.exit(0)
    win=MainWindow(settings); win.show(); sys.exit(app.exec())

if __name__=="__main__":
    main()
