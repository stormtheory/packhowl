#!/usr/bin/env python3.12
# client.py
from PySide6 import QtWidgets
import sys
import json
from typing import Optional

import ipaddress
from client.settings import Settings
from client.network import NetworkThread
from client.audio_engine import AudioEngine
from client.gui import MainWindow

from config import (APP_NAME, APP_ICON_PATH, CLIENT_IP, SSL_CA_PATH, CERTS_DIR,
                    DATA_DIR, ensure_data_dirs)
from config import SERVER_PORT as DEFAULT_SERVER_PORT
import socket
import argparse
import logging
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("-d", "--debug", action='store_true', help='Run GUI in debug mode')
parser.add_argument("-l", "--loopback", action="store_true", help="Enable mic-to-speaker loopback at startup")
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

###############################################################################
# ─── ERROR CHECKING ─────────────────────────────────────────────────────────
###############################################################################
PROMPT_EXIT = False

if DATA_DIR.is_dir():
    pass
else:
    PROMPT_EXIT = True
    print(f"\n Directory missing: {DATA_DIR}\n ")

# Check if file exists
if CERTS_DIR.is_dir():
    pass
else:
    PROMPT_EXIT = True
    print(f"\n Directory missing: {CERTS_DIR}\n ")
    
if SSL_CA_PATH.is_file():
    pass
else:
    PROMPT_EXIT = True
    print(f"\n \n File missing: {SSL_CA_PATH}")
    print(f"\n This file: {SSL_CA_PATH} \n needs to be generated at the \n server and shared with this client in {CERTS_DIR} \n")

hostname = socket.gethostname()
HOST_PEM_PATH = CERTS_DIR / f"{hostname}.pem"
if HOST_PEM_PATH.is_file():
    pass
else:
    PROMPT_EXIT = True
    print(f"\n \n File missing: {CERTS_DIR}/{hostname}.pem")
    print(f"\n This file: {CERTS_DIR}/{hostname}.pem \n needs to be generated at the \n server and shared with this client in {CERTS_DIR} \n")
    
if PROMPT_EXIT is True:
    exit()



import os
# especially needed on Wayland (e.g. GNOME, KDE) where Qt apps may silently fail to show windows under certain themes or missing dependencies
os.environ["QT_QPA_PLATFORM"] = "xcb"


###############################################################################
# ─── UI helpers ─────────────────────────────────────────────────────────────
###############################################################################

class FirstRunDialog(QtWidgets.QDialog):
    """Ask for Display Name, Server IP, and Port on first launch."""
    def __init__(self):
        PTT_KEY_OPTIONS = [
            "leftalt",
            "rightalt",
            "alt",
            "leftctrl",
            "rightctrl",
            "ctrl",
            "leftshift",
            "rightshift",
            "shift",
            "space",
            "f1",
            "f2"
        ]
        MIC_STARTUP_OPTIONS = [
            "mute",
            "on"
        ]
        SPK_STARTUP_OPTIONS = [
            "on",
            "mute"
        ]
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} – Setup")
        form = QtWidgets.QFormLayout(self)

        self.name_edit = QtWidgets.QLineEdit()
        self.ip_edit = QtWidgets.QLineEdit()
        self.port_edit = QtWidgets.QLineEdit(str(DEFAULT_SERVER_PORT))
        self.ptt_combo = QtWidgets.QComboBox()
        self.ptt_combo.addItems(PTT_KEY_OPTIONS)
        self.mic_startup_combo = QtWidgets.QComboBox()
        self.mic_startup_combo.addItems(MIC_STARTUP_OPTIONS)
        self.spk_startup_combo = QtWidgets.QComboBox()
        self.spk_startup_combo.addItems(SPK_STARTUP_OPTIONS)

        form.addRow("Display Name:", self.name_edit)
        form.addRow("Server IP:", self.ip_edit)
        form.addRow("Server Port:", self.port_edit)
        form.addRow("Push-To-Talk Key:", self.ptt_combo)
        form.addRow("Mic at App Startup:", self.mic_startup_combo)
        form.addRow("Speaker at App Startup:", self.spk_startup_combo)

        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.clicked.connect(self.accept)
        self.save_btn.setEnabled(False)
        form.addRow(self.save_btn)

        # Real-time validation
        self.name_edit.textChanged.connect(self.validate)
        self.ip_edit.textChanged.connect(self.validate)
        self.port_edit.textChanged.connect(self.validate)
        
        self.validate()

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
    
    @property
    def ptt_key(self) -> str:
        return self.ptt_combo.currentText()
    
    @property
    def mic_startup(self) -> bool:
        return self.mic_startup_combo.currentText().lower() == "on" # True value

    @property
    def spk_startup(self) -> bool:
        return self.spk_startup_combo.currentText().lower() == "on" # True value


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
            settings["ptt_key"]      = dlg.ptt_key
            settings["mic_startup"]  = dlg.mic_startup
            settings["spk_startup"]  = dlg.spk_startup
            settings.save()  # Save immediately after first run setup
        else:
            sys.exit(0)
            
    print("Using input device:", settings.get("input_device"))
    print("Using output device:", settings.get("output_device"))

    #import sounddevice as sd
    #for i, dev in enumerate(sd.query_devices()):
        #print(f"{i}: {dev['name']} (input channels: {dev['max_input_channels']}, output channels: {dev['max_output_channels']})")


    # Initialize network thread first (without audio_engine)
    
    net_thread = NetworkThread(settings)  # create first
        
        
    # Now create audio engine with net_thread
    audio_engine = AudioEngine(settings, net_thread)
    net_thread.audio_engine = audio_engine

    try:
        logging.debug("Starting AudioEngine")
        audio_engine.start()
        logging.debug("AudioEngine started")
    except Exception as e:
        logging.debug(f"[Startup ERROR] AudioEngine failed to start: {e}")
        import traceback
        traceback.print_exc()

    # If NetworkThread needs audio_engine reference, assign it after both are created
    net_thread.audio_engine = audio_engine

    # Now start network thread
    try:
        logging.debug("Starting NetworkThread")
        net_thread.start()
        logging.debug("NetworkThread started")
    except Exception as e:
        logging.debug(f"[Startup ERROR] NetworkThread failed to start: {e}")
        import traceback
        traceback.print_exc()


    if args.loopback:
        audio_engine.set_loopback_enabled(True)

    # Create main GUI window
    window = MainWindow(settings, net_thread, audio_engine)

    # Assign the status callback after window is created
    audio_engine.status_callback = window.show_status

    # PTT Key filter
    #window.install_ptt_key_filter()  # Moved to a global call

    window.show()

    # Run Qt event loop
    exit_code = app.exec()

    # On app exit, ensure proper cleanup
    window.cleanup()
    audio_engine.stop()
    net_thread.stop()
    net_thread.wait()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

