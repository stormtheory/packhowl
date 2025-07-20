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
from common import (APP_NAME, APP_ICON_PATH, PORT, CLIENT_IP, SSL_CA_PATH, CERTS_DIR,
                    DATA_DIR, ensure_data_dirs)
from settings import Settings
import socket

CLIENT_CERT_PATH = CERTS_DIR / f"{socket.gethostname()}.pem"

###############################################################################
# ─── ERROR CHECK ────────────────────────────────────────────────────────────
###############################################################################

if not CLIENT_CERT_PATH.exists():
    raise FileNotFoundError(f"Client cert not found: {CLIENT_CERT_PATH}")

###############################################################################
# ─── UI helpers ─────────────────────────────────────────────────────────────
###############################################################################

class FirstRunDialog(QtWidgets.QDialog):
    """Ask for Display Name & Server IP on first launch."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} – Setup")
        form = QtWidgets.QFormLayout(self)
        self.name_edit  = QtWidgets.QLineEdit()
        self.ip_edit    = QtWidgets.QLineEdit()
        form.addRow("Display Name:", self.name_edit)
        form.addRow("Server IP:", self.ip_edit)
        btn = QtWidgets.QPushButton("Save")
        btn.clicked.connect(self.accept)
        form.addRow(btn)

    @property
    def display_name(self): return self.name_edit.text().strip()
    @property
    def server_ip(self):   return self.ip_edit.text().strip()

###############################################################################
# ─── Networking Thread ─────────────────────────────────────────────────────
###############################################################################

class NetThread(QtCore.QThread):
    """Runs asyncio TLS client loop without blocking the Qt event loop."""
    status = QtCore.Signal(str)          # emits status/info lines
    userlist = QtCore.Signal(list)       # emits current userlist (list[dict])
    chatmsg  = QtCore.Signal(dict)       # emits chat messages

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._stop = False

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        while not self._stop:
            try:
                await self._connect_and_loop()
            except Exception as e:
                self.status.emit(f"[ERR] {e}")
                traceback.print_exc()
            # Auto-reconnect with back-off
            for i in range(5, 0, -1):
                if self._stop: return
                self.status.emit(f"[INFO] reconnect in {i}s")
                await asyncio.sleep(1)

    async def _connect_and_loop(self):
        ip = self.settings["server_ip"]
        self.status.emit(f"[INFO] connecting to {ip}:{PORT}")

        # Build TLS context (client side, mutual auth)
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.load_verify_locations(cafile=str(SSL_CA_PATH))
        ctx.load_cert_chain(certfile=str(CLIENT_CERT_PATH))  # your `client.pem`
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED

        # NOTE: load client cert/key here if you require client auth
        reader, writer = await asyncio.open_connection(
            host=ip, port=PORT, ssl=ctx, local_addr=(CLIENT_IP, 0)
        )
        self.status.emit("[OK] connected")

        # Send "hello" with display_name
        hello = {"type": "hello", "display_name": self.settings["display_name"]}
        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()

        # Main Rx loop
        while not reader.at_eof():
            line = await reader.readline()
            if not line:
                break
            msg = json.loads(line.decode())
            match msg.get("type"):
                case "userlist": self.userlist.emit(msg["users"])
                case "chat":     self.chatmsg.emit(msg)
                # TODO: audio payloads, control frames, etc.

        self.status.emit("[WARN] server closed connection")
        writer.close()
        await writer.wait_closed()

###############################################################################
# ─── Main Window ───────────────────────────────────────────────────────────
###############################################################################

class MainWindow(QtWidgets.QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QtGui.QIcon(APP_ICON_PATH))
        self.resize(640, 400)

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
        # (audio controls UI omitted for brevity – add later)

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

        self.update_server_label()

    # ── UI updates ───────────────────────────────────────────────────────
    def update_server_label(self):
        ip = self.settings["server_ip"]
        self.server_lbl.setText(f"🔗 Server: {ip}:{PORT}")

    def add_status(self, line: str):
        self.status.addItem(line)
        self.status.scrollToBottom()

    def update_users(self, users: list):
        self.users.clear()
        for u in users:
            item = QtWidgets.QTreeWidgetItem(
                ["🟢" if u.get("tx") else "🔴" if u.get("muted") else " ", 
                 u["name"], u["ip"]]
            )
            self.users.addTopLevelItem(item)

    def add_chat(self, msg: dict):
        self.chat_view.append(f"<b>{msg['name']}</b>: {msg['text']}")

    # ── chat send ────────────────────────────────────────────────────────
    def send_chat(self):
        text = self.chat_edit.text().strip()
        if not text: return
        payload = {"type": "chat", "text": text}
        # NetThread exposes writer? (simplified: place on queue)
        self.net.chatmsg.emit(payload)  # locally echo
        # TODO: queue outbound message to net thread
        self.chat_edit.clear()

    # ── close/hide → tray ────────────────────────────────────────────────
    def closeEvent(self, ev: QtGui.QCloseEvent):
        self.hide()
        ev.ignore()

###############################################################################
# ─── Entry-point ────────────────────────────────────────────────────────────
###############################################################################

def main():
    app = QtWidgets.QApplication(sys.argv)
    ensure_data_dirs()
    settings = Settings()

    # First-run wizard
    if not settings["display_name"] or not settings["server_ip"]:
        dlg = FirstRunDialog()
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            settings["display_name"] = dlg.display_name
            settings["server_ip"]    = dlg.server_ip
        else:
            sys.exit(0)

    win = MainWindow(settings)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
