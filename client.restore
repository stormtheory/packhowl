#!/usr/bin/env python3.12
"""
Silent Link – minimal PySide6 client shell with Opus audio encoding/decoding
• First-run wizard collects Display Name + Server IP
• System tray icon, hide-on-close behavior
• Background QThread handles TLS networking & auto-reconnect
• Real-time audio capture/playback using sounddevice and Opus codec for bandwidth efficiency
• Push-To-Talk and Voice Activation modes supported
"""

import json, sys, ssl, asyncio, traceback
from functools import partial
from typing import Optional
import threading

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
import numpy as np
import opuslib     # Opus codec import for encoding/decoding voice data
import sounddevice as sd
import samplerate 

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
# ─── Audio Handling (Encoder / Decoder / I/O) ──────────────────────────────
###############################################################################

class AudioEngine:
    """
    Handles audio capture and playback with Opus encoding/decoding.
    Designed for low latency, privacy-focused, efficient transmission.
    """
    SAMPLE_RATE = 48000          # Opus standard sample rate
    CHANNELS = 1                 # Mono audio for voice
    FRAME_DURATION_MS = 20       # 20 ms per frame for low latency
    FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # samples per frame

    def __init__(self, settings: Settings, net_thread: NetThread):
        self.settings = settings
        self.net_thread = net_thread

        # Create Opus encoder and decoder instances
        self.encoder = opuslib.Encoder(
            self.SAMPLE_RATE, self.CHANNELS, opuslib.APPLICATION_VOIP
        )
        self.decoder = opuslib.Decoder(
            self.SAMPLE_RATE, self.CHANNELS
        )

        # Internal flags for mute states and modes
        self.mic_muted = False
        self.mic_paused = False 
        self.spk_muted = False
        self.ptt_enabled = self.settings.get("ptt", False)
        self.vox_enabled = self.settings.get("vox", False)

        # Audio stream objects for input/output, initialized in start()
        self.stream_in = None
        self.stream_out = None

        # Buffer queue for incoming audio packets to play
        self.incoming_audio_queue = asyncio.Queue()

        # Start a dedicated thread for audio I/O to avoid blocking GUI
        self.audio_thread = threading.Thread(target=self._audio_io_loop, daemon=True)
        self.running = False

        # Voice activation detection threshold and state
        self.vox_threshold = 0.01   # Adjust this threshold for sensitivity
        self.vox_active = False

        # Lock for thread-safe state changes
        self.lock = threading.Lock()

    async def audio_sender_loop(self):
        """Continuously pull audio frames from send_queue and forward to network."""
        while self.running:
            try:
                packet = await self.send_queue.get()
                self.net_thread.queue_message(packet)
            except Exception as e:
                print(f"[Audio][ERR] Failed to send packet: {e}")


    # ── Helper: probe the first input-legal sample-rate that device accepts ──
    def _find_compatible_samplerate(self, dev_index: Optional[int], *, is_input: bool) -> int:
        """
        Try a list of common VoIP sample-rates until sounddevice says OK.
        Returns the first sample-rate that passes sd.check_*_settings().
        """
        candidate_rates = [48000, 44100, 32000, 16000, 8000]
        for rate in candidate_rates:
            try:
                if is_input:
                    sd.check_input_settings(device=dev_index, samplerate=rate)
                else:
                    sd.check_output_settings(device=dev_index, samplerate=rate)
                return rate
            except Exception:
                continue
        # As a last resort ask PortAudio for the device’s default rate
        info = sd.query_devices(dev_index, 'input' if is_input else 'output')
        return int(info['default_samplerate'])

    # ── Helper: find a device index by name or fall back to system default ──
    def _find_device_index(self, name: Optional[str], *, is_input: bool) -> Optional[int]:
        """
        Returns the PortAudio device index that matches `name`.
        If no name given or not found, returns None so sounddevice uses default.
        """
        if not name:
            return None
        for idx, dev in enumerate(sd.query_devices()):
            if dev['name'] == name and (
                (is_input  and dev['max_input_channels']  > 0) or
                (not is_input and dev['max_output_channels'] > 0)
            ):
                return idx
        return None  # fall back to default device

    # ── Start the audio subsystem: open streams & launch worker thread ──────
    def start(self):
        """
        Opens input/output streams at the closest-supported device rate.
        Resamples to 48 kHz for Opus if the device can’t do 48 kHz natively.
        """
        # ── Pick devices from saved settings (or defaults) ──────────────────
        in_name  = self.settings.get("input_device",  None)
        out_name = self.settings.get("output_device", None)
        in_idx   = self._find_device_index(in_name,  is_input=True)
        out_idx  = self._find_device_index(out_name, is_input=False)

        # ── Discover workable sample-rates ──────────────────────────────────
        self.dev_in_rate  = self._find_compatible_samplerate(in_idx,  is_input=True)
        self.dev_out_rate = self._find_compatible_samplerate(out_idx, is_input=False)
        print(f"[Audio] Using input rate {self.dev_in_rate} Hz, output rate {self.dev_out_rate} Hz")

        # Flag if we must resample mic frames → 48 kHz for Opus
        self.resample_input = (self.dev_in_rate != 48000)

        # ── (Re-)create Opus codec (always 48 kHz mono) ─────────────────────
        self.encoder = opuslib.Encoder(48000, self.CHANNELS, opuslib.APPLICATION_VOIP)
        self.decoder = opuslib.Decoder(48000, self.CHANNELS)

        # ── Compute frame sizes for each rate (20 ms default frame) ─────────
        self.dev_in_frames  = int(self.dev_in_rate  * self.FRAME_DURATION_MS / 1000)
        self.opus_frames    = int(48000            * self.FRAME_DURATION_MS / 1000)

        # ── Build PortAudio streams ─────────────────────────────────────────
        self.stream_in = sd.InputStream(
            samplerate=self.dev_in_rate,
            blocksize=self.dev_in_frames,
            channels=self.CHANNELS,
            dtype='int16',
            device=in_idx,
            callback=self._input_callback,
            latency='low'
        )
        self.stream_out = sd.OutputStream(
            samplerate=48000,                    # always feed 48 kHz into speaker path
            blocksize=self.opus_frames,
            channels=self.CHANNELS,
            dtype='int16',
            device=out_idx,
            callback=self._output_callback,
            latency='low'
        )

        # ── Kick everything off ─────────────────────────────────────────────
        self.stream_in.start()
        self.stream_out.start()
        self.running = True
        print("[Audio] Streams started successfully")

        # Worker thread keeps any asyncio queues alive for playback
        self.audio_thread = threading.Thread(target=self._audio_io_loop, daemon=True)
        self.audio_thread.start()

    def stop(self):
        """Stop audio processing and close streams."""
        self.running = False
        if self.stream_in:
            self.stream_in.stop()
            self.stream_in.close()
            self.stream_in = None
        if self.stream_out:
            self.stream_out.stop()
            self.stream_out.close()
            self.stream_out = None

    def _find_device_index(self, name: Optional[str], is_input: bool) -> Optional[int]:
        """Find the device index matching the saved device name, fallback to default."""
        if not name:
            return None  # Use default device
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev['name'] == name and ((is_input and dev['max_input_channels'] > 0) or (not is_input and dev['max_output_channels'] > 0)):
                return i
        return None  # fallback to default

    # ── Mic capture callback: encode + send ────────────────────────────────
    def _input_callback(self, indata, frames, time_info, status):
        """
        Called by sounddevice every `dev_in_frames` samples. Handles VOX/PTT,
        optional resampling to 48 kHz, then Opus-encodes and enqueues JSON.
        """
        if self.mic_muted:
            return  # Hard-mute

        try:
            pcm = indata[:, 0].copy()  # mono → flat int16 ndarray

            # ── Basic VOX (voice detection) ───────────────────────────────
            if self.vox_enabled:
                rms = np.sqrt(np.mean(pcm.astype(np.float32)**2))
                if rms < self.vox_threshold:
                    return  # below threshold, skip frame

            # ── Push-to-Talk gate (stub: always allowed unless you implement key state) ──
            if self.ptt_enabled and not self._is_ptt_pressed():
                return

            # ── Resample if device ≠ 48 kHz ───────────────────────────────
            if self.resample_input:
                resampled = samplerate.resample(pcm, 48000 / self.dev_in_rate, 'sinc_fastest')
                pcm_int16 = np.clip(resampled, -32768, 32767).astype(np.int16)
            else:
                pcm_int16 = pcm

            # ── Opus encode & ship ────────────────────────────────────────
            opus_bytes = self.encoder.encode(pcm_int16.tobytes(), self.opus_frames)
            self.net_thread.queue_message({
                "type": "audio",
                "data": opus_bytes.hex()  # hex for JSON safety
            })

        except Exception as e:
            print(f"[Audio][ERR] Input callback error: {e}")

    def log(self, *args):
        print("[Audio]", *args)

    def _output_callback(self, outdata, frames, time_info, status):
        """
        Called in sounddevice output thread context when audio playback buffer is needed.
        Dequeues Opus packets, decodes to PCM, and writes to output buffer.
        """
        # Non-blocking read from incoming queue
        try:
            opus_packet = self.incoming_audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            # No audio packet available, output silence
            outdata.fill(0)
            return

        try:
            # Decode hex string back to bytes
            opus_bytes = bytes.fromhex(opus_packet)
            # Decode to PCM int16 bytes
            pcm_bytes = self.decoder.decode(opus_bytes, self.FRAME_SIZE)
            # Convert PCM bytes to numpy int16 array for playback
            import numpy as np
            pcm_array = np.frombuffer(pcm_bytes, dtype='int16')
            outdata[:] = pcm_array.reshape((-1, self.CHANNELS))
        except Exception as e:
            logging.warning(f"Audio decoding error: {e}")
            outdata.fill(0)  # Output silence on error

    def queue_incoming_audio(self, opus_hex: str):
        """Called from GUI thread to queue incoming audio packets for playback."""
        # We must add this in the event loop thread safely
        try:
            self.incoming_audio_queue.put_nowait(opus_hex)
        except asyncio.QueueFull:
            pass  # Drop packets if queue is full to maintain low latency

    def _audio_io_loop(self):
        """
        Runs in separate thread to keep asyncio.Queue running properly with sounddevice threads.
        This thread can be extended to handle other audio processing as needed.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self.running:
            loop.run_until_complete(asyncio.sleep(0.1))
        loop.close()

    def _is_ptt_pressed(self) -> bool:
        """Return whether Push-To-Talk key is pressed.
        This function needs platform-specific implementation or integration with GUI input events.
        For now, we assume PTT is always pressed if enabled (for demo purposes).
        """
        # TODO: integrate with actual PTT key state detection (e.g., via pynput or Qt events)
        return True if self.ptt_enabled else False

    # Setters to update mute/mode flags from GUI controls:
    def set_mic_muted(self, muted: bool):
        with self.lock:
            self.mic_muted = muted

    def set_spk_muted(self, muted: bool):
        with self.lock:
            self.spk_muted = muted

    def set_ptt_enabled(self, enabled: bool):
        with self.lock:
            self.ptt_enabled = enabled

    def set_vox_enabled(self, enabled: bool):
        with self.lock:
            self.vox_enabled = enabled


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
        self.net.chatmsg.connect(self._handle_incoming_msg)
        self.net.start()

        # ── Audio engine setup ───────────────────────────────────────────
        self.audio_engine = AudioEngine(settings, self.net)
        self.audio_engine.start()

        # Restore mute states and connect buttons
        self.mic_muted = False
        self.mic_paused = False
        self.spk_muted = False
        self._update_mute_buttons()

        self.mute_mic_btn.clicked.connect(self._toggle_mic_mute)
        self.mute_spk_btn.clicked.connect(self._toggle_spk_mute)

        # ── Signals ──────────────────────────────────────────────────────
        send_btn.clicked.connect(self.send_chat)
        self.chat_edit.returnPressed.connect(send_btn.click)

        # ── Audio devices population and restoration ──────────────────────
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
        self.ptt_checkbox.toggled.connect(self._ptt_toggled)
        self.vox_checkbox.toggled.connect(self._vox_toggled)
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
        self.add_chat({"type": "chat", "name": self.settings["display_name"], "text": text})

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

    # Ensure cleanup on exit
    app.aboutToQuit.connect(win.cleanup)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
