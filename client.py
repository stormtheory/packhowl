#!/usr/bin/env python3.12
# client.py
from PySide6 import QtWidgets
import sys
import asyncio
import json
from typing import Optional

import ipaddress
from client.settings import Settings
from client.network import NetworkThread
from client.audio_engine import AudioEngine
from client.gui import MainWindow

from common import (APP_NAME, APP_ICON_PATH, CLIENT_IP, SSL_CA_PATH, CERTS_DIR,
                    DATA_DIR, ensure_data_dirs)
from common import SERVER_PORT as DEFAULT_SERVER_PORT
import socket
import argparse
import logging

parser = argparse.ArgumentParser()
parser.add_argument("-d", "--debug", action='store_true', help='Run GUI in debug mode')
args = parser.parse_args()

### SET LOGGING LEVEL
logger = logging.getLogger()
if args.debug:
    logger.setLevel(logging.DEBUG)     # INFO, DEBUG
else:
    logger.setLevel(logging.INFO)     # INFO, DEBUG



"""
Main client script for Silent Link
• Starts Qt application with PySide6
• Loads settings
• Initializes network thread, audio engine, and GUI
• Handles graceful shutdown
"""



import os
# especially needed on Wayland (e.g. GNOME, KDE) where Qt apps may silently fail to show windows under certain themes or missing dependencies
os.environ["QT_QPA_PLATFORM"] = "xcb"


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

    # Initialize network thread for communication with server
    net_thread = NetworkThread(settings)
    net_thread.start()

    # Initialize audio engine for mic and speaker handling
    audio_engine = AudioEngine(settings, net_thread, status_callback=None)  # Will set callback later

    # Create main GUI window
    window = MainWindow(settings, net_thread, audio_engine)

    # Assign the status callback after window is created
    audio_engine.status_callback = window.show_status

    # PTT Key filter    
    window.install_ptt_key_filter()

    window.show()

    # Run Qt event loop
    exit_code = app.exec()

    # On app exit, ensure proper cleanup
    window.cleanup()
    net_thread.stop()
    net_thread.wait()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

