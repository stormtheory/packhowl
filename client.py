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
from client.ptt import PTTManager
from client.gui import MainWindow
from client.first_run_settings import FirstRunDialog

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

def main():
    app = QtWidgets.QApplication(sys.argv)

    ###############################################################################
    # ─── ERROR CHECKING / LOAD JSON / FIRST TIME RUN ─────────────────────────────
    ###############################################################################
    ensure_data_dirs()
    settings = Settings()
    logging.debug(json.dumps(settings.data, indent=2))

    audio_engine = AudioEngine
    ptt_manager = PTTManager(settings, audio_engine)

    # First-run wizard
    if not settings["display_name"] or not settings["server_ip"]:
        dlg = FirstRunDialog(ptt_manager)
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

